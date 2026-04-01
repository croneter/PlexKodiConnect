#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Various functions and decorators for PKC
"""
from logging import getLogger
from sqlite3 import OperationalError
from datetime import datetime
from unicodedata import normalize
from threading import Lock
import urllib
# even with the above import urllib, Python3 sometimes runs into this issue
# AttributeError: module 'urllib' has no attribute 'parse'
# Hence import explicitly
import urllib.parse
# Originally tried faster cElementTree, but does NOT work reliably with Kodi
# etree parse unsafe; make sure we're always receiving unicode
from .defusedxml import ElementTree as etree
from .defusedxml.ElementTree import ParseError
import xml.etree.ElementTree as undefused_etree
from functools import wraps
import re
import gc

import xbmc
import xbmcaddon
import xbmcgui

from . import pathvalidate, path_ops, variables as v

LOG = getLogger('PLEX.utils')

WINDOW = xbmcgui.Window(10000)
ADDON = xbmcaddon.Addon(id='plugin.video.plexkodiconnect')

# If several threads access  the settings.xml file concurrently, it gets
# corrupted
SETTINGS_LOCK = Lock()

# Grab Plex id from '...plex_id=XXXX....'
REGEX_PLEX_ID = re.compile(r'''plex_id=(\d+)''')
# Return the numbers at the end of an url like '.../.../XXXX'
REGEX_END_DIGITS = re.compile(r'''/(.+)/(\d+)$''')
REGEX_PLEX_DIRECT = re.compile(r'''\.plex\.direct:\d+$''')
# Plex API
REGEX_IMDB = re.compile(r'''/(tt\d+)''')
REGEX_TVDB = re.compile(r'''(?:the)?tvdb(?::\/\/|[2-5]?-)(\d+?)\?''')
REGEX_TMDB = re.compile(r'''themoviedb:\/\/(.+?)\?''')
REGEX_ANIDB = re.compile(r'''anidb[2-4]?-(\d+?)\?''')
# Plex music
REGEX_MUSICPATH = re.compile(r'''^\^(.+)\$$''')
# Grab Plex id from an URL-encoded string
REGEX_PLEX_ID_FROM_URL = re.compile(r'''metadata%2F(\d+)''')

SAFE_URL_CHARACTERS = "%/:=&?~#+!$,;'@()*[]"
HTTP_DAV_FTP = re.compile(r'(http(s)?|dav(s)?|(s)?ftp)://((.+):(.+)@)?([\w\.]+)(:([\d]+))?/')


def garbageCollect():
    gc.collect(2)


def setGlobalProperty(key, val):
    xbmcgui.Window(10000).setProperty(
        'plugin.video.plexkodiconnect.{0}'.format(key), val)


def setGlobalBoolProperty(key, boolean):
    xbmcgui.Window(10000).setProperty(
        'plugin.video.plexkodiconnect.{0}'.format(key), boolean and '1' or '')


def getGlobalProperty(key):
    return xbmc.getInfoLabel(
        'Window(10000).Property(plugin.video.plexkodiconnect.{0})'.format(key))


def reboot_kodi(message=None):
    """
    Displays an OK prompt with 'Kodi will now restart to apply the changes'
    Kodi will then reboot.

    Set optional custom message
    """
    message = message or lang(33033)
    messageDialog(lang(29999), message)
    xbmc.executebuiltin('RestartApp')


def window(prop, value=None, clear=False, windowid=10000):
    """
    Get or set window property - thread safe!

    Returns unicode.

    Property and value may be string or unicode
    """
    if windowid != 10000:
        win = xbmcgui.Window(windowid)
    else:
        win = WINDOW
    if clear:
        win.clearProperty(prop)
    elif value is not None:
        win.setProperty(prop, value)
    else:
        return win.getProperty(prop)


def settings(setting, value=None):
    """
    Get or add addon setting. Returns unicode

    setting and value can either be unicode or string
    """
    # We need to instantiate every single time to read changed variables!
    with SETTINGS_LOCK:
        addon = xbmcaddon.Addon('plugin.video.plexkodiconnect')
        if value is not None:
            # Takes string or unicode by default!
            addon.setSetting(setting, value)
        else:
            # Should return unicode by default, but just in case
            return addon.getSetting(setting)


def lang(stringid):
    """
    Central string retrieval from strings.po. If not found within PKC,
    standard XBMC/Kodi strings are retrieved.
    Will return unicode
    """
    return (ADDON.getLocalizedString(stringid) or
            xbmc.getLocalizedString(stringid))


def messageDialog(heading, msg):
    """
    Shows a dialog using the Plex layout
    """
    from .windows import optionsdialog
    optionsdialog.show(heading, msg, lang(186))


def yesno_dialog(heading, msg):
    """
    Shows a dialog with a yes and a no button using the Plex layout.
    Returns True if the user selected yes, False otherwise
    """
    from .windows import optionsdialog
    return optionsdialog.show(heading, msg, lang(107), lang(106)) == 0


def dialog(typus, *args, **kwargs):
    """
    Displays xbmcgui Dialog. Pass a string as typus:
        'yesno', 'ok', 'notification', 'input', 'select', 'numeric'
    kwargs:
        heading='{plex}'        title bar (here PlexKodiConnect)
        message=lang(30128),    Dialog content
        time=5000,
        sound=True,
        nolabel=str(),          For 'yesno' dialogs
        yeslabel=str(),         For 'yesno' dialogs
    Icons:
        icon='{plex}'       Display Plex standard icon
        icon='{info}'       xbmcgui.NOTIFICATION_INFO
        icon='{warning}'    xbmcgui.NOTIFICATION_WARNING
        icon='{error}'      xbmcgui.NOTIFICATION_ERROR
    Input Types:
        type='{alphanum}'   xbmcgui.INPUT_ALPHANUM (standard keyboard)
        type='{numeric}'    xbmcgui.INPUT_NUMERIC (format: #)
        type='{date}'       xbmcgui.INPUT_DATE (format: DD/MM/YYYY)
        type='{time}'       xbmcgui.INPUT_TIME (format: HH:MM)
        type='{ipaddress}'  xbmcgui.INPUT_IPADDRESS (format: #.#.#.#)
        type='{password}'   xbmcgui.INPUT_PASSWORD
                            (return md5 hash of input, input is masked)
    Options:
        option='{password}' xbmcgui.PASSWORD_VERIFY (verifies an existing
                            (default) md5 hashed password)
        option='{hide}'     xbmcgui.ALPHANUM_HIDE_INPUT (masks input)
    """
    if 'icon' in kwargs:
        types = {
            '{plex}': 'special://home/addons/plugin.video.plexkodiconnect/icon.png',
            '{info}': xbmcgui.NOTIFICATION_INFO,
            '{warning}': xbmcgui.NOTIFICATION_WARNING,
            '{error}': xbmcgui.NOTIFICATION_ERROR
        }
        for key, value in types.items():
            kwargs['icon'] = kwargs['icon'].replace(key, value)
    if 'type' in kwargs:
        types = {
            '{alphanum}': xbmcgui.INPUT_ALPHANUM,
            '{numeric}': xbmcgui.INPUT_NUMERIC,
            '{date}': xbmcgui.INPUT_DATE,
            '{time}': xbmcgui.INPUT_TIME,
            '{ipaddress}': xbmcgui.INPUT_IPADDRESS,
            '{password}': xbmcgui.INPUT_PASSWORD
        }
        kwargs['type'] = types[kwargs['type']]
    if 'option' in kwargs:
        types = {
            '{password}': xbmcgui.PASSWORD_VERIFY,
            '{hide}': xbmcgui.ALPHANUM_HIDE_INPUT
        }
        kwargs['option'] = types[kwargs['option']]
    if 'heading' in kwargs:
        kwargs['heading'] = kwargs['heading'].replace("{plex}",
                                                      lang(29999))
    dia = xbmcgui.Dialog()
    types = {
        'yesno': dia.yesno,
        'ok': dia.ok,
        'notification': dia.notification,
        'input': dia.input,
        'select': dia.select,
        'numeric': dia.numeric,
        'contextmenu': dia.contextmenu
    }
    return types[typus](*args, **kwargs)


def ERROR(txt='', hide_tb=False, notify=False, cancel_sync=False):
    import sys
    short = str(sys.exc_info()[1])
    LOG.error('Error encountered: %s - %s', txt, short)
    if cancel_sync:
        from . import app
        app.APP.stop_threads(block=False)
    if hide_tb:
        return short

    import traceback
    trace = traceback.format_exc()
    LOG.error("_____________________________________________________________")
    for line in trace.splitlines():
        LOG.error('    ' + line)
    LOG.error("_____________________________________________________________")
    if notify:
        dialog('notification',
               heading='{plex}',
               message=short,
               icon='{error}')
    return short


def rreplace(s, old, new, occurrence=-1):
    """
    Replaces the string old [str, unicode] with new from the RIGHT given a
    string s.
    """
    li = s.rsplit(old, occurrence)
    return new.join(li)


class AttributeDict(dict):
    """
    Turns an etree xml response's xml.attrib into an object with attributes
    """
    def __getattr__(self, attr):
        return self.get(attr)

    def __setattr__(self, attr, value):
        self[attr] = value

    def __repr__(self):
        return '<{0}:{1}:{2}>'.format(self.__class__.__name__,
                                      self.id,
                                      self.get('title', 'None'))


def cast(func, value):
    """
    Cast the specified value to the specified type (returned by func). Currently
    this only support int, float, bool. Should be extended if needed.
    Parameters:
        func (func): Calback function to used cast to type (int, bool, float).
        value (any): value to be cast and returned.

    Returns None if something goes wrong
    """
    if value is None:
        return value
    elif func == bool:
        return bool(int(value))
    elif func == str:
        if isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            return value
        else:
            return value.decode('utf-8')
    elif func == str:
        if isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            return value
        else:
            return value.encode('utf-8')
    elif func == int:
        try:
            return int(value)
        except ValueError:
            try:
                # Converting e.g. '8.0' fails; need to convert to float first
                return int(float(value))
            except ValueError:
                return
    elif func == float:
        try:
            return float(value)
        except ValueError:
            return
    return func(value)


def extend_url(url, params):
    """
    Pass in an url [string] and params [dict]. Returns the extended url
        '<url><? or &><urllib.urlencode(params)>'
    in string
    """
    params = urllib.parse.urlencode(params)
    if '?' in url:
        return f'{url}&{params}'
    else:
        return f'{url}?{params}'


def parse_qs(qs, keep_blank_values=0, strict_parsing=0):
    """
    Pass in the query string qs as string. Returns a dict with lists as values
    as unicode
    """
    return urllib.parse.parse_qs(qs, keep_blank_values, strict_parsing)


def parse_qsl(qs, keep_blank_values=0, strict_parsing=0):
    """
    Pass in string. Returns a list of string tuples
    """
    return urllib.parse.parse_qsl(qs, keep_blank_values, strict_parsing)


def urlparse(url, scheme='', allow_fragments=True):
    """
    Pass in string.
    CAREFUL: returns an encoded urlparse.ParseResult()!
    """
    return urllib.parse.urlparse(url, scheme, allow_fragments)


def escape_path(path, safe_url_char=SAFE_URL_CHARACTERS):
    """
    Uses urllib.quote to escape to escape path [unicode]. See here for the
    reasoning whether a character is safe or not and whether or not it should
    be escaped:
        https://bugs.python.org/issue918368
    Letters, digits, and the characters '_.-' are never quoted. Choosing the
    "wrong" characters for a password for USERNAME:PASSWORD@host.com will get
    you in trouble (e.g. '@')
    Returns the escaped path as unicode
    """
    is_http_dav_ftp = HTTP_DAV_FTP.match(path)
    if is_http_dav_ftp:
        # If path seems to be a http(s), dav(s) or (s)ftp url, the escape path will be constructed
        # using RegExp and using safe_url_char as safe characters not to be escaped
        protocol = is_http_dav_ftp.group(1)
        user = is_http_dav_ftp.group(6)
        passwd = is_http_dav_ftp.group(7)
        if user and passwd:
            user = quote(user, safe=safe_url_char)
            passwd = quote(passwd, safe=safe_url_char)
        host = is_http_dav_ftp.group(8)
        port = is_http_dav_ftp.group(10)
        url_path = path.replace(is_http_dav_ftp.group(), '', 1)
        if url_path:
            url_path = quote(path.replace(is_http_dav_ftp.group(), '', 1),
                                    safe=safe_url_char)
        return f'{protocol}://' + \
               (f'{user}:{passwd}@' if user and passwd else '') + \
               host + \
               (f':{port}' if port else '') + \
               (f'/{url_path}' if url_path else '')
    else:
        # If paths does not seem to be a http(s), dav(s) or (s)ftp url (e.g.
        # plugin://) escape path as before
        return quote(path, safe=SAFE_URL_CHARACTERS)


def quote(s, safe='/'):
    """
    Pass in unicode, returns unicode
    """
    return urllib.parse.quote(s, safe)


def quote_plus(s, safe=''):
    """
    Pass in unicode, returns unicode
    """
    return urllib.parse.quote_plus(s, safe)


def unquote(s):
    """
    Pass in unicode, returns unicode
    """
    return urllib.parse.unquote(s)


def slugify(text):
    """
    Normalizes text (in unicode or string) to e.g. enable safe filenames.
    Returns unicode
    """
    if not isinstance(text, str):
        text = str(text)
    return str(normalize('NFKD', text).encode('ascii', 'ignore'))


def valid_filename(text):
    """
    Return a valid filename after passing it in [unicode].
    """
    return pathvalidate.sanitize_filename(text,
                                          replacement_text='_',
                                          platform='auto',
                                          max_len=248)


def escape_html(string):
    """
    Escapes the following:
        < to &lt;
        > to &gt;
        & to &amp;
    """
    escapes = {
        '<': '&lt;',
        '>': '&gt;',
        '&': '&amp;'
    }
    for key, value in escapes.items():
        string = string.replace(key, value)
    return string


def delete_temporary_subtitles():
    """
    Permanently deletes all temporary subtitle files
    """
    for root, _, files in path_ops.walk(v.EXTERNAL_SUBTITLE_TEMP_PATH):
        for file in files:
            try:
                path_ops.remove(path_ops.path.join(root, file))
            except (OSError, IOError) as err:
                LOG.error('Could not delete temporary subtitle: %s, %s: %s',
                          root, file, err)


def wipe_database(reboot=True):
    """
    Deletes all Plex playlists as well as video nodes, then clears Kodi as well
    as Plex databases completely.
    Will also delete all cached artwork.
    """
    LOG.warn('Start wiping')
    from .library_sync.sections import delete_files
    from .library_sync.common import PLAYLIST_SYNC_ENABLED
    from . import kodi_db, plex_db
    delete_files()
    if PLAYLIST_SYNC_ENABLED:
        from .playlists import remove_synced_playlists
        remove_synced_playlists()
    try:
        with plex_db.PlexDB(lock=False) as plexdb:
            if plexdb.songs_have_been_synced():
                LOG.info('Detected that music has also been synced - wiping music')
                music = True
            else:
                LOG.info('No music has been synced in the past - not wiping')
                music = False
    except OperationalError:
        # Plex DB completely empty yet. Wipe existing Kodi music only if we
        # expect to sync Plex music
        music = settings('enableMusic') == 'true'
    LOG.info("Resetting all cached artwork.")
    kodi_db.wipe_dbs(music)
    plex_db.wipe()

    # reset the install run flag
    settings('SyncInstallRunDone', value="false")
    settings('sections_asked_for_machine_identifier', value='')
    init_dbs()
    LOG.info('Wiping done')
    no_reboot = settings('kodi_db_has_been_wiped_clean') == 'true' or not reboot
    settings('kodi_db_has_been_wiped_clean', value='true')
    if not no_reboot:
        # Root cause is sqlite WAL mode - Kodi might still have DB access open
        LOG.warn('Need to restart Kodi before filling Kodi DB again')
        reboot_kodi()


def init_dbs():
    """
    Call e.g. on startup to ensure that Plex and Kodi DBs look like they should
    """
    from . import kodi_db, plex_db
    # Ensure that Plex DB is set-up
    plex_db.initialize()
    # Hack to speed up look-ups for actors (giant table!)
    kodi_db.create_kodi_db_indicees()
    kodi_db.setup_kodi_default_entries()
    with kodi_db.KodiVideoDB() as kodidb:
        # Setup the paths for addon-paths (even when using direct paths)
        kodidb.setup_path_table()
    LOG.info('Init DBs done')


def default_kodi_skin_warning_message():
    """"To ensure a smooth PlexKodiConnect experience, it is HIGHLY recommended
    to use Kodi's default skin \"Estuary\" for initial set-up and for possible
    database resets. Continue?"
    """
    if yesno_dialog(lang(29999), lang(30029)):
        LOG.warn('User accepted risk of a non-default skin')
        return True
    else:
        LOG.warn('User chose to stop due to skin not being Estuary')
        return False


def reset(ask_user=True):
    """
    User navigated to the PKC settings, Advanced, and wants to reset the Kodi
    database and possibly PKC entirely
    """
    # Are you sure you want to reset your local Kodi database?
    if ask_user and not yesno_dialog(lang(29999), lang(39600)):
        return
    from . import app
    # first stop any db sync
    app.APP.suspend_threads()
    # Reset all PlexKodiConnect Addon settings? (this is usually NOT
    # recommended and unnecessary!)
    if ask_user and yesno_dialog(lang(29999), lang(39603)):
        # Delete the settings
        LOG.info("Deleting: settings.xml")
        path_ops.remove("%ssettings.xml" % v.ADDON_PROFILE)

    # Wipe everything
    wipe_database()
    reboot_kodi()


def log_xml(xml, logger, loglevel):
    """
    Logs an etree xml. Pass the loglevel for which logging will happen, e.g.
    loglevel=logging.DEBUG
    """
    if LOG.isEnabledFor(loglevel):
        string = undefused_etree.tostring(xml, encoding='utf8')
        string = string.decode('utf-8')
        logger('\n' + string)


def compare_version(current, minimum):
    """
    Returns True if current is >= then minimum. False otherwise. Returns True
    if there was no valid input for current!

    Input strings: e.g. "1.2.3"; always with Major, Minor and Patch!
    """
    try:
        curr_major, curr_minor, curr_patch = current.split(".")
    except ValueError:
        # there WAS no current DB, e.g. deleted.
        return True
    min_major, min_minor, min_patch = minimum.split(".")
    curr_major = int(curr_major)
    curr_minor = int(curr_minor)
    curr_patch = int(curr_patch)
    min_major = int(min_major)
    min_minor = int(min_minor)
    min_patch = int(min_patch)

    if curr_major > min_major:
        return True
    elif curr_major < min_major:
        return False

    if curr_minor > min_minor:
        return True
    elif curr_minor < min_minor:
        return False
    return curr_patch >= min_patch


def normalize_nodes(text):
    """
    For video nodes. Returns unicode
    """
    text = text.replace(":", "")
    text = text.replace("/", "-")
    text = text.replace("\\", "-")
    text = text.replace("<", "")
    text = text.replace(">", "")
    text = text.replace("*", "")
    text = text.replace("?", "")
    text = text.replace('|', "")
    text = text.replace('(', "")
    text = text.replace(')', "")
    text = text.strip()
    # Remove dots from the last character as windows can not have directories
    # with dots at the end
    text = text.rstrip('.')
    text = normalize('NFKD', str(text, 'utf-8'))
    return text


def indent(elem, level=0):
    """
    Prettifies xml trees. Pass the etree root in
    """
    try:
        i = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for elem in elem:
                indent(elem, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i
    except Exception as err:
        LOG.info('Indentation failed with: %s', err)


# Python's arrow library is broken for Kodi
# Importing from several Python instances is faulty :-(

# def localdate_from_utc_string(timestring):
#     """helper to convert internal utc time (used in pvr) to local timezone"""
#     utc_datetime = arrow.get(timestring)
#     local_datetime = utc_datetime.to('local')
#     return local_datetime.format("YYYY-MM-DD HH:mm:ss")


# def localized_date_time(timestring):
#     """returns localized version of the timestring (used in pvr)"""
#     date_time = arrow.get(timestring)
#     local_date = date_time.strftime(xbmc.getRegion("dateshort"))
#     local_time = date_time.strftime(xbmc.getRegion("time").replace(":%S", ""))
#     return local_date, local_time


class XmlKodiSetting(object):
    """
    Used to load a Kodi XML settings file from special://profile as an etree
    object to read settings or set them. Usage:
        with XmlKodiSetting(filename,
                            path=None,
                            force_create=False,
                            top_element=None) as xml:
            xml.get_setting('test')

    filename [str]:      filename of the Kodi settings file under
    path [str]:          if set, replace special://profile path with custom
                         path
    force_create:        will create the XML file if it does not exist
    top_element [str]:   Name of the top xml element; used if xml does not
                         yet exist

    Raises IOError if the file does not exist or is empty and force_create
    has been set to False.
    Raises utils.ParseError if the file could not be parsed by etree

    xml.write_xml        Set to True if we need to write the XML to disk
    """
    def __init__(self, filename, path=None, force_create=False,
                 top_element=None):
        self.filename = filename
        if path is None:
            self.path = path_ops.path.join(v.KODI_PROFILE, filename)
        else:
            self.path = path_ops.path.join(path, filename)
        self.force_create = force_create
        self.top_element = top_element
        self.tree = None
        self.root = None
        self.write_xml = False

    def __enter__(self):
        try:
            self.tree = etree.parse(self.path)
        except IOError:
            # Document is blank or missing
            if self.force_create is False:
                LOG.debug('%s does not seem to exist; not creating', self.path)
                raise
            # Create topmost xml entry
            self.tree = undefused_etree.ElementTree(
                undefused_etree.Element(self.top_element))
            self.write_xml = True
        except ParseError:
            LOG.error('Error parsing %s', self.path)
            # "Kodi cannot parse {0}. PKC will not function correctly. Please
            # visit {1} and correct your file!"
            messageDialog(lang(29999), lang(39716).format(
                self.filename,
                'http://kodi.wiki'))
            raise
        self.root = self.tree.getroot()
        return self

    def __exit__(self, e_typ, e_val, trcbak):
        if e_typ:
            # re-raise any exception
            return False
        # Only safe to file if we did not botch anything
        if self.write_xml is True:
            self._remove_empty_elements()
            # Indent and make readable
            indent(self.root)
            # Safe the changed xml
            try:
                self.tree.write(self.path, encoding='utf-8')
            except IOError as err:
                LOG.error('Could not save xml %s. Error: %s',
                          self.filename, err)
                # Could not change the Kodi settings file {0}. PKC might not
                # work correctly. Error: {1}
                if not settings('%s_ioerror' % self.filename):
                    messageDialog(lang(29999),
                                  lang(30417).format(self.filename, err))
                    settings('%s_ioerror' % self.filename,
                             value='warning_shown')
        return True

    def _is_empty(self, element, empty_elements):
        empty = True
        for child in element:
            empty_child = True
            if list(child):
                empty_child = self._is_empty(child, empty_elements)
            if empty_child and (child.attrib or
                                (child.text and child.text.strip())):
                empty_child = False
            if empty_child:
                empty_elements.append((element, child))
            else:
                # At least one non-empty entry - hence we cannot delete the
                # original element itself
                empty = False
        return empty

    def _remove_empty_elements(self):
        """
        Deletes all empty XML elements, otherwise Kodi/PKC gets confused
        This is recursive, so an empty element with empty children will also
        get deleted
        """
        empty_elements = []
        self._is_empty(self.root, empty_elements)
        for element, child in empty_elements:
            element.remove(child)

    @staticmethod
    def _set_sub_element(element, subelement):
        """
        Returns an etree element's subelement. Creates one if not exist
        """
        answ = element.find(subelement)
        if answ is None:
            answ = undefused_etree.SubElement(element, subelement)
        return answ

    def get_setting(self, node_list):
        """
        node_list is a list of node names starting from the outside, ignoring
        the outter advancedsettings.
        Example nodelist=['video', 'busydialogdelayms'] for the following xml
        would return the etree Element:

            <busydialogdelayms>750</busydialogdelayms>

        for the following example xml:

        <advancedsettings>
            <video>
                <busydialogdelayms>750</busydialogdelayms>
            </video>
        </advancedsettings>

        Returns the etree element or None if not found
        """
        element = self.root
        for node in node_list:
            element = element.find(node)
            if element is None:
                break
        return element

    def set_setting(self, node_list, value=None, attrib=None, append=False):
        """
        node_list is a list of node names starting from the outside, ignoring
        the outter advancedsettings.
        Example nodelist=['video', 'busydialogdelayms'] for the following xml
        would return the etree Element:

            <busydialogdelayms>750</busydialogdelayms>

        for the following example xml:

        <advancedsettings>
            <video>
                <busydialogdelayms>750</busydialogdelayms>
            </video>
        </advancedsettings>

        value, e.g. '750' will be set accordingly, returning the new
        etree Element. Advancedsettings might be generated if it did not exist
        already

        If the dict attrib is set, the Element's attributs will be appended
        accordingly

        If append is True, the last element of node_list with value and attrib
        will always be added. WARNING: this will set self.write_xml to True!

        Returns the (last) etree element
        """
        attrib = attrib or {}
        value = value or ''
        if not append:
            old = self.get_setting(node_list)
            if (old is not None and
                    ((old.text is not None and old.text.strip() == value) or
                     (old.text is None and value == '')) and
                    (old.attrib or {}) == attrib):
                # Already set exactly these values
                return old
        LOG.debug('Adding etree to: %s, value: %s, attrib: %s, append: %s',
                  node_list, value, attrib, append)
        self.write_xml = True
        element = self.root
        nodes = node_list[:-1] if append else node_list
        for node in nodes:
            element = self._set_sub_element(element, node)
        if append:
            element = undefused_etree.SubElement(element, node_list[-1])
        # Write new values
        element.text = value
        if attrib:
            for key, attribute in attrib.items():
                element.set(key, attribute)
        return element


###############################################################################
# WRAPPERS

def catch_exceptions(warnuser=False):
    """
    Decorator for methods to catch exceptions and log them. Useful for e.g.
    librarysync threads using itemtypes.py, because otherwise we would not
    get informed of crashes

    warnuser=True:      sets the window flag 'plex_scancrashed' to true
                        which will trigger a Kodi infobox to inform user
    """
    def decorate(func):
        """
        Decorator construct
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            """
            Wrapper construct
            """
            try:
                return func(*args, **kwargs)
            except Exception as err:
                LOG.error('%s has crashed. Error: %s', func.__name__, err)
                import traceback
                LOG.error("Traceback:\n%s", traceback.format_exc())
                if warnuser:
                    window('plex_scancrashed', value='true')
                return
        return wrapper
    return decorate


def log_time(func):
    """
    Decorator for functions and methods to log the time it took to run the code
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        starttotal = datetime.now()
        result = func(*args, **kwargs)
        elapsedtotal = datetime.now() - starttotal
        LOG.info('It took %s to run the function %s',
                 elapsedtotal, func.__name__)
        return result
    return wrapper
