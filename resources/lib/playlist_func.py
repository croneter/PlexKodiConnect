#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Collection of functions associated with Kodi and Plex playlists and playqueues
"""
from logging import getLogger

from .plex_api import API
from .plex_db import PlexDB
from . import plex_functions as PF
from .kodi_db import kodiid_from_filename
from .downloadutils import DownloadUtils as DU
from . import utils
from .utils import cast
from . import json_rpc as js
from . import variables as v
from . import app
from .exceptions import PlaylistError
from .subtitles import accessible_plex_subtitles


LOG = getLogger('PLEX.playlist_func')


class PlaylistItem(object):
    """
    Object to fill our playqueues and playlists with.

    id = None          [int] Plex playlist/playqueue id, e.g. playQueueItemID
    plex_id = None     [int] Plex unique item id, "ratingKey"
    plex_type = None   [str] Plex type, e.g. 'movie', 'clip'
    kodi_id = None     [int] Kodi unique kodi id (unique only within type!)
    kodi_type = None   [str] Kodi type: 'movie'
    file = None        [str] Path to the item's file. STRING!!
    uri = None         [str] PMS path to item; will be auto-set with plex_id
    guid = None        [str] Weird Plex guid
    api = None         [API] API of xml 1 lvl below <MediaContainer>
    playmethod = None  [str] either 'DirectPath', 'DirectStream', 'Transcode'
    playcount = None   [int] how many times the item has already been played
    offset = None      [int] the item's view offset UPON START in Plex time
    part = 0           [int] part number if Plex video consists of mult. parts
    force_transcode    [bool] defaults to False
    """

    def __init__(self):
        self.id = None
        self._plex_id = None
        self.plex_type = None
        self.kodi_id = None
        self.kodi_type = None
        self.file = None
        self._uri = None
        self.guid = None
        self.api = None
        self.playmethod = None
        self.playcount = None
        self.offset = None
        self.playerid = None
        # Transcoding quality, if needed
        self.quality = None
        # If Plex video consists of several parts; part number
        self.part = 0
        self.force_transcode = False
        # Shall we ask user to resume this item?
        #   None: ask user to resume
        #   False: do NOT resume, don't ask user
        #   True: do resume, don't ask user
        self.resume = None
        # Get the Plex audio and subtitle streams in the same order as Kodi
        # uses them (Kodi uses indexes to activate them, not ids like Plex)
        self._streams_have_been_processed = False
        self._video_streams = None
        self._audio_streams = None
        self._subtitle_streams = None
        # Which Kodi streams are active?
        self._current_kodi_video_stream = None
        self._current_kodi_audio_stream = None
        # Kodi subs can be turned on/off additionally!
        self._current_kodi_sub_stream = None
        self._current_kodi_sub_stream_enabled = None
        self.streams_initialized = False

    @property
    def plex_id(self):
        return self._plex_id

    @plex_id.setter
    def plex_id(self, value):
        self._plex_id = value
        self._uri = ('server://%s/com.plexapp.plugins.library/library/metadata/%s' %
                     (app.CONN.machine_identifier, value))

    @property
    def uri(self):
        return self._uri

    @property
    def video_streams(self):
        if not self._streams_have_been_processed:
            self._process_streams()
        return self._video_streams

    @property
    def audio_streams(self):
        if not self._streams_have_been_processed:
            self._process_streams()
        return self._audio_streams

    @property
    def subtitle_streams(self):
        if not self._streams_have_been_processed:
            self._process_streams()
        return self._subtitle_streams

    @property
    def current_kodi_video_stream(self):
        return self._current_kodi_video_stream

    @current_kodi_video_stream.setter
    def current_kodi_video_stream(self, value):
        if value != self._current_kodi_video_stream:
            self.on_kodi_video_stream_change(value)
        self._current_kodi_video_stream = value

    @property
    def current_kodi_audio_stream(self):
        return self._current_kodi_audio_stream

    @current_kodi_audio_stream.setter
    def current_kodi_audio_stream(self, value):
        if value != self._current_kodi_audio_stream:
            self.on_kodi_audio_stream_change(value)
        self._current_kodi_audio_stream = value

    @property
    def current_kodi_sub_stream_enabled(self):
        return self._current_kodi_sub_stream_enabled

    @current_kodi_sub_stream_enabled.setter
    def current_kodi_sub_stream_enabled(self, value):
        if value != self._current_kodi_sub_stream_enabled:
            self.on_kodi_subtitle_stream_change(self.current_kodi_sub_stream,
                                                value)
        self._current_kodi_sub_stream_enabled = value

    @property
    def current_kodi_sub_stream(self):
        return self._current_kodi_sub_stream

    @current_kodi_sub_stream.setter
    def current_kodi_sub_stream(self, value):
        if value != self._current_kodi_sub_stream:
            self.on_kodi_subtitle_stream_change(value,
                                                self.current_kodi_sub_stream_enabled)
        self._current_kodi_sub_stream = value

    @property
    def current_plex_video_stream(self):
        return self.plex_stream_index(self.current_kodi_video_stream, 'video')

    @property
    def current_plex_audio_stream(self):
        return self.plex_stream_index(self.current_kodi_audio_stream, 'audio')

    @property
    def current_plex_sub_stream(self):
        return self.plex_stream_index(self.current_kodi_sub_stream, 'subtitle')

    def __repr__(self):
        return ("{{"
                "'id': {self.id}, "
                "'plex_id': {self.plex_id}, "
                "'plex_type': '{self.plex_type}', "
                "'kodi_id': {self.kodi_id}, "
                "'kodi_type': '{self.kodi_type}', "
                "'file': '{self.file}', "
                "'guid': '{self.guid}', "
                "'playmethod': '{self.playmethod}', "
                "'playcount': {self.playcount}, "
                "'resume': {self.resume},"
                "'offset': {self.offset}, "
                "'force_transcode': {self.force_transcode}, "
                "'part': {self.part}}}".format(self=self))

    def _process_streams(self):
        """
        Builds audio and subtitle streams and enables matching between Plex
        and Kodi using self.audio_streams and self.subtitle_streams
        """
        # The playqueue response from the PMS does not contain a stream filename
        # thanks Plex
        self._subtitle_streams = accessible_plex_subtitles(
            self.playmethod,
            self.file,
            self.api.plex_media_streams())
        # Audio streams are much easier - they're always available and sorted
        # the same in Kodi and Plex
        self._audio_streams = [x for x in self.api.plex_media_streams()
                               if x.get('streamType') == '2']
        # Same for video streams
        self._video_streams = [x for x in self.api.plex_media_streams()
                               if x.get('streamType') == '1']
        if len(self._video_streams) == 1:
            # Add a selected = "1" attribute to let our logic stand!
            # Missing if there is only 1 video stream present
            self._video_streams[0].set('selected', '1')
        self._streams_have_been_processed = True

    def _get_iterator(self, stream_type):
        if stream_type == 'audio':
            return self.audio_streams
        elif stream_type == 'subtitle':
            return self.subtitle_streams
        elif stream_type == 'video':
            return self.video_streams

    def _current_index(self, stream_type):
        """
        Kodi might tell us the wrong index for any stream after playback start
        Get the correct one!
        """
        function = {
            'audio': js.get_current_audio_stream_index,
            'video': js.get_current_video_stream_index,
            'subtitle': js.get_current_subtitle_stream_index
        }[stream_type]
        i = 0
        while i < 30:
            # Really annoying: Kodi might return wrong results directly after
            # playback startup, e.g. a Kodi audio index of 1953718901 (!)
            try:
                index = function(self.playerid)
            except (TypeError, IndexError, KeyError):
                # No sensible reply yet
                pass
            else:
                if index != 1953718901:
                    # Correct result!
                    return index
            i += 1
            app.APP.monitor.waitForAbort(0.1)
        else:
            raise RuntimeError('Kodi did not tell us the correct index for %s'
                               % stream_type)

    def init_streams(self):
        """
        Initializes all streams after Kodi has started playing this video
        WARNING: KODI TAKES FOREVER TO INITIALIZE STREAMS AFTER PLAYBACK
        STARTUP. YOU WONT GET THE CORRECT NUMBER OFAUDIO AND SUB STREAMS RIGHT
        AFTER STARTUP. Seems like you need to wait a couple of seconds
        """
        if not app.PLAYSTATE.item == self:
            # Already stopped playback or skipped to the next one
            LOG.warn('Skipping init_streams!')
            return
        self.init_kodi_streams()
        self.switch_to_plex_stream('video')
        if utils.settings('audioStreamPick') == '0':
            self.switch_to_plex_stream('audio')
        if utils.settings('subtitleStreamPick') == '0':
            self.switch_to_plex_stream('subtitle')
        self.streams_initialized = True
        LOG.debug('Successfully initialized streams')

    def init_kodi_streams(self):
        self._current_kodi_video_stream = self._current_index('video')
        self._current_kodi_audio_stream = self._current_index('audio')
        self._current_kodi_sub_stream_enabled = js.get_subtitle_enabled(self.playerid)
        self._current_kodi_sub_stream = self._current_index('subtitle')

    def plex_stream_index(self, kodi_stream_index, stream_type):
        """
        Pass in the kodi_stream_index [int] in order to receive the Plex stream
        index [int].
            stream_type:    'video', 'audio', 'subtitle'
        """
        if stream_type == 'audio':
            return int(self.audio_streams[kodi_stream_index].get('id'))
        elif stream_type == 'video':
            return int(self.video_streams[kodi_stream_index].get('id'))
        elif stream_type == 'subtitle':
            if self.current_kodi_sub_stream_enabled:
                try:
                    return int(self.subtitle_streams[kodi_stream_index].get('id'))
                except (IndexError, TypeError):
                    # A subtitle that is not available on the Plex side
                    # deactivating subs
                    return 0
            else:
                return 0

    def kodi_stream_index(self, plex_stream_index, stream_type):
        """
        Pass in the plex_stream_index [int] in order to receive the Kodi stream
        index [int].
            stream_type:    'video', 'audio', 'subtitle'
        Raises ValueError if unsuccessful
        """
        if not isinstance(plex_stream_index, int):
            raise ValueError('%s plex_stream_index %s of type %s received' %
                             (stream_type, plex_stream_index, type(plex_stream_index)))
        for i, stream in enumerate(self._get_iterator(stream_type)):
            if cast(int, stream.get('id')) == plex_stream_index:
                return i
        raise ValueError('No %s kodi_stream_index for plex_stream_index %s' %
                         (stream_type, plex_stream_index))

    def active_plex_stream_index(self, stream_type):
        """
        Returns the following tuple for the active stream on the Plex side:
            (Plex stream id [int], languageTag [str] or None)
        Returns None if no stream has been selected
        """
        for i, stream in enumerate(self._get_iterator(stream_type)):
            if stream.get('selected') == '1':
                return (int(stream.get('id')), stream.get('languageTag'))

    def on_kodi_subtitle_stream_change(self, kodi_stream_index, subs_enabled):
        """
        Call this method if Kodi changed its subtitle and you want Plex to
        know.
        """
        if subs_enabled:
            try:
                plex_stream_index = int(self.subtitle_streams[kodi_stream_index].get('id'))
            except (IndexError, TypeError):
                LOG.debug('Kodi subtitle change detected to a sub %s that is '
                          'NOT available on the Plex side', kodi_stream_index)
                plex_stream_index = 0
            else:
                LOG.debug('Kodi subtitle change detected: telling Plex about '
                          'switch to index %s, Plex stream id %s',
                          kodi_stream_index, plex_stream_index)
        else:
            plex_stream_index = 0
            LOG.debug('Kodi subtitle has been deactivated, telling Plex')
        PF.change_subtitle(plex_stream_index, self.api.part_id())

    def on_kodi_audio_stream_change(self, kodi_stream_index):
        """
        Call this method if Kodi changed its audio stream and you want Plex to
        know. kodi_stream_index [int]
        """
        plex_stream_index = int(self.audio_streams[kodi_stream_index].get('id'))
        LOG.debug('Changing Plex audio stream to %s, Kodi index %s',
                  plex_stream_index, kodi_stream_index)
        PF.change_audio_stream(plex_stream_index, self.api.part_id())

    def on_kodi_video_stream_change(self, kodi_stream_index):
        """
        Call this method if Kodi changed its video stream and you want Plex to
        know. kodi_stream_index [int]
        """
        plex_stream_index = int(self.video_streams[kodi_stream_index].get('id'))
        LOG.debug('Changing Plex video stream to %s, Kodi index %s',
                  plex_stream_index, kodi_stream_index)
        PF.change_video_stream(plex_stream_index, self.api.part_id())

    def _set_kodi_stream_if_different(self, kodi_index, typus):
        """Will always activate subtitles."""
        if typus == 'video':
            current = js.get_current_video_stream_index(self.playerid)
            if current != kodi_index:
                LOG.debug('Switching video stream')
                app.APP.player.setVideoStream(kodi_index)
            else:
                LOG.debug('Not switching video stream (no change)')
        elif typus == 'audio':
            current = js.get_current_audio_stream_index(self.playerid)
            if current != kodi_index:
                LOG.debug('Switching audio stream')
                app.APP.player.setAudioStream(kodi_index)
            else:
                LOG.debug('Not switching audio stream (no change)')
        elif typus == 'subtitle':
            current = js.get_current_subtitle_stream_index(self.playerid)
            enabled = js.get_subtitle_enabled(self.playerid)
            if current != kodi_index:
                LOG.debug('Switching subtitle stream')
                app.APP.player.setSubtitleStream(kodi_index)
            else:
                LOG.debug('Not switching subtitle stream (no change)')
            if not enabled:
                LOG.debug('Enabling subtitles')
                app.APP.player.showSubtitles(True)
        else:
            raise RuntimeError('Unknown stream type %s' % typus)

    def switch_to_plex_stream(self, typus):
        try:
            plex_index, language_tag = self.active_plex_stream_index(typus)
        except TypeError:
            # Only happens if Plex did not provide us with a suitable sub
            # Meaning Plex tells us to deactivate subs
            LOG.debug('Deactivating Kodi subtitles because the PMS '
                      'told us to not show any subtitles')
            app.APP.player.showSubtitles(False)
            self._current_kodi_sub_stream_enabled = False
            return
        # Rest: video, audio and activated subs
        LOG.debug('The PMS wants to display %s stream with Plex id %s and '
                  'languageTag %s', typus, plex_index, language_tag)
        try:
            kodi_index = self.kodi_stream_index(plex_index, typus)
        except ValueError:
            kodi_index = None
            LOG.debug('Leaving Kodi %s stream settings untouched since we '
                      'could not parse Plex %s stream with id %s to a Kodi'
                      ' index', typus, typus, plex_index)
        else:
            LOG.debug('Switching to Kodi %s stream number %s because the '
                      'PMS told us to show stream with Plex id %s',
                      typus, kodi_index, plex_index)
            # If we're choosing an "illegal" index, this function does
            # need seem to fail nor log any errors
            self._set_kodi_stream_if_different(kodi_index, typus)
        if typus == 'audio':
            self._current_kodi_audio_stream = kodi_index
        elif typus == 'subtitle':
            self._current_kodi_sub_stream_enabled = True
            self._current_kodi_sub_stream = kodi_index
        elif typus == 'video':
            self._current_kodi_video_stream = kodi_index

    def on_plex_stream_change(self, video_stream_id=None, audio_stream_id=None,
                              subtitle_stream_id=None):
        """
        Call this method if Plex Companion wants to change streams [ints]
        """
        if video_stream_id is not None:
            try:
                kodi_index = self.kodi_stream_index(video_stream_id, 'video')
            except ValueError:
                LOG.error('Unexpected Plex video_stream_id %s, not changing '
                          'the video stream!', video_stream_id)
                return
            self._set_kodi_stream_if_different(kodi_index, 'video')
            self._current_kodi_video_stream = kodi_index
        if audio_stream_id is not None:
            try:
                kodi_index = self.kodi_stream_index(audio_stream_id, 'audio')
            except ValueError:
                LOG.error('Unexpected Plex audio_stream_id %s, not changing '
                          'the video stream!', audio_stream_id)
                return
            self._set_kodi_stream_if_different(kodi_index, 'audio')
            self._current_kodi_audio_stream = kodi_index
        if subtitle_stream_id is not None:
            if subtitle_stream_id == 0:
                app.APP.player.showSubtitles(False)
                self._current_kodi_sub_stream_enabled = False
            else:
                try:
                    kodi_index = self.kodi_stream_index(subtitle_stream_id,
                                                        'subtitle')
                except ValueError:
                    LOG.debug('The PMS wanted to change subs, but we could not'
                              ' match the sub with id %s to a Kodi sub',
                              subtitle_stream_id)
                else:
                    app.APP.player.setSubtitleStream(kodi_index)
                    app.APP.player.showSubtitles(True)
                    self._current_kodi_sub_stream_enabled = True
                    self._current_kodi_sub_stream = kodi_index


def playlist_item_from_kodi(kodi_item):
    """
    Turns the JSON answer from Kodi into a playlist element

    Supply with data['item'] as returned from Kodi JSON-RPC interface.
    kodi_item dict contains keys 'id', 'type', 'file' (if applicable)
    """
    item = PlaylistItem()
    item.kodi_id = kodi_item.get('id')
    item.kodi_type = kodi_item.get('type')
    item.file = kodi_item.get('file')
    if item.kodi_id:
        with PlexDB(lock=False) as plexdb:
            db_item = plexdb.item_by_kodi_id(kodi_item['id'], kodi_item['type'])
        if db_item:
            item.plex_id = db_item['plex_id']
            item.plex_type = db_item['plex_type']
    if item.plex_id is None and item.file is not None:
        try:
            query = item.file.split('?', 1)[1]
        except IndexError:
            query = ''
        query = dict(utils.parse_qsl(query))
        item.plex_id = cast(int, query.get('plex_id'))
        item.plex_type = query.get('itemType')
    LOG.debug('Made playlist item from Kodi: %s', item)
    return item


def verify_kodi_item(plex_id, kodi_item):
    """
    Tries to lookup kodi_id and kodi_type for kodi_item (with kodi_item['file']
    supplied) - if and only if plex_id is None.

    Returns the kodi_item with kodi_item['id'] and kodi_item['type'] possibly
    set to None if unsuccessful.

    Will raise a PlaylistError if plex_id is None and kodi_item['file'] starts
    with either 'plugin' or 'http'.
    Will raise KeyError if neither plex_id nor kodi_id are found
    """
    if plex_id is not None or kodi_item.get('id') is not None:
        # Got all the info we need
        return kodi_item
    # Special case playlist startup - got type but no id
    if (not app.SYNC.direct_paths and app.SYNC.enable_music and
            kodi_item.get('type') == v.KODI_TYPE_SONG and
            kodi_item['file'].startswith('http')):
        kodi_item['id'], _ = kodiid_from_filename(kodi_item['file'],
                                                  v.KODI_TYPE_SONG)
        LOG.debug('Detected song. Research results: %s', kodi_item)
        return kodi_item
    # Need more info since we don't have kodi_id nor type. Use file path.
    if ((kodi_item['file'].startswith('plugin') and
         not kodi_item['file'].startswith('plugin://%s' % v.ADDON_ID)) or
            kodi_item['file'].startswith('http')):
        LOG.debug('kodi_item cannot be used for Plex playback: %s', kodi_item)
        raise PlaylistError('kodi_item cannot be used for Plex playback')
    LOG.debug('Starting research for Kodi id since we didnt get one: %s',
              kodi_item)
    # Try the VIDEO DB first - will find both movies and episodes
    kodi_id, kodi_type = kodiid_from_filename(kodi_item['file'],
                                              db_type='video')
    if not kodi_id:
        # No movie or episode found - try MUSIC DB now for songs
        kodi_id, kodi_type = kodiid_from_filename(kodi_item['file'],
                                                  db_type='music')
    kodi_item['id'] = kodi_id
    kodi_item['type'] = None if kodi_id is None else kodi_type
    if plex_id is None and kodi_id is None:
        raise KeyError('Neither Plex nor Kodi id found for %s' % kodi_item)
    LOG.debug('Research results for kodi_item: %s', kodi_item)
    return kodi_item


def playlist_item_from_plex(plex_id):
    """
    Returns a playlist element providing the plex_id ("ratingKey")

    Returns a Playlist_Item
    """
    item = PlaylistItem()
    item.plex_id = plex_id
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_id(plex_id)
    if db_item:
        item.plex_type = db_item['plex_type']
        item.kodi_id = db_item['kodi_id']
        item.kodi_type = db_item['kodi_type']
    else:
        raise KeyError('Could not find plex_id %s in database' % plex_id)
    LOG.debug('Made playlist item from plex: %s', item)
    return item


def playlist_item_from_xml(xml_video_element, kodi_id=None, kodi_type=None):
    """
    Returns a playlist element for the playqueue using the Plex xml

    xml_video_element: etree xml piece 1 level underneath <MediaContainer>
    """
    item = PlaylistItem()
    api = API(xml_video_element)
    item.plex_id = api.plex_id
    item.plex_type = api.plex_type
    # item.id will only be set if you passed in an xml_video_element from e.g.
    # a playQueue
    item.id = api.item_id()
    if kodi_id is not None:
        item.kodi_id = kodi_id
        item.kodi_type = kodi_type
    elif item.plex_type != v.PLEX_TYPE_CLIP:
        with PlexDB(lock=False) as plexdb:
            db_element = plexdb.item_by_id(item.plex_id,
                                           plex_type=item.plex_type)
        if db_element:
            item.kodi_id = db_element['kodi_id']
            item.kodi_type = db_element['kodi_type']
    item.guid = api.guid_html_escaped()
    item.playcount = api.viewcount()
    item.offset = api.resume_point()
    item.api = api
    LOG.debug('Created new playlist item from xml: %s', item)
    return item


def _update_playlist_version(playlist, xml):
    """
    Takes a PMS xml (one level above the xml-depth where we're usually applying
    API()) as input to overwrite the playlist version (e.g. Plex
    playQueueVersion).

    Raises PlaylistError if unsuccessful
    """
    try:
        playlist.version = int(xml.get('%sVersion' % playlist.kind))
    except (AttributeError, TypeError):
        raise PlaylistError('Could not get new playlist Version for playlist '
                            '%s' % playlist)


def get_playlist_details_from_xml(playlist, xml):
    """
    Takes a PMS xml as input and overwrites all the playlist's details, e.g.
    playlist.id with the XML's playQueueID

    Raises PlaylistError if something went wrong.
    """
    if xml is None:
        raise PlaylistError('No playlist received for playlist %s' % playlist)
    playlist.id = cast(int, xml.get('%sID' % playlist.kind))
    playlist.version = cast(int, xml.get('%sVersion' % playlist.kind))
    playlist.shuffled = cast(int, xml.get('%sShuffled' % playlist.kind))
    playlist.selectedItemID = cast(int, xml.get('%sSelectedItemID'
                                                % playlist.kind))
    playlist.selectedItemOffset = cast(int, xml.get('%sSelectedItemOffset'
                                                    % playlist.kind))
    LOG.debug('Updated playlist from xml: %s', playlist)


def update_playlist_from_PMS(playlist, playlist_id=None, xml=None):
    """
    Updates Kodi playlist using a new PMS playlist. Pass in playlist_id if we
    need to fetch a new playqueue

    If an xml is passed in, the playlist will be overwritten with its info

    Raises PlaylistError if something went wront
    """
    if xml is None:
        xml = get_PMS_playlist(playlist, playlist_id)
    # Clear our existing playlist and the associated Kodi playlist
    playlist.clear()
    # Set new values
    get_playlist_details_from_xml(playlist, xml)
    for plex_item in xml:
        playlist_item = add_to_Kodi_playlist(playlist, plex_item)
        if playlist_item is not None:
            playlist.items.append(playlist_item)


def init_plex_playqueue(playlist, plex_id=None, kodi_item=None):
    """
    Initializes the Plex side without changing the Kodi playlists
    WILL ALSO UPDATE OUR PLAYLISTS.

    Returns the first PKC playlist item or raises PlaylistError
    """
    LOG.debug('init_plex_playqueue: Entered. Playlist: %s, plex_id: %s, kodi_item: %s', playlist, plex_id, kodi_item)
    # LOG.debug('Initializing the playqueue on the Plex side: %s', playlist) # Original log, seems redundant with the one above
    kodi_item = kodi_item or {}
    verify_kodi_item(plex_id, kodi_item)
    playlist.clear(kodi=False)
    try:
        if plex_id:
            item = playlist_item_from_plex(plex_id)
        else:
            item = playlist_item_from_kodi(kodi_item)
        params = {
            'next': 0,
            'type': playlist.type,
            'uri': item.uri,
            'includeMarkers': 1,  # e.g. start + stop of intros
        }
        xml = DU().downloadUrl(url="{server}/%ss" % playlist.kind,
                               action_type="POST",
                               parameters=params)
        if xml in (None, 401):
            raise PlaylistError('Did not receive a valid xml from the PMS')
        get_playlist_details_from_xml(playlist, xml)
        # Need to get the details for the playlist item
        item = playlist_item_from_xml(xml[0],
                                      kodi_id=kodi_item.get('id'),
                                      kodi_type=kodi_item.get('type'))
        item.file = kodi_item.get('file')
    except (KeyError, IndexError, TypeError):
        LOG.error('Could not init Plex playlist: plex_id %s, kodi_item %s',
                  plex_id, kodi_item)
        raise PlaylistError
    playlist.items.append(item)
    LOG.debug("init_plex_playqueue: Adding item with plex_id %s to playqueue %s. Item details: %s", item.plex_id if item else "N/A", playlist.id if playlist else "N/A", item)
    # LOG.debug('Initialized the playqueue on the Plex side: %s', playlist) # Original log
    LOG.debug('init_plex_playqueue: About to return. Playlist: %s, Item: %s', playlist, item)
    return item


def add_listitem_to_playlist(playlist, pos, listitem, kodi_id=None,
                             kodi_type=None, plex_id=None, file=None):
    """
    Adds a listitem to both the Kodi and Plex playlist at position pos [int].

    If file is not None, file will overrule kodi_id!

    file: str!!
    """
    LOG.debug('add_listitem_to_playlist at position %s. Playlist before add: '
              '%s', pos, playlist)
    kodi_item = {'id': kodi_id, 'type': kodi_type, 'file': file}
    if playlist.id is None:
        init_plex_playqueue(playlist, plex_id, kodi_item)
    else:
        add_item_to_plex_playqueue(playlist, pos, plex_id, kodi_item)
    if kodi_id is None and playlist.items[pos].kodi_id:
        kodi_id = playlist.items[pos].kodi_id
        kodi_type = playlist.items[pos].kodi_type
    if file is None:
        file = playlist.items[pos].file
    # Otherwise we double the item!
    del playlist.items[pos]
    kodi_item = {'id': kodi_id, 'type': kodi_type, 'file': file}
    add_listitem_to_Kodi_playlist(playlist,
                                  pos,
                                  listitem,
                                  file,
                                  kodi_item=kodi_item)


def add_item_to_playlist(playlist, pos, kodi_id=None, kodi_type=None,
                         plex_id=None, file=None):
    """
    Adds an item to BOTH the Kodi and Plex playlist at position pos [int]
        file: str!

    Raises PlaylistError if something went wrong
    """
    LOG.debug('add_item_to_playlist. Playlist before adding: %s', playlist)
    kodi_item = {'id': kodi_id, 'type': kodi_type, 'file': file}
    if playlist.id is None:
        item = init_plex_playqueue(playlist, plex_id, kodi_item)
    else:
        item = add_item_to_plex_playqueue(playlist, pos, plex_id, kodi_item)
    params = {
        'playlistid': playlist.playlistid,
        'position': pos
    }
    if item.kodi_id is not None:
        params['item'] = {'%sid' % item.kodi_type: int(item.kodi_id)}
    else:
        params['item'] = {'file': item.file}
    reply = js.playlist_insert(params)
    if reply.get('error') is not None:
        raise PlaylistError('Could not add item to playlist. Kodi reply. %s'
                            % reply)
    return item


def add_item_to_plex_playqueue(playlist, pos, plex_id=None, kodi_item=None):
    """
    Adds a new item to the playlist at position pos [int] only on the Plex
    side of things (e.g. because the user changed the Kodi side)
    WILL ALSO UPDATE OUR PLAYLISTS

    Returns the PKC PlayList item or raises PlaylistError
    """
    verify_kodi_item(plex_id, kodi_item)
    if plex_id:
        item = playlist_item_from_plex(plex_id)
    else:
        item = playlist_item_from_kodi(kodi_item)
    url = "{server}/%ss/%s" % (playlist.kind, playlist.id)
    parameters = {
        'uri': item.uri,
        'includeMarkers': 1,  # e.g. start + stop of intros
    }
    # Will always put the new item at the end of the Plex playlist
    xml = DU().downloadUrl(url,
                           action_type="PUT",
                           parameters=parameters)
    try:
        xml[-1].attrib
    except (TypeError, AttributeError, KeyError, IndexError):
        raise PlaylistError('Could not add item %s to playlist %s'
                            % (kodi_item, playlist))
    api = API(xml[-1])
    item.api = api
    item.id = api.item_id()
    item.guid = api.guid_html_escaped()
    item.offset = api.resume_point()
    item.playcount = api.viewcount()
    playlist.items.append(item)
    if pos == len(playlist.items) - 1:
        # Item was added at the end
        _update_playlist_version(playlist, xml)
    else:
        # Move the new item to the correct position
        move_playlist_item(playlist,
                           len(playlist.items) - 1,
                           pos)
    LOG.debug('Successfully added item on the Plex side: %s', playlist)
    return item


def add_item_to_kodi_playlist(playlist, pos, kodi_id=None, kodi_type=None,
                              file=None, xml_video_element=None):
    """
    Adds an item to the KODI playlist only. WILL ALSO UPDATE OUR PLAYLISTS

    Returns the playlist item that was just added or raises PlaylistError

    file: str!
    """
    LOG.debug('Adding new item kodi_id: %s, kodi_type: %s, file: %s to Kodi '
              'only at position %s for %s',
              kodi_id, kodi_type, file, pos, playlist)
    params = {
        'playlistid': playlist.playlistid,
        'position': pos
    }
    if kodi_id is not None:
        params['item'] = {'%sid' % kodi_type: int(kodi_id)}
    else:
        params['item'] = {'file': file}
    reply = js.playlist_insert(params)
    if reply.get('error') is not None:
        raise PlaylistError('Could not add item to playlist. Kodi reply. %s',
                            reply)
    if xml_video_element is not None:
        item = playlist_item_from_xml(xml_video_element)
        item.kodi_id = kodi_id
        item.kodi_type = kodi_type
        item.file = file
    elif kodi_id is not None:
        item = playlist_item_from_kodi(
            {'id': kodi_id, 'type': kodi_type, 'file': file})
        if item.plex_id is not None:
            xml = PF.GetPlexMetadata(item.plex_id)
            item.api = API(xml[-1])
    playlist.items.insert(pos, item)
    return item


def move_playlist_item(playlist, before_pos, after_pos):
    """
    Moves playlist item from before_pos [int] to after_pos [int] for Plex only.

    WILL ALSO CHANGE OUR PLAYLISTS.
    """
    LOG.debug('Moving item from %s to %s on the Plex side for %s',
              before_pos, after_pos, playlist)
    if after_pos == 0:
        url = "{server}/%ss/%s/items/%s/move?after=0" % \
              (playlist.kind,
               playlist.id,
               playlist.items[before_pos].id)
    else:
        url = "{server}/%ss/%s/items/%s/move?after=%s" % \
              (playlist.kind,
               playlist.id,
               playlist.items[before_pos].id,
               playlist.items[after_pos - 1].id)
    # Tell the PMS that we're moving items around
    xml = DU().downloadUrl(url, action_type="PUT")
    # We need to increment the playlist version for communicating with the PMS
    _update_playlist_version(playlist, xml)
    # Move our item's position in our internal playlist
    playlist.items.insert(after_pos, playlist.items.pop(before_pos))
    LOG.debug('Done moving for %s', playlist)


def get_PMS_playlist(playlist, playlist_id=None):
    """
    Fetches the PMS playlist/playqueue as an XML. Pass in playlist_id if we
    need to fetch a new playlist

    Raises PlaylistError if something went wrong
    """
    playlist_id = playlist_id if playlist_id else playlist.id
    parameters = {'includeMarkers': 1}
    if playlist.kind == 'playList':
        xml = DU().downloadUrl("{server}/playlists/%s/items" % playlist_id,
                               parameters=parameters)
    else:
        xml = DU().downloadUrl("{server}/playQueues/%s" % playlist_id,
                               parameters=parameters)
    try:
        xml.attrib
    except AttributeError:
        raise PlaylistError('Did not get a valid xml')
    return xml


def refresh_playlist_from_PMS(playlist):
    """
    Only updates the selected item from the PMS side (e.g.
    playQueueSelectedItemID). Will NOT check whether items still make sense.
    """
    get_playlist_details_from_xml(playlist, get_PMS_playlist(playlist))


def delete_playlist_item_from_PMS(playlist, pos):
    """
    Delete the item at position pos [int] on the Plex side and our playlists
    """
    LOG.debug('Deleting position %s for %s on the Plex side', pos, playlist)
    try:
        xml = DU().downloadUrl("{server}/%ss/%s/items/%s?repeat=%s" %
                               (playlist.kind,
                                playlist.id,
                                playlist.items[pos].id,
                                playlist.repeat),
                               action_type="DELETE")
    except IndexError:
        raise PlaylistError('Position %s out of bound for %s' % (pos, playlist))
    else:
        del playlist.items[pos]
        _update_playlist_version(playlist, xml)


# Functions operating on the Kodi playlist objects ##########

def add_to_Kodi_playlist(playlist, xml_video_element):
    """
    Adds a new item to the Kodi playlist via JSON (at the end of the playlist).
    Pass in the PMS xml's video element (one level underneath MediaContainer).

    Returns a Playlist_Item or raises PlaylistError
    """
    item = playlist_item_from_xml(xml_video_element)
    if item.kodi_id:
        json_item = {'%sid' % item.kodi_type: item.kodi_id}
    else:
        json_item = {'file': item.file}
    reply = js.playlist_add(playlist.playlistid, json_item)
    if reply.get('error') is not None:
        raise PlaylistError('Could not add item %s to Kodi playlist. Error: '
                            '%s', xml_video_element, reply)
    return item


def add_listitem_to_Kodi_playlist(playlist, pos, listitem, file,
                                  xml_video_element=None, kodi_item=None):
    """
    Adds an xbmc listitem to the Kodi playlist.xml_video_element

    WILL NOT UPDATE THE PLEX SIDE, BUT WILL UPDATE OUR PLAYLISTS

    file: string!
    """
    LOG.debug('Insert listitem at position %s for Kodi only for %s',
              pos, playlist)
    # Add the item into Kodi playlist
    playlist.kodi_pl.add(url=file, listitem=listitem, index=pos)
    # We need to add this to our internal queue as well
    if xml_video_element is not None:
        item = playlist_item_from_xml(xml_video_element)
    else:
        item = playlist_item_from_kodi(kodi_item)
    if file is not None:
        item.file = file
    playlist.items.insert(pos, item)
    LOG.debug('Done inserting for %s', playlist)
    return item


def remove_from_kodi_playlist(playlist, pos):
    """
    Removes the item at position pos from the Kodi playlist using JSON.

    WILL NOT UPDATE THE PLEX SIDE, BUT WILL UPDATE OUR PLAYLISTS
    """
    LOG.debug('Removing position %s from Kodi only from %s', pos, playlist)
    reply = js.playlist_remove(playlist.playlistid, pos)
    if reply.get('error') is not None:
        LOG.error('Could not delete the item from the playlist. Error: %s',
                  reply)
        return
    try:
        del playlist.items[pos]
    except IndexError:
        LOG.error('Cannot delete position %s for %s', pos, playlist)


def get_pms_playqueue(playqueue_id):
    """
    Returns the Plex playqueue as an etree XML or None if unsuccessful
    """
    parameters = {'includeMarkers': 1}
    xml = DU().downloadUrl("{server}/playQueues/%s" % playqueue_id,
                           parameters=parameters,
                           headerOptions={'Accept': 'application/xml'})
    try:
        xml.attrib
    except AttributeError:
        LOG.error('Could not download Plex playqueue %s', playqueue_id)
        xml = None
    return xml


def get_plextype_from_xml(xml):
    """
    Needed if PMS returns an empty playqueue. Will get the Plex type from the
    empty playlist playQueueSourceURI. Feed with (empty) etree xml

    returns None if unsuccessful
    """
    try:
        plex_id = utils.REGEX_PLEX_ID_FROM_URL.findall(
            xml.attrib['playQueueSourceURI'])[0]
    except IndexError:
        LOG.error('Could not get plex_id from xml: %s', xml.attrib)
        return
    new_xml = PF.GetPlexMetadata(plex_id)
    try:
        new_xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        LOG.error('Could not get plex metadata for plex id %s', plex_id)
        return
    return new_xml[0].attrib.get('type')
