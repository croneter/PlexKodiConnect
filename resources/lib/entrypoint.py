#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Loads of different functions called in SEPARATE Python instances through
e.g. plugin://... calls. Hence be careful to only rely on window variables.
"""
from logging import getLogger
import sys
import copy
import xml.etree.ElementTree as etree

import xbmc
import xbmcplugin
from xbmcgui import ListItem

from . import utils
from . import path_ops
from .downloadutils import DownloadUtils as DU
from .plex_api import API, mass_api
from . import plex_functions as PF
from . import variables as v
# Be careful - your using app in another Python instance!
from . import app, widgets
from .library_sync.nodes import NODE_TYPES


LOG = getLogger('PLEX.entrypoint')


class ListingException(Exception):
    """
    Exception raised if something went wrong and we need to fail gracefully on
    the Kodi side of things when trying to list add-on content.
    I.e.: make sure to call xbmcplugin.endOfDirectory(int(sys.argv[1]), False)
    """
    pass


def guess_video_or_audio():
    """
    Returns either 'video', 'audio' or 'image', based how the user navigated to
    the current view.
    Returns None if this failed, e.g. when the user picks widgets
    """
    content_type = None
    if xbmc.getCondVisibility('Window.IsActive(Videos)'):
        content_type = 'video'
    elif xbmc.getCondVisibility('Window.IsActive(Music)'):
        content_type = 'audio'
    elif xbmc.getCondVisibility('Window.IsActive(Pictures)'):
        content_type = 'image'
    elif xbmc.getCondVisibility('Container.Content(movies)'):
        content_type = 'video'
    elif xbmc.getCondVisibility('Container.Content(episodes)'):
        content_type = 'video'
    elif xbmc.getCondVisibility('Container.Content(seasons)'):
        content_type = 'video'
    elif xbmc.getCondVisibility('Container.Content(tvshows)'):
        content_type = 'video'
    elif xbmc.getCondVisibility('Container.Content(albums)'):
        content_type = 'audio'
    elif xbmc.getCondVisibility('Container.Content(artists)'):
        content_type = 'audio'
    elif xbmc.getCondVisibility('Container.Content(songs)'):
        content_type = 'audio'
    elif xbmc.getCondVisibility('Container.Content(pictures)'):
        content_type = 'image'
    return content_type


def _wait_for_auth():
    """
    Call to be sure that PKC is authenticated, e.g. for widgets on Kodi startup
    Will wait for at most 30s, then raise a ListingException if not authorized.

    WARNING - this will potentially stall the shutdown of Kodi since we cannot
    poll xbmc.Monitor().abortRequested() or waitForAbort()
    """
    counter = 0
    startupdelay = int(utils.settings('startupDelay') or 0)
    # Wait for <startupdelay in seconds> + 10 seconds at most
    startupdelay = 10 * startupdelay + 100
    while utils.window('plex_authenticated') != 'true':
        counter += 1
        if counter == startupdelay:
            LOG.error('Aborting view, we were not authenticated for PMS')
            raise ListingException
        xbmc.sleep(100)


def directory_item(label, path, folder=True):
    """
    Adds a xbmcplugin.addDirectoryItem() directory itemlistitem
    """
    listitem = ListItem(label, path=path)
    listitem.setArt(
        {'landscape':'special://home/addons/plugin.video.plexkodiconnect/fanart.jpg',
         'fanart': 'special://home/addons/plugin.video.plexkodiconnect/fanart.jpg',
         'thumb': 'special://home/addons/plugin.video.plexkodiconnect/icon.png'})
    xbmcplugin.addDirectoryItem(handle=int(sys.argv[1]),
                                url=path,
                                listitem=listitem,
                                isFolder=folder)


def show_main_menu(content_type=None):
    """
    Shows the main PKC menu listing with all libraries, Channel, settings, etc.
    """
    content_type = content_type or guess_video_or_audio()
    LOG.debug('Do main listing for content_type %s', content_type)
    xbmcplugin.setContent(int(sys.argv[1]), v.CONTENT_TYPE_FILE)
    # Get nodes from the window props
    totalnodes = int(utils.window('Plex.nodes.total') or 0)
    # Plex Hub
    path = 'plugin://%s?mode=hub' % v.ADDON_ID
    if content_type:
        path += '&content_type=%s' % content_type
    directory_item('Plex Hub', path)
    # Entries for sections, if the content type matches
    for i in range(totalnodes):
        path = utils.window('Plex.nodes.%s.index' % i)
        if not path:
            continue
        label = utils.window('Plex.nodes.%s.title' % i)
        node_type = utils.window('Plex.nodes.%s.type' % i)
        # because we do not use seperate entrypoints for each content type,
        # we need to figure out which items to show in each listing. for
        # now we just only show picture nodes in the picture library video
        # nodes in the video library and all nodes in any other window
        if node_type == v.CONTENT_TYPE_PHOTO and content_type == 'image':
            directory_item(label, path)
        elif node_type in (v.CONTENT_TYPE_ARTIST,
                           v.CONTENT_TYPE_ALBUM,
                           v.CONTENT_TYPE_SONG) and content_type == 'audio':
            directory_item(label, path)
        elif node_type in (v.CONTENT_TYPE_MOVIE,
                           v.CONTENT_TYPE_SHOW,
                           v.CONTENT_TYPE_MUSICVIDEO) and content_type == 'video':
            directory_item(label, path)
        elif content_type is None:
            # To let the user pick this node as a WIDGET (content_type is None)
            # Should only be called if the user selects widgets
            LOG.info('Detected user selecting widgets')
            directory_item(label, path)
    # Playlists
    if content_type != 'image':
        path = 'plugin://%s?mode=playlists' % v.ADDON_ID
        if content_type:
            path += '&content_type=%s' % content_type
        directory_item(utils.lang(136), path)
    # Plex Search "Search"
    directory_item(utils.lang(137), "plugin://%s?mode=search" % v.ADDON_ID)
    # Plex Watch later and Watchlist
    if content_type not in ('image', 'audio'):
        directory_item(utils.lang(39211),
                       "plugin://%s?mode=watchlater" % v.ADDON_ID)
        directory_item(utils.lang(39212),
                       "plugin://%s?mode=watchlist" % v.ADDON_ID)
    # Plex Channels
    directory_item(utils.lang(30173), "plugin://%s?mode=channels" % v.ADDON_ID)
    # Plex user switch
    directory_item('%s%s' % (utils.lang(39200), utils.settings('username')),
                   "plugin://%s?mode=switchuser" % v.ADDON_ID)

    # some extra entries for settings and stuff
    directory_item(utils.lang(39201), "plugin://%s?mode=settings" % v.ADDON_ID)
    directory_item(utils.lang(39204),
                   "plugin://%s?mode=manualsync" % v.ADDON_ID)


def show_section(section_index):
    """
    Displays menu for an entire Plex section. We're using add-on paths instead
    of Kodi video library xmls to be able to use type="filter" library xmls
    and thus set the "content"

    Only used for synched Plex sections - otherwise, PMS xml for the section
    is used directly
    """
    LOG.debug('Do section listing for section index %s', section_index)
    xbmcplugin.setContent(int(sys.argv[1]), v.CONTENT_TYPE_FILE)
    # Get nodes from the window props
    node = 'Plex.nodes.%s' % section_index
    content = utils.window('%s.type' % node)
    plex_type = v.PLEX_TYPE_MOVIE if content == v.CONTENT_TYPE_MOVIE \
        else v.PLEX_TYPE_SHOW
    for node_type, *_ in NODE_TYPES[plex_type]:
        label = utils.window('%s.%s.title' % (node, node_type))
        path = utils.window('%s.%s.index' % (node, node_type))
        directory_item(label, path)


def show_listing(xml, plex_type=None, section_id=None, synched=True, key=None):
    """
    Pass synched=False if the items have not been synched to the Kodi DB

    Kodi content type will be set using the very first item returned by the PMS
    """
    try:
        xml[0]
    except IndexError:
        LOG.info('xml received from the PMS is empty: %s, %s',
                 xml.tag, xml.attrib)
        return
    api = API(xml[0])
    # Determine content type for Kodi's Container.content
    if key == '/hubs/home/continueWatching' or key == 'watchlist':
        # Mix of movies and episodes
        plex_type = v.PLEX_TYPE_VIDEO
    elif key == '/hubs/home/recentlyAdded?type=2':
        # "Recently Added TV", potentially a mix of Seasons and Episodes
        plex_type = v.PLEX_TYPE_VIDEO
    elif api.plex_type is None and api.fast_key and '?collection=' in api.fast_key:
        # Collections/Kodi sets
        plex_type = v.PLEX_TYPE_SET
    elif api.plex_type is None and plex_type:
        # e.g. browse by folder - folders will be listed first
        # Retain plex_type
        pass
    else:
        plex_type = api.plex_type
    content_type = v.CONTENT_FROM_PLEX_TYPE[plex_type]
    LOG.debug('show_listing: section_id %s, synched %s, key %s, plex_type %s, '
              'content type %s',
              section_id, synched, key, plex_type, content_type)
    xbmcplugin.setContent(int(sys.argv[1]), content_type)
    # Initialization
    widgets.PLEX_TYPE = plex_type
    widgets.SYNCHED = synched
    if plex_type == v.PLEX_TYPE_EPISODE and key and 'onDeck' in key:
        widgets.APPEND_SHOW_TITLE = utils.settings('OnDeckTvAppendShow') == 'true'
        widgets.APPEND_SXXEXX = utils.settings('OnDeckTvAppendSeason') == 'true'
    if plex_type == v.PLEX_TYPE_EPISODE and key and 'recentlyAdded' in key:
        widgets.APPEND_SHOW_TITLE = utils.settings('RecentTvAppendShow') == 'true'
        widgets.APPEND_SXXEXX = utils.settings('RecentTvAppendSeason') == 'true'
    if api.tag == 'Playlist':
        # Only show video playlists if navigation started for videos
        # and vice-versa for audio playlists
        content = guess_video_or_audio()
        if content:
            for entry in reversed(xml):
                tmp_api = API(entry)
                if tmp_api.playlist_type() != content:
                    xml.remove(entry)
    if xml.get('librarySectionID'):
        widgets.SECTION_ID = utils.cast(int, xml.get('librarySectionID'))
    elif section_id:
        widgets.SECTION_ID = utils.cast(int, section_id)
    if xml.get('viewGroup') == 'secondary':
        # Need to chain keys for navigation
        widgets.KEY = key
    # Process all items to show
    all_items = mass_api(xml, check_by_guid=key == "watchlist")

    if key == "watchlist":
        # filter out items that are not in the kodi db (items that will not be playable)
        all_items = [item for item in all_items if item.kodi_id is not None]

        # filter out items in the wrong section id when it's specified
        if section_id is not None:
            all_items = [item for item in all_items
                         if item.section_id == utils.cast(int, section_id)]

    all_items = [widgets.generate_item(api) for api in all_items]
    all_items = [widgets.prepare_listitem(item, key) for item in all_items]
    # fill that listing...
    all_items = [widgets.create_listitem(item) for item in all_items]
    xbmcplugin.addDirectoryItems(int(sys.argv[1]), all_items, len(all_items))
    # end directory listing
    xbmcplugin.addSortMethod(int(sys.argv[1]), xbmcplugin.SORT_METHOD_UNSORTED)


def get_video_files(plex_id, params):
    """
    GET VIDEO EXTRAS FOR LISTITEM

    returns the video files for the item as plugin listing, can be used for
    browsing the actual files or videoextras etc.
    """
    if plex_id is None:
        filename = params.get('filename')
        if filename is not None:
            filename = filename[0]
            import re
            regex = re.compile(r'''library/metadata/(\d+)''')
            filename = regex.findall(filename)
            try:
                plex_id = filename[0]
            except IndexError:
                pass

    if plex_id is None:
        LOG.info('No Plex ID found, abort getting Extras')
        raise ListingException
    _wait_for_auth()
    app.init(entrypoint=True)
    item = PF.GetPlexMetadata(plex_id)
    try:
        path = item[0][0][0].attrib['file']
    except (TypeError, IndexError, AttributeError, KeyError):
        LOG.error('Could not get file path for item %s', plex_id)
        raise ListingException
    # Assign network protocol
    if path.startswith('\\\\'):
        path = path.replace('\\\\', 'smb://')
        path = path.replace('\\', '/')
    # Plex returns Windows paths as e.g. 'c:\slfkjelf\slfje\file.mkv'
    elif '\\' in path:
        path = path.replace('\\', '\\\\')
    # Directory only, get rid of filename
    path = path.replace(path_ops.path.basename(path), '')
    if not path_ops.exists(path):
        LOG.error('get_video_files: Kodi cannot access folder %s', path)
        raise ListingException
    for root, dirs, files in path_ops.walk(path):
        for directory in dirs:
            item_path = path_ops.path.join(root, directory)
            listitem = ListItem(item_path, path=item_path)
            xbmcplugin.addDirectoryItem(handle=int(sys.argv[1]),
                                        url=item_path,
                                        listitem=listitem,
                                        isFolder=True)
        for file in files:
            item_path = path_ops.path.join(root, file)
            listitem = ListItem(item_path, path=item_path)
            xbmcplugin.addDirectoryItem(handle=int(sys.argv[1]),
                                        url=file,
                                        listitem=listitem)
        break


def extra_fanart(plex_id, plex_path):
    """
    Get extrafanart for listitem
    will be called by skinhelper script to get the extrafanart
    for tvshows we get the plex_id just from the path
    """
    LOG.debug('extra_fanart alled with plex_id: %s, plex_path: %s',
              plex_id, plex_path)
    if not plex_id:
        if "plugin.video.plexkodiconnect" in plex_path:
            plex_id = plex_path.split("/")[-2]
    if not plex_id:
        LOG.error('extra_fanart: Could not get a plex_id, aborting')
        raise ListingException

    # We need to store the images locally for this to work
    # because of the caching system in xbmc
    fanart_dir = path_ops.translate_path("special://thumbnails/plex/%s/"
                                         % plex_id)
    _wait_for_auth()
    if not path_ops.exists(fanart_dir):
        # Download the images to the cache directory
        path_ops.makedirs(fanart_dir)
        app.init(entrypoint=True)
        xml = PF.GetPlexMetadata(plex_id)
        if xml in (None, 401):
            LOG.error('Could not download metadata for %s', plex_id)
            raise ListingException
        api = API(xml[0])
        backdrops = api.artwork()['Backdrop']
        for count, backdrop in enumerate(backdrops):
            # Same ordering as in artwork
            art_file = path_ops.path.join(fanart_dir, "fanart%.3d.jpg" % count)
            listitem = ListItem("%.3d" % count, path=art_file)
            xbmcplugin.addDirectoryItem(
                handle=int(sys.argv[1]),
                url=art_file,
                listitem=listitem)
            path_ops.copyfile(backdrop, art_file)
    else:
        LOG.info("Found cached backdrop.")
        # Use existing cached images
        fanart_dir = fanart_dir
        for root, _, files in path_ops.walk(fanart_dir):
            for file in files:
                art_file = path_ops.path.join(root, file)
                listitem = ListItem(file, path=art_file)
                xbmcplugin.addDirectoryItem(handle=int(sys.argv[1]),
                                            url=art_file,
                                            listitem=listitem)


def playlists(content_type):
    """
    Lists all Plex playlists of the media type plex_playlist_type
    content_type: 'audio', 'video'
    """
    LOG.debug('Listing Plex playlists for content type %s', content_type)
    _wait_for_auth()
    app.init(entrypoint=True)
    from .playlists.pms import all_playlists
    xml = all_playlists()
    if xml is None:
        raise ListingException
    if content_type is not None:
        # This will be skipped if user selects a widget
        # Buggy xml.remove(child) requires reversed()
        for entry in reversed(xml):
            api = API(entry)
            if not api.playlist_type() == content_type:
                xml.remove(entry)
    show_listing(xml)


def hub(content_type):
    """
    Plus hub endpoint pms:port/hubs. Need to separate Kodi types with
    content_type:
        audio, video, image
    """
    content_type = content_type or guess_video_or_audio()
    LOG.debug('Showing Plex Hub entries for %s', content_type)
    _wait_for_auth()
    app.init(entrypoint=True)
    xml = PF.get_plex_hub()
    try:
        xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        LOG.error('Could not get Plex hub listing')
        raise ListingException
    # We need to make sure that only entries that WORK are displayed
    # WARNING: using xml.remove(child) in for-loop requires traversing from
    # the end!
    pkc_cont_watching = None
    for entry in reversed(xml):
        api = API(entry)
        append = False
        if content_type == 'video' and api.plex_type in v.PLEX_VIDEOTYPES:
            append = True
        elif content_type == 'audio' and api.plex_type in v.PLEX_AUDIOTYPES:
            append = True
        elif content_type == 'image' and api.plex_type == v.PLEX_TYPE_PHOTO:
            append = True
        elif content_type != 'image' and api.plex_type == v.PLEX_TYPE_PLAYLIST:
            append = True
        elif content_type is None:
            # Needed for widgets, where no content_type is provided
            append = True
        if not append:
            xml.remove(entry)

        # HACK ##################
        # Merge Plex's "Continue watching" with "On deck"
        if entry.get('key') == '/hubs/home/continueWatching':
            pkc_cont_watching = copy.deepcopy(entry)
            pkc_cont_watching.set('key', '/hubs/continueWatching')
            title = pkc_cont_watching.get('title') or 'Continue Watching'
            pkc_cont_watching.set('title', 'PKC %s' % title)
    if pkc_cont_watching:
        for i, entry in enumerate(xml):
            if entry.get('key') == '/hubs/home/continueWatching':
                xml.insert(i + 1, pkc_cont_watching)
                break
    # END HACK ##################
    show_listing(xml)


def watchlater():
    """
    Listing for plex.tv Watch Later section (if signed in to plex.tv)
    """
    _wait_for_auth()
    if utils.window('plex_token') == '':
        LOG.error('No watch later - not signed in to plex.tv')
        raise ListingException
    if utils.window('plex_restricteduser') == 'true':
        LOG.error('No watch later - restricted user')
        raise ListingException
    app.init(entrypoint=True)
    xml = DU().downloadUrl('https://plex.tv/pms/playlists/queue/all',
                           authenticate=False,
                           headerOptions={'X-Plex-Token': utils.window('plex_token')})
    try:
        xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        LOG.error('Could not download watch later list from plex.tv')
        raise ListingException
    show_listing(xml)


def watchlist(section_id=None):
    """
    Listing for plex.tv Watchlist section (if signed in to plex.tv)
    """
    _wait_for_auth()
    if utils.window('plex_token') == '':
        LOG.error('No watchlist - not signed in to plex.tv')
        raise ListingException
    if utils.window('plex_restricteduser') == 'true':
        LOG.error('No watchlist - restricted user')
        raise ListingException
    app.init(entrypoint=True)
    xml = DU().downloadUrl('https://discover.provider.plex.tv/library/sections/watchlist/all',
                           authenticate=False,
                           headerOptions={'X-Plex-Token': utils.window('plex_token')})
    try:
        xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        LOG.error('Could not download watch list list from plex.tv')
        raise ListingException
    show_listing(xml, None, section_id, False, "watchlist")


def browse_plex(key=None, plex_type=None, section_id=None, synched=True,
                args=None, prompt=None, query=None):
    """
    Lists the content of a Plex folder, e.g. channels. Either pass in key (to
    be used directly for PMS url {server}<key>) or the section_id

    Pass synched=False if the items have NOT been synched to the Kodi DB
    """
    LOG.debug('Browsing to key %s, section %s, plex_type: %s, synched: %s, '
              'prompt "%s", args %s', key, section_id, plex_type, synched,
              prompt, args)
    _wait_for_auth()
    app.init(entrypoint=True)
    args = args or {}
    if query:
        args['query'] = query
    elif prompt:
        prompt = utils.dialog('input', prompt)
        if prompt is None:
            LOG.debug('User cancelled prompt for browse_plex')
            raise ListingException
        prompt = prompt.strip()
        args['query'] = prompt
    xml = DU().downloadUrl(utils.extend_url('{server}%s' % key, args))
    try:
        xml.attrib
    except AttributeError:
        LOG.error('Could not browse to key %s, section %s', key, section_id)
        raise ListingException
    if len(xml) > 0 and xml[0].tag == 'Hub':
        # E.g. when hitting the endpoint '/hubs/search'
        answ = etree.Element(xml.tag, attrib=xml.attrib)
        for hub in xml:
            if not utils.cast(int, hub.get('size')):
                # Empty category
                continue
            for entry in hub:
                api = API(entry)
                if api.plex_type == v.PLEX_TYPE_TAG:
                    # Append the type before the actual element for all "tags"
                    # like genres, actors, etc.
                    entry.attrib['tag'] = '%s: %s' % (hub.get('title'),
                                                      api.tag_label())
                answ.append(entry)
        xml = answ
    show_listing(xml, plex_type, section_id, synched, key)


def extras(plex_id):
    """
    Lists all extras for plex_id
    """
    LOG.debug('Showing extras')
    _wait_for_auth()
    app.init(entrypoint=True)
    xml = PF.GetPlexMetadata(plex_id)
    try:
        xml[0].attrib
    except (TypeError, IndexError, KeyError):
        LOG.error('Could not get extras for Plex id %s', plex_id)
        raise ListingException
    extras = API(xml[0]).extras()
    if extras is None:
        return
    for child in xml:
        xml.remove(child)
    for i, child in enumerate(extras):
        xml.insert(i, child)
    show_listing(xml, synched=False, plex_type=v.PLEX_TYPE_MOVIE)
