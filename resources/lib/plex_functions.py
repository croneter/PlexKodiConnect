#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
from ast import literal_eval
from copy import deepcopy
from time import time
from threading import Thread

from .downloadutils import DownloadUtils as DU, exceptions
from . import backgroundthread, utils, plex_tv, variables as v, app

###############################################################################
LOG = getLogger('PLEX.plex_functions')

CONTAINERSIZE = int(utils.settings('limitindex'))

# For discovery of PMS in the local LAN
PLEX_GDM_IP = b'239.0.0.250'  # multicast to PMS
PLEX_GDM_PORT = 32414
PLEX_GDM_MSG = b'M-SEARCH * HTTP/1.0'

###############################################################################


def ConvertPlexToKodiTime(plexTime):
    """
    Converts Plextime to Koditime. Returns an int (in seconds).
    """
    if plexTime is None:
        return None
    return int(float(plexTime) * v.PLEX_TO_KODI_TIMEFACTOR)


def GetPlexKeyNumber(plexKey):
    """
    Deconstructs e.g. '/library/metadata/xxxx' to the tuple (unicode, int)

        ('library/metadata', xxxx)

    Returns (None, None) if nothing is found
    """
    try:
        result = utils.REGEX_END_DIGITS.findall(plexKey)[0]
    except IndexError:
        return (None, None)
    else:
        return (result[0], utils.cast(int, result[1]))


def ParseContainerKey(containerKey):
    """
    Parses e.g. /playQueues/3045?own=1&repeat=0&window=200 to:
    'playQueues', 3045, {'window': '200', 'own': '1', 'repeat': '0'}

    Output hence: library, key, query       (str, int, dict)
    """
    result = utils.urlparse(containerKey)
    library, key = GetPlexKeyNumber(result.path)
    query = dict(utils.parse_qsl(result.query))
    return library, key, query


def LiteralEval(string):
    """
    Turns a string e.g. in a dict, safely :-)
    """
    return literal_eval(string)


def GetMethodFromPlexType(plexType):
    methods = {
        'movie': 'add_update',
        'episode': 'add_updateEpisode',
        'show': 'add_update',
        'season': 'add_updateSeason',
        'track': 'add_updateSong',
        'album': 'add_updateAlbum',
        'artist': 'add_updateArtist'
    }
    return methods[plexType]


def GetPlexLoginFromSettings():
    """
    Returns a dict:
        'plexLogin': utils.settings('plexLogin'),
        'plexToken': utils.settings('plexToken'),
        'plexid': utils.settings('plexid'),
        'myplexlogin': utils.settings('myplexlogin'),
        'plexAvatar': utils.settings('plexAvatar'),

    Returns strings or unicode

    Returns empty strings '' for a setting if not found.

    myplexlogin is 'true' if user opted to log into plex.tv (the default)
    """
    return {
        'plexLogin': utils.settings('plexLogin'),
        'plexToken': utils.settings('plexToken'),
        'plexid': utils.settings('plexid'),
        'myplexlogin': utils.settings('myplexlogin'),
        'plexAvatar': utils.settings('plexAvatar'),
    }


def check_connection(url, token=None, verifySSL=None):
    """
    Checks connection to a Plex server, available at url. Can also be used
    to check for connection with plex.tv.

    Override SSL to skip the check by setting verifySSL=False
    if 'None', SSL will be checked (standard requests setting)
    if 'True', SSL settings from file settings are used (False/True)

    Input:
        url         URL to Plex server (e.g. https://192.168.1.1:32400)
        token       appropriate token to access server. If None is passed,
                    the current token is used
    Output:
        False       if server could not be reached or timeout occured
        200         if connection was successfull
        int         or other HTML status codes as received from the server
    """
    # Add '/clients' to URL because then an authentication is necessary
    # If a plex.tv URL was passed, this does not work.
    header_options = None
    if token is not None:
        header_options = {'X-Plex-Token': token}
    if 'plex.tv' in url:
        url = 'https://plex.tv/api/home/users'
    LOG.debug("Checking connection to server %s with verifySSL=%s",
              url, verifySSL)
    answer = DU().downloadUrl(url,
                              authenticate=False,
                              headerOptions=header_options,
                              verifySSL=verifySSL,
                              timeout=10)
    if answer is None:
        LOG.debug("Could not connect to %s", url)
        return False
    try:
        # xml received?
        answer.attrib
    except AttributeError:
        if answer is True:
            # Maybe no xml but connection was successful nevertheless
            answer = 200
    else:
        # Success - we downloaded an xml!
        answer = 200
    # We could connect but maybe were not authenticated. No worries
    LOG.debug("Checking connection successfull. Answer: %s", answer)
    return answer


def discover_pms(token=None):
    """
    Optional parameter:
        token       token for plex.tv - WARNING: for the main Plex user only!

    Returns a list of available PMS to connect to, one entry is the dict:
    {
        'machineIdentifier'     [str] unique identifier of the PMS
        'name'                  [str] name of the PMS
        'token'                 [str] token needed to access that PMS
        'ownername'             [str] name of the owner of this PMS or None if
                                the owner itself supplied tries to connect
        'product'               e.g. 'Plex Media Server' or None
        'version'               e.g. '1.11.2.4772-3e...' or None
        'device':               e.g. 'PC' or 'Windows' or None
        'platform':             e.g. 'Windows', 'Android' or None
        'local'                 [bool] True if plex.tv supplied
                                'publicAddressMatches'='1'
                                or if found using Plex GDM in the local LAN
        'owned'                 [bool] True if it's the owner's PMS
        'relay'                 [bool] True if plex.tv supplied 'relay'='1'
        'presence'              [bool] True if plex.tv supplied 'presence'='1'
        'httpsRequired'         [bool] True if plex.tv supplied
                                'httpsRequired'='1'
        'scheme'                [str] either 'http' or 'https'
        'ip':                   [str] IP of the PMS, e.g. '192.168.1.1'
        'port':                 [str] Port of the PMS, e.g. '32400'
        'baseURL':              [str] <scheme>://<ip>:<port> of the PMS
    }
    """
    LOG.info('Start discovery of Plex Media Servers')
    # Look first for local PMS in the LAN
    local_pms_list = _plex_gdm()
    LOG.debug('PMS found in the local LAN using Plex GDM: %s', local_pms_list)
    # Get PMS from plex.tv
    if token:
        LOG.info('Checking with plex.tv for more PMS to connect to')
        plex_pms_list = _pms_list_from_plex_tv(token)
        _log_pms(plex_pms_list)
    else:
        LOG.info('No plex token supplied, only checked LAN for available PMS')
        plex_pms_list = []

    # Add PMS found only in the LAN to the Plex.tv PMS list
    for pms in local_pms_list:
        for plex_pms in plex_pms_list:
            if pms['machineIdentifier'] == plex_pms['machineIdentifier']:
                break
        else:
            # Only found PMS using GDM. Check whether we can use baseURL
            # (which is in a different format) or need to connect directly
            if not _correct_baseurl(pms,
                                    '%s:%s' % (pms['baseURL'], pms['port'])):
                if not _correct_baseurl(pms,
                                        '%s:%s' % (pms['ip'], pms['port'])):
                    continue
            plex_pms_list.append(pms)
    _log_pms(plex_pms_list)
    return plex_pms_list


def _correct_baseurl(pms, url):
    https = _pms_https_enabled(url)
    if https is None:
        # Error contacting url
        return False
    elif https is True:
        pms['scheme'] = 'https'
    else:
        pms['scheme'] = 'http'
    pms['baseURL'] = '%s://%s' % (pms['scheme'], url)
    return True


def _log_pms(pms_list):
    log_list = deepcopy(pms_list)
    for pms in log_list:
        if pms.get('token') is not None:
            pms['token'] = '%s...' % pms['token'][:5]
    LOG.debug('Found the following PMS: %s', log_list)


def _plex_gdm():
    """
    PlexGDM - looks for PMS in the local LAN and returns a list of the PMS found
    """
    # Import here because we might not need to do gdm because we already
    # connected to a PMS successfully in the past
    import struct
    import socket

    # setup socket for discovery -> multicast message
    gdm = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gdm.settimeout(2.0)
    # Set the time-to-live for messages to 2 for local network
    ttl = struct.pack('b', 2)
    gdm.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

    return_data = []
    try:
        # Send data to the multicast group
        gdm.sendto(PLEX_GDM_MSG, (PLEX_GDM_IP, PLEX_GDM_PORT))

        # Look for responses from all recipients
        while True:
            try:
                data, server = gdm.recvfrom(1024)
                return_data.append({'from': server,
                                    'data': data.decode('utf-8')})
            except socket.timeout:
                break
            except Exception as error:
                # For some reason, above socket.timeout does not catch :-(
                LOG.debug('got an unexpected exception %s: %s',
                          type(error), error)
                break
    except Exception as error:
        # Probably error: (101, 'Network is unreachable')
        LOG.exception('Got a really unexpected exception %s: %s',
                      type(error), error)
    finally:
        gdm.close()
    LOG.debug('Plex GDM returned the data: %s', return_data)
    pms_list = []
    for response in return_data:
        # Check if we had a positive HTTP response
        if '200 OK' not in response['data']:
            continue
        pms = {
            'ip': response['from'][0],
            'scheme': None,
            'local': True,  # Since we found it using GDM
            'product': None,
            'baseURL': None,
            'name': None,
            'version': None,
            'token': None,
            'ownername': None,
            'device': None,
            'platform': None,
            'owned': None,
            'relay': None,
            'presence': True,  # Since we're talking to the PMS
            'httpsRequired': None,
        }
        for line in response['data'].split('\n'):
            if 'Content-Type:' in line:
                pms['product'] = line.split(':')[1].strip()
            elif 'Host:' in line:
                pms['baseURL'] = line.split(':')[1].strip()
            elif 'Name:' in line:
                pms['name'] = line.split(':')[1].strip()
            elif 'Port:' in line:
                pms['port'] = line.split(':')[1].strip()
            elif 'Resource-Identifier:' in line:
                pms['machineIdentifier'] = line.split(':')[1].strip()
            elif 'Version:' in line:
                pms['version'] = line.split(':')[1].strip()
        pms_list.append(pms)
    return pms_list


def _pms_list_from_plex_tv(token):
    """
    get Plex media Server List from plex.tv/pms/resources
    """
    xml = DU().downloadUrl('https://plex.tv/api/resources',
                           authenticate=False,
                           parameters={'includeHttps': 1},
                           headerOptions={'X-Plex-Token': token})
    try:
        xml.attrib
    except AttributeError:
        LOG.error('Could not get list of PMS from plex.tv')
        return []

    from queue import Queue
    queue = Queue()
    thread_queue = []

    max_age_in_seconds = 2 * 60 * 60 * 24
    for device in xml.findall('Device'):
        if 'server' not in device.get('provides'):
            # No PMS - skip
            continue
        if device.find('Connection') is None:
            # no valid connection - skip
            continue
        # check MyPlex data age - skip if >2 days
        info_age = time() - int(device.get('lastSeenAt'))
        if info_age > max_age_in_seconds:
            LOG.debug("Skip server %s not seen for 2 days", device.get('name'))
            continue
        pms = {
            'machineIdentifier': device.get('clientIdentifier'),
            'name': device.get('name'),
            'token': device.get('accessToken'),
            'ownername': device.get('sourceTitle'),
            'product': device.get('product'),  # e.g. 'Plex Media Server'
            'version': device.get('productVersion'),  # e.g. '1.11.2.4772-3e..'
            'device': device.get('device'),  # e.g. 'PC' or 'Windows'
            'platform': device.get('platform'),  # e.g. 'Windows', 'Android'
            'local': device.get('publicAddressMatches') == '1',
            'owned': device.get('owned') == '1',
            'relay': device.get('relay') == '1',
            'presence': device.get('presence') == '1',
            'httpsRequired': device.get('httpsRequired') == '1',
            'connections': []
        }
        # Try a local connection first, no matter what plex.tv tells us
        for connection in device.findall('Connection'):
            if connection.get('local') == '1':
                pms['connections'].append(connection)
        # Then try non-local
        for connection in device.findall('Connection'):
            if connection.get('local') != '1':
                pms['connections'].append(connection)
        # Spawn threads to ping each PMS simultaneously
        thread = Thread(target=_poke_pms, args=(pms, queue))
        thread_queue.append(thread)

    max_threads = 5
    threads = []
    # poke PMS, own thread for each PMS
    while True:
        # Remove finished threads
        for thread in threads:
            if not thread.is_alive():
                threads.remove(thread)
        if len(threads) < max_threads:
            try:
                thread = thread_queue.pop()
            except IndexError:
                # We have done our work
                break
            else:
                thread.start()
                threads.append(thread)
        else:
            app.APP.monitor.waitForAbort(0.05)
    # wait for requests being answered
    for thread in threads:
        thread.join()
    # declare new PMSs
    pms_list = []
    while not queue.empty():
        pms = queue.get()
        del pms['connections']
        pms_list.append(pms)
        queue.task_done()
    return pms_list


def _poke_pms(pms, queue):
    data = pms['connections'][0].attrib
    url = data['uri']
    if data['local'] == '1' and utils.REGEX_PLEX_DIRECT.findall(url):
        # In case DNS resolve of plex.direct does not work, append a new
        # connection that will directly access the local IP (e.g. internet down)
        conn = deepcopy(pms['connections'][0])
        # Overwrite plex.direct
        conn.attrib['uri'] = '%s://%s:%s' % (data['protocol'],
                                             data['address'],
                                             data['port'])
        pms['connections'].insert(1, conn)
    try:
        protocol, address, port = url.split(':', 2)
    except ValueError:
        # e.g. .ork.plex.services uri, thanks Plex
        protocol, address = url.split(':', 1)
        port = data['port']
        url = '%s:%s' % (url, port)
    address = address.replace('/', '')
    xml = DU().downloadUrl('%s/identity' % url,
                           authenticate=False,
                           headerOptions={'X-Plex-Token': pms['token']},
                           verifySSL=True,
                           timeout=(3.0, 5.0))
    try:
        xml.attrib['machineIdentifier']
    except (AttributeError, KeyError):
        # No connection, delete the one we just tested
        del pms['connections'][0]
        if pms['connections']:
            # Still got connections left, try them
            return _poke_pms(pms, queue)
        return
    else:
        # Connection successful - correct pms?
        if xml.get('machineIdentifier') == pms['machineIdentifier']:
            # process later
            pms['baseURL'] = url
            pms['scheme'] = protocol
            pms['ip'] = address
            pms['port'] = port
            queue.put(pms)
            return
    LOG.info('Found a pms at %s, but the expected machineIdentifier of '
             '%s did not match the one we found: %s',
             url, pms['uuid'], xml.get('machineIdentifier'))


def GetPlexMetadata(key, reraise=False):
    """
    Returns raw API metadata for key as an etree XML.

    Can be called with either Plex key '/library/metadata/xxxx'metadata
    OR with the digits 'xxxx' only.

    Returns None or 401 if something went wrong
    """
    key = str(key)
    if '/library/metadata/' in key:
        url = "{server}" + key
    else:
        url = "{server}/library/metadata/" + key
    arguments = {
        'checkFiles': 0,
        'includeExtras': 1,         # Trailers and Extras => Extras
        'includeReviews': 1,
        'includeRelated': 0,        # Similar movies => Video -> Related
        'skipRefresh': 1,
        'includeMarkers': 1,        # e.g. start + stop of intros
        # 'includeRelatedCount': 0,
        # 'includeOnDeck': 1,
        # 'includeChapters': 1,
        # 'includePopularLeaves': 1,
        # 'includeConcerts': 1
    }
    try:
        xml = DU().downloadUrl(utils.extend_url(url, arguments),
                               reraise=reraise)
    except exceptions.RequestException:
        # "PMS offline"
        utils.dialog('notification',
                     utils.lang(29999),
                     utils.lang(39213).format(app.CONN.server_name),
                     icon='{plex}')
    except Exception:
        # "Error"
        utils.dialog('notification',
                     utils.lang(29999),
                     utils.lang(30135),
                     icon='{error}')
    else:
        if xml == 401:
            # Either unauthorized (taken care of by doUtils) or PMS under strain
            return 401
        # Did we receive a valid XML?
        try:
            xml[0].attrib
        # Nope we did not receive a valid XML
        except (TypeError, IndexError, AttributeError):
            LOG.error("Error retrieving metadata for %s", url)
            xml = None
        return xml


def get_playback_xml(url, server_name, authenticate=True, token=None):
    """
    Returns None if something went wrong
    """
    header_options = {'includeMarkers': 1}
    if not authenticate:
        header_options['X-Plex-Token'] = token
    try:
        xml = DU().downloadUrl(url,
                               authenticate=authenticate,
                               headerOptions=header_options,
                               reraise=True)
    except exceptions.RequestException:
        # "{0} offline"
        utils.dialog('notification',
                     utils.lang(29999),
                     utils.lang(39213).format(server_name),
                     icon='{plex}')
    except Exception as e:
        LOG.error(e)
        import traceback
        LOG.error("Traceback:\n%s", traceback.format_exc())
        # "Play error"
        utils.dialog('notification',
                     utils.lang(29999),
                     utils.lang(30128),
                     icon='{error}')
    else:
        try:
            xml[0].attrib
        except (TypeError, IndexError, AttributeError):
            LOG.error('Could not get a valid xml, unfortunately')
            # "Play error"
            utils.dialog('notification',
                         utils.lang(29999),
                         utils.lang(30128),
                         icon='{error}')
        else:
            return xml


def GetAllPlexChildren(key):
    """
    Returns a list (raw xml API dump) of all Plex children for the key.
    (e.g. /library/metadata/194853/children pointing to a season)

    Input:
        key             Key to a Plex item, e.g. 12345
    """
    return DownloadChunks(utils.extend_url(
            f'{{server}}/library/metadata/{key}/children',
            {'includeElements': 'Stream'}))


class ThreadedDownloadChunk(backgroundthread.Task):
    """
    This task will also be executed while library sync is suspended!
    """
    def __init__(self, url, args, callback):
        self.url = url
        self.args = args
        self.callback = callback
        super(ThreadedDownloadChunk, self).__init__()

    def run(self):
        xml = DU().downloadUrl(self.url, parameters=self.args)
        try:
            xml.attrib
        except AttributeError:
            LOG.error('Error while downloading chunks: %s, args: %s',
                      self.url, self.args)
            xml = None
        self.callback(xml)


class DownloadGen(object):
    """
    Special iterator object that will yield all child xmls piece-wise. It also
    saves the original xml.attrib.

    Yields XML etree children or raises RuntimeError at the end
    """
    def __init__(self, url, plex_type, last_viewed_at, updated_at, args,
                 downloader):
        self._downloader = downloader
        self.successful = True
        self.xml = None
        self.args = args
        self.args.update({
            'X-Plex-Container-Start': 0,
            'X-Plex-Container-Size': CONTAINERSIZE
        })
        url += '?'
        if plex_type:
            url = '%stype=%s&' % (url, v.PLEX_TYPE_NUMBER_FROM_PLEX_TYPE[plex_type])
        if last_viewed_at:
            url = '%slastViewedAt>=%s&' % (url, last_viewed_at)
        if updated_at:
            url = '%supdatedAt>=%s&' % (url, updated_at)
        self.url = url[:-1]
        _blocking_download_chunk(self.url, self.args, 0, self.set_xml)
        self.attrib = self.xml.attrib
        self.current = 0
        self.total = int(self.attrib['totalSize'])
        self.cache_factor = 10
        # Will keep track whether we still have results incoming
        self.pending_counter = []
        end = min(self.cache_factor * CONTAINERSIZE,
                  self.total + CONTAINERSIZE - self.total % CONTAINERSIZE)
        for pos in range(CONTAINERSIZE, end, CONTAINERSIZE):
            self.pending_counter.append(None)
            self._downloader(self.url, self.args, pos, self.on_chunk_downloaded)

    def set_xml(self, xml):
        self.xml = xml

    def on_chunk_downloaded(self, xml):
        if xml is not None:
            self.xml.extend(xml)
        else:
            self.successful = False
        self.pending_counter.pop()

    def get(self, key, default=None):
        """
        Mimick etree xml's way to access xml.attrib via xml.get(key, default)
        """
        return self.attrib.get(key, default)

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                child = self.xml[0]
                self.current += 1
                self.xml.remove(child)
                if (self.current % CONTAINERSIZE == 0 and
                        self.current <= self.total - (self.cache_factor - 1) * CONTAINERSIZE):
                    self.pending_counter.append(None)
                    self._downloader(
                        self.url,
                        self.args,
                        self.current + (self.cache_factor - 1) * CONTAINERSIZE,
                        self.on_chunk_downloaded)
                return child
            except IndexError:
                if not self.pending_counter and not len(self.xml):
                    if not self.successful:
                        raise RuntimeError('Could not download everything')
                    else:
                        raise StopIteration()
            LOG.debug('Waiting for download to finish')
            if app.APP.monitor.waitForAbort(0.1):
                raise StopIteration('PKC needs to exit now')

    next = __next__


def _blocking_download_chunk(url, args, start, callback):
    """
    callback will be called with the downloaded xml (fragment)
    """
    args['X-Plex-Container-Start'] = start
    xml = DU().downloadUrl(url, parameters=args)
    try:
        xml.attrib
    except AttributeError:
        LOG.error('Error while downloading chunks: %s, args: %s',
                  url, args)
        raise RuntimeError('Error while downloading chunks for %s'
                           % url)
    callback(xml)


def _async_download_chunk(url, args, start, callback):
    args['X-Plex-Container-Start'] = start
    task = ThreadedDownloadChunk(url,
                                 deepcopy(args),  # Beware!
                                 callback)
    backgroundthread.BGThreader.addTask(task)


def get_section_iterator(section_id, plex_type=None, last_viewed_at=None,
                         updated_at=None, args=None):
    args = args or {}
    args.update({
        'checkFiles': 0,
        'includeExtras': 0,         # Trailers and Extras => Extras
        'includeReviews': 0,
        'includeRelated': 0,        # Similar movies => Video -> Related
        'skipRefresh': 1,  # don't scan
        'excludeAllLeaves': 1  # PMS wont attach a first summary child
    })
    if plex_type == v.PLEX_TYPE_ALBUM:
        # Kodi sorts Newest Albums by their position within the Kodi music
        # database - great...
        downloader = _blocking_download_chunk
        args['sort'] = 'addedAt:asc'
    else:
        downloader = _async_download_chunk
        args['sort'] = 'id'  # Entries are sorted by plex_id
    if plex_type in (v.PLEX_TYPE_EPISODE, v.PLEX_TYPE_SONG):
        # Annoying Plex bug. You won't get all episodes otherwise
        url = '{server}/library/sections/%s/allLeaves' % section_id
        plex_type = None
    else:
        url = '{server}/library/sections/%s/all' % section_id
    return DownloadGen(url,
                       plex_type,
                       last_viewed_at,
                       updated_at,
                       args,
                       downloader)


def DownloadChunks(url):
    """
    Downloads PMS url in chunks of CONTAINERSIZE.
    Returns a stitched-together xml or None.
    """
    xml = None
    pos = 0
    error_counter = 0
    while error_counter < 10:
        args = {
            'X-Plex-Container-Size': CONTAINERSIZE,
            'X-Plex-Container-Start': pos,
            'sort': 'id'
        }
        xmlpart = DU().downloadUrl(utils.extend_url(url, args))
        # If something went wrong - skip in the hope that it works next time
        try:
            xmlpart.attrib
        except AttributeError:
            LOG.error('Error while downloading chunks: %s, args: %s',
                      url, args)
            pos += CONTAINERSIZE
            error_counter += 1
            continue

        # Very first run: starting xml (to retain data in xml's root!)
        if xml is None:
            xml = deepcopy(xmlpart)
            if len(xmlpart) < CONTAINERSIZE:
                break
            else:
                pos += CONTAINERSIZE
                continue
        # Build answer xml - containing the entire library
        for child in xmlpart:
            xml.append(child)
        # Done as soon as we don't receive a full complement of items
        if len(xmlpart) < CONTAINERSIZE:
            break
        pos += CONTAINERSIZE
    if error_counter == 10:
        LOG.error('Fatal error while downloading chunks for %s', url)
        return None
    return xml


def GetPlexOnDeck(viewId):
    """
    """
    return DownloadChunks("{server}/library/sections/%s/onDeck" % viewId)


def get_plex_hub():
    return DU().downloadUrl('{server}/hubs')


def get_plex_sections():
    """
    Returns all Plex sections (libraries) of the PMS as an etree xml
    """
    xml = DU().downloadUrl('{server}/library/sections')
    try:
        xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        xml = None
    return xml


def init_plex_playqueue(plex_id, plex_type, section_uuid, trailers=False):
    """
    Returns raw API metadata XML dump for a playlist with e.g. trailers.
    """
    url = "{server}/playQueues"
    args = {
        'type': plex_type,
        'uri': ('server://%s/com.plexapp.plugins.library/library/metadata/%s' %
                (app.CONN.machine_identifier, plex_id)),
        'includeChapters': '1',
        'shuffle': '0',
        'repeat': '0',
        'includeMarkers': 1,        # e.g. start + stop of intros
    }
    if trailers is True:
        args['extrasPrefixCount'] = utils.settings('trailerNumber')
    xml = DU().downloadUrl(utils.extend_url(url, args), action_type="POST")
    try:
        xml[0].tag
    except (IndexError, TypeError, AttributeError):
        LOG.warn('Need to initialize Plex playqueue the old fashioned way')
        xml = init_plex_playqueue_old_fashioned(plex_id, section_uuid, url, args)
    return xml


def init_plex_playqueue_old_fashioned(plex_id, section_uuid, url, args):
    """
    In rare cases (old PMS version?), the PMS does not allow to add media using
    an uri
        server://<machineIdentifier>/com.plexapp.plugins.library/library...
    We need to use
        library://<librarySectionUUID>/item/...

    This involves an extra step to grab the librarySectionUUID for plex_id
    """
    args['uri'] = 'library://{0}/item/%2Flibrary%2Fmetadata%2F{1}'.format(
        section_uuid, plex_id)
    xml = DU().downloadUrl(utils.extend_url(url, args), action_type="POST")
    try:
        xml[0].tag
    except (IndexError, TypeError, AttributeError):
        LOG.error('Error initializing the playqueue the old fashioned way %s',
                  utils.extend_url(url, args))
        xml = None
    return xml


def _pms_https_enabled(url):
    """
    Returns True if the PMS can talk https, False otherwise.
    None if error occured, e.g. the connection timed out

    Call with e.g. url='192.168.0.1:32400' (NO http/https)

    This is done by GET /identity (returns an error if https is enabled and we
    are trying to use http)

    Prefers HTTPS over HTTP
    """
    # Try HTTPS first
    try:
        DU().downloadUrl('https://%s/identity' % url,
                         authenticate=False,
                         reraise=True)
    except exceptions.SSLError:
        LOG.debug('SSLError trying to connect to https://%s/identity', url)
    except Exception as e:
        LOG.info('Couldnt check https connection to https://%s/identity: %s',
                 url, e)
    else:
        return True

    # Try HTTP next
    try:
        DU().downloadUrl('http://%s/identity' % url,
                         authenticate=False,
                         reraise=True)
    except Exception as e:
        LOG.info('Couldnt check http connection to http://%s/identity: %s',
                 url, e)
        return
    else:
        return False


def GetMachineIdentifier(url):
    """
    Returns the unique PMS machine identifier of url

    Returns None if something went wrong
    """
    xml = DU().downloadUrl('%s/identity' % url,
                           authenticate=False,
                           verifySSL=True,
                           timeout=10,
                           reraise=True)
    try:
        machineIdentifier = xml.attrib['machineIdentifier']
    except (AttributeError, KeyError):
        LOG.error('Could not get the PMS machineIdentifier for %s', url)
        return None
    LOG.debug('Found machineIdentifier %s for the PMS %s',
              machineIdentifier, url)
    return machineIdentifier


def GetPMSStatus(token):
    """
    token:                  Needs to be authorized with a master Plex token
                            (not a managed user token)!
    Calls /status/sessions on currently active PMS. Returns a dict with:

    'sessionKey':
    {
        'userId':           Plex ID of the user (if applicable, otherwise '')
        'username':         Plex name (if applicable, otherwise '')
        'ratingKey':        Unique Plex id of item being played
    }

    or an empty dict.
    """
    answer = {}
    xml = DU().downloadUrl('{server}/status/sessions',
                           headerOptions={'X-Plex-Token': token})
    try:
        xml.attrib
    except AttributeError:
        return answer
    for item in xml:
        ratingKey = item.attrib.get('ratingKey')
        sessionKey = item.attrib.get('sessionKey')
        userId = item.find('User')
        username = ''
        if userId is not None:
            username = userId.attrib.get('title', '')
            userId = userId.attrib.get('id', '')
        else:
            userId = ''
        answer[sessionKey] = {
            'userId': userId,
            'username': username,
            'ratingKey': ratingKey
        }
    return answer


def collections(section_id):
    """
    Returns an etree with list of collections or None.
    """
    url = '{server}/library/sections/%s/all' % section_id
    params = {
        'type': 18,  # Collections
        'includeCollections': 1,
    }
    xml = DU().downloadUrl(url, parameters=params)
    try:
        xml.attrib
    except AttributeError:
        LOG.error("Error retrieving collections for %s", url)
        xml = None
    return xml


def scrobble(ratingKey, state):
    """
    Tells the PMS to set an item's watched state to state="watched" or
    state="unwatched"
    """
    args = {
        'key': ratingKey,
        'identifier': 'com.plexapp.plugins.library'
    }
    if state == "watched":
        url = '{server}/:/scrobble'
    elif state == "unwatched":
        url = '{server}/:/unscrobble'
    else:
        return
    DU().downloadUrl(utils.extend_url(url, args))
    LOG.info("Toggled watched state for Plex item %s", ratingKey)


def delete_item_from_pms(plexid):
    """
    Deletes the item plexid from the Plex Media Server (and the harddrive!).
    Do make sure that the currently logged in user has the credentials

    Returns True if successful, False otherwise
    """
    if DU().downloadUrl('{server}/library/metadata/%s' % plexid,
                        action_type="DELETE") is True:
        LOG.info('Successfully deleted Plex id %s from the PMS', plexid)
        return True
    LOG.error('Could not delete Plex id %s from the PMS', plexid)
    return False


def analyze_item_on_pms(plexid):
    """
    Triggers media analysis on the Plex Media Server for the given item.

    Returns True if the request was accepted, False otherwise
    """
    url = '{server}/library/metadata/%s/analyze' % plexid
    # Request async analysis to return immediately
    answer = DU().downloadUrl(url, parameters={'async': 1})
    if answer is None:
        LOG.error('Could not trigger analysis for Plex id %s on the PMS', plexid)
        return False
    LOG.info('Triggered analysis for Plex id %s on the PMS', plexid)
    return True


def pms_root(url, token):
    """
    Retrieve the PMS' most basic settings by retrieving <url>/
    """
    return DU().downloadUrl(
        url,
        authenticate=False,
        verifySSL=True,
        headerOptions={'X-Plex-Token': token} if token else None)


def get_PMS_settings(url, token):
    """
    Retrieve the PMS' settings via <url>/:/prefs

    Call with url: scheme://ip:port
    """
    return DU().downloadUrl(
        '%s/:/prefs' % url,
        authenticate=False,
        verifySSL=True,
        headerOptions={'X-Plex-Token': token} if token else None)


def GetUserArtworkURL(username):
    """
    Returns the URL for the user's Avatar. Or False if something went
    wrong.
    """
    users = plex_tv.plex_home_users(utils.settings('plexToken'))
    url = ''
    for user in users:
        if user.title == username:
            url = user.thumb
    LOG.debug("Avatar url for user %s is: %s", username, url)
    return url


def show_episodes(plex_id):
    """
    Returns all episodes for the tv show with plex_id
    """
    url = "{server}/library/metadata/%s/allLeaves" % plex_id
    arguments = {
        'checkFiles': 0,
        'skipRefresh': 1,
    }
    return DownloadChunks(utils.extend_url(url, arguments))


def transcoding_arguments(path, media, part, playmethod, args=None):
    if playmethod == v.PLAYBACK_METHOD_DIRECT_PLAY:
        direct_play = 1
        direct_stream = 1
    elif playmethod == v.PLAYBACK_METHOD_DIRECT_STREAM:
        direct_play = 0
        direct_stream = 1
    elif playmethod == v.PLAYBACK_METHOD_TRANSCODE:
        direct_play = 0
        direct_stream = 0
    arguments = {
        # e.g. '/library/metadata/831399'
        'path': path,
        # 1 if you want to directPlay, 0 if you want to transcode
        'directPlay': direct_play,
        # 1 if you want to play a stream copy of data into a new container. This
        # is unlikely to come up but it’s possible if you are playing something
        # with a lot of tracks, a direct stream can result in lower bandwidth
        # when a direct play would be over the limit.
        # Assume Kodi can always handle any stream thrown at it!
        'directStream': direct_stream,
        # Same for audio - assume Kodi can play any audio stream passed in!
        'directStreamAudio': direct_stream,
        # This tells the server that you definitively know that the client can
        # direct play (when you have directPlay=1) the content in spite of what
        # client profiles may say about what the client can play. When this is
        # set and directPlay=1, the server just checks bandwidth restrictions
        # and gives you a reservation if bandwidth restrictions are met
        'hasMDE': direct_play,
        # where # is an integer, 0 indexed. If you specify directPlay, this is
        # required. -1 indicates let the server choose.
        'mediaIndex': media,
        # Similar to mediaIndex but indicates which part you want to direct
        # play. Really only comes into play with multi-part files which are
        # uncommon. -1 here means concatenate the parts together but that
        # requires the transcoder.
        'partIndex': part,
        # all the rest
        'audioBoost': utils.settings('audioBoost'),
        'autoAdjustQuality': 1 if utils.settings('auto_adjust_transcode_quality') == 'true' else 0,
        'protocol': 'hls',   # seen in the wild: 'http', 'dash', 'http', 'hls'
        'session': v.PKC_MACHINE_IDENTIFIER,  # TODO: create new unique id
        'fastSeek': 1,
        'copyts': 1
    }
    if playmethod != v.PLAYBACK_METHOD_TRANSCODE:
        # Essentially indicating what you want to do with subtitles and state
        # you aren’t want it to burn them into the video (requires transcoding)
        # none, embedded, sidecar
        args['subtitles'] = 'none'
    else:
        args['subtitleSize'] = utils.settings('subtitleSize')
    if args:
        arguments.update(args)
    return arguments


def playback_decision(path, media, part, playmethod, video=True, args=None):
    """
    Let's the PMS decide how we should playback this file
    """
    arguments = transcoding_arguments(path, media, part, playmethod, args=args)
    if video:
        url = '{server}/video/:/transcode/universal/decision'
    else:
        url = '{server}/music/:/transcode/universal/decision'
    LOG.debug('Asking the PMS if we can play this video with settings: %s',
              arguments)
    return DU().downloadUrl(utils.extend_url(url, arguments),
                            headerOptions=v.STREAMING_HEADERS,
                            reraise=True)


def change_subtitle(plex_stream_id, part_id):
    """
    Tell the PMS to display/burn-in the subtitle stream with id plex_stream_id
    for the Part (PMS XML etree tag "Part") with unique id part_id.
    - plex_stream_id = 0 will deactivate the subtitle
    - We always do this for ALL parts of a video
    """
    arguments = {
        'subtitleStreamID': plex_stream_id,
        'allParts': 1
    }
    url = '{server}/library/parts/%s' % part_id
    return DU().downloadUrl(utils.extend_url(url, arguments),
                            action_type='PUT')


def change_audio_stream(plex_stream_id, part_id):
    """
    Tell the PMS to display/burn-in the subtitle stream with id plex_stream_id
    for the Part (PMS XML etree tag "Part") with unique id part_id.
    - plex_stream_id = 0 will deactivate the subtitle
    - We always do this for ALL parts of a video
    """
    arguments = {
        'audioStreamID': plex_stream_id,
        'allParts': 1
    }
    url = '{server}/library/parts/%s' % part_id
    return DU().downloadUrl(utils.extend_url(url, arguments),
                            action_type='PUT')


def change_video_stream(plex_stream_id, part_id):
    """
    Tell the PMS to display another video stream
    - We always do this for ALL parts of a video
    """
    arguments = {
        'videoStreamID': plex_stream_id,
        'allParts': 1
    }
    url = '{server}/library/parts/%s' % part_id
    return DU().downloadUrl(utils.extend_url(url, arguments),
                            action_type='PUT')
