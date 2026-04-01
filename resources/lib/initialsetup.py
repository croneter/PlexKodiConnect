#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger

from xbmc import executebuiltin

from . import utils
import xml.etree.ElementTree as etree
from . import path_ops
from . import migration
from .downloadutils import DownloadUtils as DU, exceptions
from . import plex_functions as PF
from . import plex_tv
from . import json_rpc as js
from . import app
from . import variables as v

###############################################################################

LOG = getLogger('PLEX.initialsetup')

###############################################################################

if not path_ops.exists(v.EXTERNAL_SUBTITLE_TEMP_PATH):
    path_ops.makedirs(v.EXTERNAL_SUBTITLE_TEMP_PATH)


class InitialSetup(object):
    """
    Will load Plex PMS settings (e.g. address) and token
    Will ask the user initial questions on first PKC boot
    """
    def __init__(self):
        LOG.debug('Entering initialsetup class')
        # Get Plex credentials from settings file, if they exist
        plexdict = PF.GetPlexLoginFromSettings()
        self.plex_login = plexdict['plexLogin']
        self.plex_login_id = plexdict['plexid']
        self.plex_token = plexdict['plexToken']
        # Token for the PMS, not plex.tv
        self.pms_token = utils.settings('accessToken')
        if self.plex_token:
            LOG.debug('Found a plex.tv token in the settings')

    def write_credentials_to_settings(self):
        """
        Writes Plex username, token to plex.tv and Plex id to PKC settings
        """
        utils.settings('username', value=self.plex_login or '')
        utils.settings('userid', value=self.plex_login_id or '')
        utils.settings('plexToken', value=self.plex_token or '')

    @staticmethod
    def save_pms_settings(url, token):
        """
        Sets certain settings for server by asking for the PMS' settings
        Call with url: scheme://ip:port
        """
        xml = PF.get_PMS_settings(url, token)
        try:
            xml.attrib
        except AttributeError:
            LOG.error('Could not get PMS settings for %s', url)
            return
        for entry in xml:
            typus = entry.get('id')
            if typus == 'allowMediaDeletion':
                value = 'true' if entry.get('value', '1') == '1' else 'false'
                utils.settings('plex_allows_mediaDeletion', value=value)
                utils.window('plex_allows_mediaDeletion', value=value)
                LOG.info('Using PMS setting allowMediaDeletion=%s',
                         value)
            elif typus == 'LibraryVideoPlayedThreshold':
                # Set the progress percentage for video playback at which point
                # the video will be marked as played
                value = int(entry.get('value'))
                LOG.info('Using PMS setting LibraryVideoPlayedThreshold=%s',
                         value)
                v.MARK_PLAYED_AT = value / 100.0
            elif typus == 'LibraryVideoPlayedAtBehaviour':
                # Decide whether to use end credits markers to determine
                # the 'played' state of video items. When markers are not
                # available the selected threshold percentage will be used.
                # enumValues=
                # 0:at selected threshold percentage
                # 1:at final credits marker position
                # 2:at first credits marker position
                # 3:earliest between threshold percent and first credits marker
                value = int(entry.get('value'))
                LOG.info('Using PMS setting LibraryVideoPlayedAtBehaviour=%s',
                         value)
                v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = value

    @staticmethod
    def enter_new_pms_address():
        LOG.info('Start getting manual PMS address and port')
        # "Enter your Plex Media Server's IP or URL. Examples are:"
        utils.messageDialog(utils.lang(29999),
                            '%s\n%s\n%s' % (utils.lang(39215),
                                            '192.168.1.2',
                                            'plex.myServer.org'))
        # "Enter PMS IP or URL"
        address = utils.dialog('input', utils.lang(39083))
        if not address:
            return False
        port = utils.dialog('input', utils.lang(39084), '32400', type='{numeric}')
        if not port:
            return False
        url = '%s:%s' % (address, port)
        # "Use HTTPS (SSL) connections? Answer should probably be yes."
        https = utils.yesno_dialog(utils.lang(29999), utils.lang(39217))
        if https:
            url = 'https://%s' % url
        else:
            url = 'http://%s' % url
        https = 'true' if https else 'false'
        # Try to connect first
        error = False
        try:
            machine_identifier = PF.GetMachineIdentifier(url)
        except exceptions.SSLError:
            LOG.error('SSL cert error contacting %s', url)
            # "SSL certificate failed to validate. Please check {0}
            # for solutions."
            utils.messageDialog(utils.lang(29999),
                                utils.lang(30503).format('github.com/croneter/PlexKodiConnect/issues'))
            return
        except Exception:
            error = True
        if error or machine_identifier is None:
            LOG.error('Could not even get a machineIdentifier for %s', url)
            # "Server is unreachable"
            utils.messageDialog(utils.lang(29999), utils.lang(33002))
            return
        # Let's use the main account's token, not managed user token
        token = utils.settings('plexToken')
        xml = PF.pms_root(url, token)
        if xml == 401:
            LOG.error('Not yet authorized for %s', url)
            # "User is unauthorized for server {0}",
            # "Please sign in to plex.tv."
            utils.messageDialog(utils.lang(29999),
                                '%s. %s' % (utils.lang(33010).format(address),
                                            utils.lang(39014)))
            return
        try:
            xml[0].attrib
        except (IndexError, TypeError, AttributeError):
            LOG.error('Could not get PMS root directory for %s', url)
            # "Error contacting PMS"
            utils.messageDialog(utils.lang(29999), utils.lang(39218))
            return
        pms = {
            'baseURL': url,
            'ip': address,
            # Assume PMS is not local so we're not resetting verifyssl
            'local': False,
            'machineIdentifier': xml.get('machineIdentifier'),
            'name': xml.get('friendlyName'),
            # Assume that we own this PMS - no easy way to check
            'owned': True,
            'platform': xml.get('platform'),
            'port': port,
            # 'relay': True,
            'scheme': 'https' if https else 'http',
            'token': token,
            'version': xml.get('version')
        }
        return pms

    def plex_tv_sign_in(self):
        """
        Signs (freshly) in to plex.tv (will be saved to file settings)

        Returns True if successful, or False if not
        """
        user = plex_tv.sign_in_with_pin()
        if user:
            self.plex_login = user.username
            self.plex_token = user.authToken
            self.plex_login_id = user.id
            return True
        return False

    def check_plex_tv_sign_in(self):
        """
        Checks existing connection to plex.tv. If not, triggers sign in

        Returns True if signed in, False otherwise
        """
        answer = True
        chk = PF.check_connection('plex.tv', token=self.plex_token)
        if chk in (401, 403):
            # HTTP Error: unauthorized. Token is no longer valid
            LOG.info('plex.tv connection returned HTTP %s', str(chk))
            # Delete token in the settings
            utils.settings('plexToken', value='')
            utils.settings('plexLogin', value='')
            # Could not login, please try again
            utils.messageDialog(utils.lang(29999), utils.lang(39009))
            answer = self.plex_tv_sign_in()
        elif chk is False or chk >= 400:
            # Problems connecting to plex.tv. Network or internet issue?
            LOG.info('Problems connecting to plex.tv; connection returned '
                     'HTTP %s', str(chk))
            utils.messageDialog(utils.lang(29999), utils.lang(39010))
            answer = False
        else:
            LOG.info('plex.tv connection with token successful')
            utils.settings('plex_status', value=utils.lang(39227))
            # Refresh the info from Plex.tv
            xml = DU().downloadUrl('https://plex.tv/users/account',
                                   authenticate=False,
                                   headerOptions={'X-Plex-Token': self.plex_token})
            try:
                self.plex_login = xml.attrib['title']
            except (AttributeError, KeyError):
                LOG.error('Failed to update Plex info from plex.tv')
            else:
                utils.settings('plexLogin', value=self.plex_login)
                utils.settings('plexAvatar', value=xml.attrib.get('thumb'))
                LOG.info('Updated Plex info from plex.tv')
        return answer

    @staticmethod
    def check_existing_pms():
        """
        Check the PMS that was set in file settings.
        Will return False if we need to reconnect, because:
            PMS could not be reached (no matter the authorization)
            machineIdentifier did not match

        Will also set the PMS machineIdentifier in the file settings if it was
        not set before
        """
        answer = True
        chk = PF.check_connection(app.CONN.server, verifySSL=True)
        if chk is False:
            LOG.warn('Could not reach PMS %s', app.CONN.server)
            answer = False
        if answer is True and not app.CONN.machine_identifier:
            LOG.info('No PMS machineIdentifier found for %s. Trying to '
                     'get the PMS unique ID', app.CONN.server)
            app.CONN.machine_identifier = PF.GetMachineIdentifier(app.CONN.server)
            if app.CONN.machine_identifier is None:
                LOG.warn('Could not retrieve machineIdentifier')
                answer = False
            else:
                utils.settings('plex_machineIdentifier', value=app.CONN.machine_identifier)
        elif answer is True:
            temp_server_id = PF.GetMachineIdentifier(app.CONN.server)
            if temp_server_id != app.CONN.machine_identifier:
                LOG.warn('The current PMS %s was expected to have a '
                         'unique machineIdentifier of %s. But we got '
                         '%s. Pick a new server to be sure',
                         app.CONN.server, app.CONN.machine_identifier, temp_server_id)
                answer = False
        return answer

    @staticmethod
    def _check_pms_connectivity(server):
        """
        Checks for server's connectivity. Returns check_connection result
        """
        if not server['token']:
            # Plex GDM: we only get the token from plex.tv after
            # Sign-in to plex.tv
            server['token'] = utils.settings('plexToken') or None
        return PF.check_connection(server['baseURL'],
                                   token=server['token'],
                                   verifySSL=True)

    def pick_pms(self, showDialog=False, inform_of_search=False):
        """
        Searches for PMS in local Lan and optionally (if self.plex_token set)
        also on plex.tv
            showDialog=True: let the user pick one
            showDialog=False: automatically pick PMS based on machineIdentifier

        Returns the picked PMS' detail as a dict:
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
        or None if unsuccessful
        """
        # If no server is set, let user choose one
        if not app.CONN.server or not app.CONN.machine_identifier:
            showDialog = True
        if showDialog is True:
            server = self._user_pick_pms()
        else:
            server = self._auto_pick_pms(show_dialog=inform_of_search)
        return server

    def _auto_pick_pms(self, show_dialog=False):
        """
        Will try to pick PMS based on machineIdentifier saved in file settings
        but only once

        Returns server or None if unsuccessful
        """
        https_updated = False
        server = None
        if show_dialog:
            # Searching for PMS
            utils.dialog('notification',
                         heading='{plex}',
                         message=utils.lang(30001),
                         icon='{plex}',
                         time=60000)
        try:
            while True:
                if https_updated is False:
                    serverlist = PF.discover_pms(self.plex_token)
                    for item in serverlist:
                        if item.get('machineIdentifier') == app.CONN.machine_identifier:
                            server = item
                    if server is None:
                        name = utils.settings('plex_servername')
                        LOG.warn('The PMS you have used before with a unique '
                                 'machineIdentifier of %s and name %s is '
                                 'offline', app.CONN.machine_identifier, name)
                        return
                chk = self._check_pms_connectivity(server)
                if chk == 504 and https_updated is False:
                    # switch HTTPS to HTTP or vice-versa
                    if server['scheme'] == 'https':
                        server['scheme'] = 'http'
                    else:
                        server['scheme'] = 'https'
                    https_updated = True
                    continue
                # Problems connecting
                elif chk >= 400 or chk is False:
                    LOG.warn('Problems connecting to server %s. chk is %s',
                             server['name'], chk)
                    return
                LOG.info('We found a server to automatically connect to: %s',
                         server['name'])
                return server
        finally:
            if show_dialog:
                executebuiltin("Dialog.Close(all, true)")


    def _user_pick_pms(self):
        """
        Lets user pick his/her PMS from a list

        Returns server or None if unsuccessful
        """
        https_updated = False
        # Searching for PMS
        utils.dialog('notification',
                     heading='{plex}',
                     message=utils.lang(30001),
                     icon='{plex}',
                     time=60000)
        while True:
            if https_updated is False:
                serverlist = PF.discover_pms(self.plex_token)
                # Exit if no servers found
                if not serverlist:
                    LOG.warn('No plex media servers found!')
                    utils.messageDialog(utils.lang(29999), utils.lang(39011))
                    return
                # Get a nicer list
                dialoglist = []
                for server in serverlist:
                    if server['local']:
                        # server is in the same network as client.
                        # Add"local"
                        msg = utils.lang(39022)
                    else:
                        # Add 'remote'
                        msg = utils.lang(39054)
                    if server.get('ownername'):
                        # Display username if its not our PMS
                        dialoglist.append('%s (%s, %s)'
                                          % (server['name'],
                                             server['ownername'],
                                             msg))
                    else:
                        dialoglist.append('%s (%s)'
                                          % (server['name'], msg))
                # Let user pick server from a list
                # Close the PKC info "Searching for PMS"
                executebuiltin("Dialog.Close(all, true)")
                resp = utils.dialog('select', utils.lang(39012), dialoglist)
                if resp == -1:
                    # User cancelled
                    return

            server = serverlist[resp]
            chk = self._check_pms_connectivity(server)
            if chk == 504 and https_updated is False:
                # Not able to use HTTP, try HTTPs for now
                serverlist[resp]['scheme'] = 'https'
                https_updated = True
                continue
            https_updated = False
            if chk == 401:
                LOG.warn('Not yet authorized for Plex server %s',
                         server['name'])
                # Not yet authorized for Plex server %s
                utils.messageDialog(
                    utils.lang(29999),
                    '%s %s\n%s' % (utils.lang(39013),
                                   server['name'],
                                   utils.lang(39014)))
                if self.plex_tv_sign_in() is False:
                    # Exit while loop if user cancels
                    return
            # Problems connecting
            elif chk >= 400 or chk is False:
                # Problems connecting to server. Pick another server?
                if not utils.yesno_dialog(utils.lang(29999), utils.lang(39015)):
                    # Exit while loop if user chooses No
                    return
            # Otherwise: connection worked!
            else:
                return server

    @staticmethod
    def write_pms_to_settings(server):
        """
        Saves server to file settings
        """
        utils.settings('plex_machineIdentifier', server['machineIdentifier'])
        utils.settings('plex_servername', server['name'])
        utils.settings('plex_serverowned',
                       'true' if server['owned'] else 'false')
        # Careful to distinguish local from remote PMS
        if server['local']:
            scheme = server['scheme']
            utils.settings('ipaddress', server['ip'])
            utils.settings('port', server['port'])
            LOG.debug("Setting SSL verify to false, because server is "
                      "local")
            utils.settings('sslverify', 'false')
        else:
            baseURL = server['baseURL'].split(':')
            scheme = baseURL[0]
            utils.settings('ipaddress', baseURL[1].replace('//', ''))
            utils.settings('port', baseURL[2])
            LOG.debug("Setting SSL verify to true, because server is not "
                      "local")
            utils.settings('sslverify', 'true')

        if scheme == 'https':
            utils.settings('https', 'true')
        else:
            utils.settings('https', 'false')
        # And finally do some logging
        LOG.debug("Writing to Kodi user settings file")
        LOG.debug("PMS machineIdentifier: %s, ip: %s, port: %s, https: %s ",
                  server['machineIdentifier'], server['ip'], server['port'],
                  server['scheme'])

    @staticmethod
    def _add_sources(root, extension):
        changed = False
        count = 2
        for source in root.findall('.//path'):
            if source.text == extension:
                count -= 1
            if count == 0:
                # sources already set
                break
        else:
            # Missing smb:// occurences, re-add.
            changed = True
            for _ in range(0, count):
                source = etree.SubElement(root, 'source')
                etree.SubElement(
                    source,
                    'name').text = "PlexKodiConnect Masterlock Hack"
                etree.SubElement(
                    source,
                    'path',
                    {'pathversion': "1"}).text = extension
                etree.SubElement(source, 'allowsharing').text = "true"
        return changed

    def setup(self):
        """
        Initial setup. Run once upon startup.

        Check server, user, direct paths, music, direct stream if not direct
        path.
        """
        LOG.info("Initial setup called.")
        try:
            with utils.XmlKodiSetting('advancedsettings.xml',
                                      force_create=True,
                                      top_element='advancedsettings') as xml:
                # Get current Kodi video cache setting
                cache = xml.get_setting(['cache', 'memorysize'])
                if utils.settings('enableMusic') == 'true':
                    # Disable foreground "Loading media information from files"
                    # (still used by Kodi, even though the Wiki says otherwise)
                    xml.set_setting(['musiclibrary', 'backgroundupdate'],
                                    value='true')
                cleanonupdate = xml.get_setting(
                    ['videolibrary', 'cleanonupdate']) == 'true'
                if utils.settings('useDirectPaths') != '1':
                    # Disable cleaning of library - not compatible with PKC
                    # Only do this for add-on paths
                    xml.set_setting(['videolibrary', 'cleanonupdate'],
                                    value='false')
                # Get settings for in-progress / marked watched
                if xml.get_setting(['video',
                                   'ignorepercentatend']) is not None:
                    v.KODI_IGNOREPERCENTATEND = float(
                        xml.get_setting(['video', 'ignorepercentatend']).text) / 100.0
                if xml.get_setting(['video',
                                   'playcountminimumpercent']) is not None:
                    v.KODI_PLAYCOUNTMINIMUMPERCENT = float(
                        xml.get_setting(['video', 'playcountminimumpercent']).text) / 100.0
                if xml.get_setting(['video',
                                   'ignoresecondsatstart']) is not None:
                    v.KODI_IGNORESECONDSATSTART = int(
                        xml.get_setting(['video', 'ignoresecondsatstart']).text)
                reboot = xml.write_xml
        except utils.ParseError:
            cache = None
            reboot = False
            cleanonupdate = False
        # Kodi default cache if no setting is set
        cache = str(cache.text) if cache is not None else '20971520'
        LOG.info('Current Kodi video memory cache in bytes: %s', cache)
        utils.settings('kodi_video_cache', value=cache)

        # Hack to make PKC Kodi master lock compatible
        try:
            with utils.XmlKodiSetting('sources.xml',
                                      force_create=True,
                                      top_element='sources') as xml:
                changed = False
                for extension in ('smb://', 'nfs://', 'plugin://'):
                    root = xml.set_setting(['video'])
                    changed = self._add_sources(root, extension) or changed
                if changed:
                    xml.write_xml = True
                    reboot = True
        except utils.ParseError:
            pass

        # Do we need to migrate stuff?
        migration.check_migration()
        # Reload the server IP cause we might've deleted it during migration
        app.CONN.load()

        # Display a warning if Kodi puts ALL movies into the queue, basically
        # breaking playback reporting for PKC
        settings = js.settings_getsettingvalue('videoplayer.autoplaynextitem')
        # Answer for videoplayer.autoplaynextitem:
        # [{u'label': u'Music videos', u'value': 0},
        #  {u'label': u'TV shows', u'value': 1},
        #  {u'label': u'Episodes', u'value': 2},
        #  {u'label': u'Movies', u'value': 3},
        #  {u'label': u'Uncategorized', u'value': 4}]
        if 1 in settings or 2 in settings or 3 in settings:
            LOG.warn('Kodi setting videoplayer.autoplaynextitem is: %s',
                     settings)
            if utils.settings('warned_setting_videoplayer.autoplaynextitem') == 'false':
                # Only warn once
                utils.settings('warned_setting_videoplayer.autoplaynextitem',
                               value='true')
                # Warning: Kodi setting "Play next video automatically" is
                # enabled. This could break PKC. Deactivate?
                if utils.yesno_dialog(utils.lang(29999), utils.lang(30003)):
                    for i in (1, 2, 3):
                        try:
                            settings.remove(i)
                        except ValueError:
                            pass
                    js.settings_setsettingvalue('videoplayer.autoplaynextitem',
                                                settings)
        # Set any video library updates to happen in the background in order to
        # hide "Compressing database"
        js.settings_setsettingvalue('videolibrary.backgroundupdate', True)

        # If a Plex server IP has already been set
        # return only if the right machine identifier is found
        if app.CONN.server:
            LOG.info("PMS is already set: %s. Checking now...", app.CONN.server)
            if self.check_existing_pms():
                LOG.info("Using PMS %s with machineIdentifier %s",
                         app.CONN.server, app.CONN.machine_identifier)
                self.save_pms_settings(app.CONN.server, self.pms_token)
                if utils.settings('kodi_db_has_been_wiped_clean') == 'false':
                    # If the user chose to go to the PKC settings on the first run
                    # Will trigger a reboot
                    utils.wipe_database()
                if reboot is True:
                    utils.reboot_kodi()
                return
        else:
            LOG.info('No PMS set yet')

        # If not already retrieved myplex info, optionally let user sign in
        # to plex.tv. This DOES get called on very first install run
        if not self.plex_token and app.ACCOUNT.myplexlogin:
            self.plex_tv_sign_in()

        server = self.pick_pms(inform_of_search=True)
        if server is not None:
            # Write our chosen server to Kodi settings file
            self.save_pms_settings(server['baseURL'], server['token'])
            self.write_pms_to_settings(server)

        # User already answered the installation questions
        if utils.settings('InstallQuestionsAnswered') == 'true':
            LOG.info('Installation questions already answered')
            if utils.settings('kodi_db_has_been_wiped_clean') == 'false':
                # If the user chose to go to the PKC settings on the first run
                # Will trigger a reboot
                utils.wipe_database()
            if reboot is True:
                utils.reboot_kodi()
            # Reload relevant settings
            app.CONN.load()
            app.ACCOUNT.load()
            app.SYNC.load()
            return

        LOG.info('Showing install questions')
        # Additional settings where the user needs to choose
        # Direct paths (\\NAS\mymovie.mkv) or addon (http)?
        goto_settings = False
        from .windows import optionsdialog
        # Use Add-on Paths (default, easy) or Direct Paths? PKC will not work
        # if your Direct Paths setup is wrong!
        # Buttons: Add-on Paths // Direct Paths
        if optionsdialog.show(utils.lang(29999), utils.lang(39080),
                              utils.lang(39081), utils.lang(39082)) == 1:
            LOG.debug("User opted to use direct paths.")
            utils.settings('useDirectPaths', value="1")
            if cleanonupdate:
                # Re-enable cleanonupdate
                with utils.XmlKodiSetting('advancedsettings.xml') as xml:
                    xml.set_setting(['videolibrary', 'cleanonupdate'],
                                    value='true')
            # Are you on a system where you would like to replace paths
            # \\NAS\mymovie.mkv with smb://NAS/mymovie.mkv? (e.g. Windows)
            if utils.yesno_dialog(utils.lang(29999), utils.lang(39033)):
                LOG.debug("User chose to replace paths with smb")
            else:
                utils.settings('replaceSMB', value="false")

            # complete replace all original Plex library paths with custom SMB
            if utils.yesno_dialog(utils.lang(29999), utils.lang(39043)):
                LOG.debug("User chose custom smb paths")
                utils.settings('remapSMB', value="true")
                # Please enter your custom smb paths in the settings under
                # "Sync Options" and then restart Kodi
                utils.messageDialog(utils.lang(29999), utils.lang(39044))
                goto_settings = True

            # Go to network credentials?
            if utils.yesno_dialog(utils.lang(39029), utils.lang(39030)):
                LOG.debug("Presenting network credentials dialog.")
                from .windows import direct_path_sources
                direct_path_sources.start()
        # Disable Plex music?
        if utils.yesno_dialog(utils.lang(29999), utils.lang(39016)):
            LOG.debug("User opted to disable Plex music library.")
            utils.settings('enableMusic', value="false")

        # Download additional art from FanArtTV
        if utils.yesno_dialog(utils.lang(29999), utils.lang(39061)):
            LOG.debug("User opted to use FanArtTV")
            utils.settings('FanartTV', value="true")
        # Do you want to replace your custom user ratings with an indicator of
        # how many versions of a media item you posses?
        if utils.yesno_dialog(utils.lang(29999), utils.lang(39718)):
            LOG.debug("User opted to replace user ratings with version number")
            utils.settings('indicate_media_versions', value="true")

        # If you use several Plex libraries of one kind, e.g. "Kids Movies" and
        # "Parents Movies", be sure to check https://goo.gl/JFtQV9
        # dialog.ok(heading=utils.lang(29999), message=utils.lang(39076))

        # Need to tell about our image source for collections: themoviedb.org
        # dialog.ok(heading=utils.lang(29999), message=utils.lang(39717))
        # Make sure that we only ask these questions upon first installation
        utils.settings('InstallQuestionsAnswered', value='true')

        if goto_settings is False:
            # Open Settings page now? You will need to restart!
            goto_settings = utils.yesno_dialog(utils.lang(29999),
                                               utils.lang(39017))
        # New installation - make sure we start with a clean slate
        utils.wipe_database(reboot=False)
        if goto_settings:
            LOG.info('User chose to go to the PKC settings - suspending PKC')
            app.APP.stop_pkc = True
            executebuiltin(
                'Addon.OpenSettings(plugin.video.plexkodiconnect)')
            return
        utils.reboot_kodi()
