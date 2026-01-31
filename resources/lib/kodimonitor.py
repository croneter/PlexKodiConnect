#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PKC Kodi Monitoring implementation
"""
from logging import getLogger
from json import loads
import copy

import xbmc

from .plex_api import API
from .plex_db import PlexDB
from .kodi_db import KodiVideoDB
from . import kodi_db
from .downloadutils import DownloadUtils as DU
from . import utils, timing, plex_functions as PF
from . import json_rpc as js, playlist_func as PL
from . import backgroundthread, app, variables as v
from . import exceptions
from . import upnext

LOG = getLogger('PLEX.kodimonitor')

WAIT_BEFORE_INIT_STREAMS = 6
ADDITIONAL_WAIT_BEFORE_INIT_STREAMS = 10


class KodiMonitor(xbmc.Monitor):
    """
    PKC implementation of the Kodi Monitor class. Invoke only once.
    """

    def __init__(self):
        self._already_slept = False
        xbmc.Monitor.__init__(self)
        for playerid in app.PLAYSTATE.player_states:
            app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
        LOG.info("Kodi monitor started.")

    def onScanStarted(self, library):
        """
        Will be called when Kodi starts scanning the library
        """
        LOG.debug("Kodi library scan %s running.", library)

    def onScanFinished(self, library):
        """
        Will be called when Kodi finished scanning the library
        """
        LOG.debug("Kodi library scan %s finished.", library)

    def onSettingsChanged(self):
        """
        Monitor the PKC settings for changes made by the user
        """
        LOG.debug('PKC settings change detected')

    def onNotification(self, sender, method, data):
        """
        Called when a bunch of different stuff happens on the Kodi side
        """
        if data:
            data = loads(data)
            LOG.debug("Method: %s Data: %s", method, data)

        if method == "Player.OnPlay":
            with app.APP.lock_playqueues:
                self.PlayBackStart(data)
        elif method == 'Player.OnAVChange':
            with app.APP.lock_playqueues:
                self._on_av_change(data)
        elif method == "Player.OnStop":
            with app.APP.lock_playqueues:
                _playback_cleanup(ended=data.get('end'))
        elif method == 'Playlist.OnAdd':
            if 'item' in data and data['item'].get('type') == v.KODI_TYPE_SHOW:
                # Hitting the "browse" button on tv show info dialog
                # Hence show the tv show directly
                xbmc.executebuiltin("Dialog.Close(all, true)")
                js.activate_window('videos',
                                   'videodb://tvshows/titles/%s/' % data['item']['id'])
            with app.APP.lock_playqueues:
                self._playlist_onadd(data)
        elif method == 'Playlist.OnRemove':
            self._playlist_onremove(data)
        elif method == 'Playlist.OnClear':
            with app.APP.lock_playqueues:
                self._playlist_onclear(data)
        elif method == "VideoLibrary.OnUpdate":
            with app.APP.lock_playqueues:
                _videolibrary_onupdate(data)
        elif method == "VideoLibrary.OnRemove":
            pass
        elif method == "System.OnSleep":
            # Connection is going to sleep
            LOG.info("Marking the server as offline. SystemOnSleep activated.")
        elif method == "System.OnWake":
            # Allow network to wake up
            self.waitForAbort(10)
            app.CONN.online = False
        elif method == "GUI.OnScreensaverDeactivated":
            if utils.settings('dbSyncScreensaver') == "true":
                self.waitForAbort(5)
                app.SYNC.run_lib_scan = 'full'
        elif method == "System.OnQuit":
            LOG.info('Kodi OnQuit detected - shutting down')
            app.APP.stop_pkc = True

    def _playlist_onadd(self, data):
        """
        Called if an item is added to a Kodi playlist. Example data dict:
        {
            u'item': {
                u'type': u'movie',
                u'id': 2},
            u'playlistid': 1,
            u'position': 0
        }
        Will NOT be called if playback initiated by Kodi widgets
        """
        pass

    def _playlist_onremove(self, data):
        """
        Called if an item is removed from a Kodi playlist. Example data dict:
        {
            u'playlistid': 1,
            u'position': 0
        }
        """
        pass

    @staticmethod
    def _playlist_onclear(data):
        """
        Called if a Kodi playlist is cleared. Example data dict:
        {
            u'playlistid': 1,
        }
        """
        playqueue = app.PLAYQUEUES[data['playlistid']]
        if not playqueue.is_pkc_clear():
            playqueue.pkc_edit = True
            playqueue.clear(kodi=False)
        else:
            LOG.debug('Detected PKC clear - ignoring')

    @staticmethod
    def _get_ids(kodi_id, kodi_type, path):
        """
        Returns the tuple (plex_id, plex_type) or (None, None)
        """
        # No Kodi id returned by Kodi, even if there is one. Ex: Widgets
        plex_id = None
        plex_type = None
        # If using direct paths and starting playback from a widget
        if not kodi_id and kodi_type and path:
            kodi_id, _ = kodi_db.kodiid_from_filename(path, kodi_type)
        if kodi_id:
            with PlexDB(lock=False) as plexdb:
                db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
            if db_item:
                plex_id = db_item['plex_id']
                plex_type = db_item['plex_type']
        return plex_id, plex_type

    @staticmethod
    def _add_remaining_items_to_playlist(playqueue):
        """
        Adds all but the very first item of the Kodi playlist to the Plex
        playqueue
        """
        items = js.playlist_get_items(playqueue.playlistid)
        if not items:
            LOG.error('Could not retrieve Kodi playlist items')
            return
        # Remove first item
        items.pop(0)
        try:
            for i, item in enumerate(items):
                PL.add_item_to_plex_playqueue(playqueue, i + 1, kodi_item=item)
        except exceptions.PlaylistError:
            LOG.info('Could not build Plex playlist for: %s', items)

    def _json_item(self, playerid):
        """
        Uses JSON RPC to get the playing item's info and returns the tuple
            kodi_id, kodi_type, path
        or None each time if not found.
        """
        if not self._already_slept:
            # SLEEP before calling this for the first time just after playback
            # start as Kodi updates this info very late!! Might get previous
            # element otherwise
            self._already_slept = True
            self.waitForAbort(1)
        try:
            json_item = js.get_item(playerid)
        except KeyError:
            LOG.debug('No playing item returned by Kodi')
            return None, None, None
        LOG.debug('Kodi playing item properties: %s', json_item)
        return (json_item.get('id'),
                json_item.get('type'),
                json_item.get('file'))

    def PlayBackStart(self, data):
        """
        Called whenever playback is started. Example data:
        {
            u'item': {u'type': u'movie', u'title': u''},
            u'player': {u'playerid': 1, u'speed': 1}
        }
        Unfortunately when using Widgets, Kodi doesn't tell us shit
        """
        self._already_slept = False
        # Get the type of media we're playing
        try:
            playerid = data['player']['playerid']
            kodi_id = data['item'].get('id')
            kodi_type = data['item'].get('type')
            path = data['item'].get('file')
        except (TypeError, KeyError):
            LOG.info('Aborting playback report - item invalid for updates %s',
                     data)
            return
        if data['item'].get('channeltype') == 'tv':
            LOG.info('TV playback detected, aborting Plex playback report')
            return
        if playerid == -1:
            # Kodi might return -1 for "last player"
            # Getting the playerid is really a PITA
            try:
                playerid = js.get_player_ids()[0]
            except IndexError:
                # E.g. Kodi 18 doesn't tell us anything useful
                if kodi_type in v.KODI_VIDEOTYPES:
                    playlist_type = v.KODI_TYPE_VIDEO_PLAYLIST
                elif kodi_type in v.KODI_AUDIOTYPES:
                    playlist_type = v.KODI_TYPE_AUDIO_PLAYLIST
                else:
                    LOG.error('Unexpected type %s, data %s', kodi_type, data)
                    return
                playerid = js.get_playlist_id(playlist_type)
                if not playerid:
                    LOG.error('Coud not get playerid for data %s', data)
                    return
        playqueue = app.PLAYQUEUES[playerid]
        info = js.get_player_props(playerid)
        if playqueue.kodi_playlist_playback:
            # Kodi will tell us the wrong position - of the playlist, not the
            # playqueue, when user starts playing from a playlist :-(
            pos = 0
            LOG.debug('Detected playback from a Kodi playlist')
        else:
            pos = info['position'] if info['position'] != -1 else 0
            LOG.debug('Detected position %s for %s', pos, playqueue)
        status = app.PLAYSTATE.player_states[playerid]
        try:
            item = playqueue.items[pos]
            LOG.debug('PKC playqueue item is: %s', item)
        except IndexError:
            # PKC playqueue not yet initialized
            LOG.debug('Position %s not in PKC playqueue yet', pos)
            initialize = True
        else:
            if not kodi_id:
                kodi_id, kodi_type, path = self._json_item(playerid)
            if kodi_id and item.kodi_id:
                if item.kodi_id != kodi_id or item.kodi_type != kodi_type:
                    LOG.debug('Detected different Kodi id')
                    initialize = True
                else:
                    initialize = False
            else:
                # E.g. clips set-up previously with no Kodi DB entry
                if not path:
                    kodi_id, kodi_type, path = self._json_item(playerid)
                if path == '':
                    LOG.debug('Detected empty path: aborting playback report')
                    return
                if item.file != path:
                    # Clips will get a new path
                    LOG.debug('Detected different path')
                    try:
                        tmp_plex_id = int(utils.REGEX_PLEX_ID.findall(path)[0])
                    except (IndexError, TypeError):
                        LOG.debug('No Plex id in path, need to init playqueue')
                        initialize = True
                    else:
                        if tmp_plex_id == item.plex_id:
                            LOG.debug('Detected different path for the same id')
                            initialize = False
                        else:
                            LOG.debug('Different Plex id, need to init playqueue')
                            initialize = True
                else:
                    initialize = False
        if initialize:
            LOG.debug('Need to initialize Plex and PKC playqueue')
            if not kodi_id or not kodi_type or not path:
                kodi_id, kodi_type, path = self._json_item(playerid)
            plex_id, plex_type = self._get_ids(kodi_id, kodi_type, path)
            if not plex_id:
                LOG.debug('No Plex id obtained - aborting playback report')
                app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                return
            try:
                item = PL.init_plex_playqueue(playqueue, plex_id=plex_id)
            except exceptions.PlaylistError:
                LOG.info('Could not initialize the Plex playlist')
                return
            item.file = path
            # Set the Plex container key (e.g. using the Plex playqueue)
            container_key = None
            if info['playlistid'] != -1:
                # -1 is Kodi's answer if there is no playlist
                container_key = app.PLAYQUEUES[playerid].id
            if container_key is not None:
                container_key = '/playQueues/%s' % container_key
            elif plex_id is not None:
                container_key = '/library/metadata/%s' % plex_id
        else:
            LOG.debug('No need to initialize playqueues')
            kodi_id = item.kodi_id
            kodi_type = item.kodi_type
            plex_id = item.plex_id
            plex_type = item.plex_type
            path = item.file
            if playqueue.id:
                container_key = '/playQueues/%s' % playqueue.id
            else:
                container_key = '/library/metadata/%s' % plex_id
        if plex_type == v.PLEX_TYPE_EPISODE and \
                xbmc.getCondVisibility('System.AddonIsEnabled(service.upnext)'):
            # Let's use Up Next if the add-on is activated REGARDLESS of
            # skipping credits is enabled or not
            upnext_integration = True
        else:
            upnext_integration = False
        # Mechanik for Plex skip intro/credits/commercials feature
        if utils.settings('enableSkipIntro') == 'true' \
                or utils.settings('enableSkipCredits') == 'true' \
                or utils.settings('enableSkipCommercials') == 'true':
            status['markers'] = item.api.markers()
            status['markers_hidden'] = {}
            status['first_credits_marker'] = item.api.first_credits_marker()
            status['final_credits_marker'] = item.api.final_credits_marker()
        # Set credits markers if skip credits OR Up Next is enabled
        # Up Next uses these markers for notification timing
        elif upnext_integration:
            status['first_credits_marker'] = item.api.first_credits_marker()
            status['final_credits_marker'] = item.api.final_credits_marker()

        if item.playmethod is None and path and not path.startswith('plugin://'):
            item.playmethod = v.PLAYBACK_METHOD_DIRECT_PATH
        item.playerid = playerid
        # Remember the currently playing item
        app.PLAYSTATE.item = item
        # Remember that this player has been active
        app.PLAYSTATE.active_players.add(playerid)
        status.update(info)
        LOG.debug('Set the Plex container_key to: %s', container_key)
        status['container_key'] = container_key
        status['file'] = path
        status['kodi_id'] = kodi_id
        status['kodi_type'] = kodi_type
        status['plex_id'] = plex_id
        status['plex_type'] = plex_type
        status['playmethod'] = item.playmethod
        status['playcount'] = item.playcount
        status['external_player'] = app.APP.player.isExternalPlayer() == 1
        LOG.debug('Set the player state: %s', status)

        if playerid == v.KODI_VIDEO_PLAYER_ID:
            task = InitVideoStreams(item)
            backgroundthread.BGThreader.addTask(task)
            # Send Up Next signal for episodes if enabled
            if upnext_integration:
                task = SendUpNextSignal(item, status)
                backgroundthread.BGThreader.addTask(task)

    def _on_av_change(self, data):
        """
        Will be called when Kodi has a video, audio or subtitle stream. Also
        happens when the stream changes.

        Example data as returned by Kodi:
            {'item': {'id': 5, 'type': 'movie'},
             'player': {'playerid': 1, 'speed': 1}}
        """
        pass


def _playback_cleanup(ended=False):
    """
    PKC cleanup after playback ends/is stopped. Pass ended=True if Kodi
    completely finished playing an item (because we will get and use wrong
    timing data otherwise)
    """
    LOG.debug('playback_cleanup called. Active players: %s',
              app.PLAYSTATE.active_players)
    if app.APP.skip_markers_dialog:
        app.APP.skip_markers_dialog.close()
        app.APP.skip_markers_dialog = None
    # We might have saved a transient token from a user flinging media via
    # Companion (if we could not use the playqueue to store the token)
    app.CONN.plex_transient_token = None
    for playerid in app.PLAYSTATE.active_players:
        status = app.PLAYSTATE.player_states[playerid]
        # Stop transcoding
        if status['playmethod'] == v.PLAYBACK_METHOD_TRANSCODE:
            LOG.debug('Tell the PMS to stop transcoding')
            DU().downloadUrl(
                '{server}/video/:/transcode/universal/stop',
                parameters={'session': v.PKC_MACHINE_IDENTIFIER})
        if playerid == 1:
            # Bookmarks might not be pickup up correctly, so let's do them
            # manually. Applies to addon paths, but direct paths might have
            # started playback via PMS
            _record_playstate(status, ended)
        # Reset the player's status
        app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
    # As all playback has halted, reset the players that have been active
    app.PLAYSTATE.active_players = set()
    app.PLAYSTATE.item = None
    utils.delete_temporary_subtitles()
    LOG.debug('Finished PKC playback cleanup')


def _record_playstate(status, ended):
    if not status['plex_id']:
        LOG.debug('No Plex id found to record playstate for status %s', status)
        return
    if status['plex_type'] not in v.PLEX_VIDEOTYPES:
        LOG.debug('Not messing with non-video entries')
        return
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_id(status['plex_id'], status['plex_type'])
    if not db_item:
        # Item not (yet) in Kodi library
        LOG.debug('No playstate update due to Plex id not found: %s', status)
        return
    time, totaltime, playcount, last_played, reload_skin = _playback_progress(status, ended, db_item)
    with kodi_db.KodiVideoDB() as kodidb:
        kodidb.set_resume(db_item['kodi_fileid'],
                          time,
                          totaltime,
                          playcount,
                          last_played)
        if 'kodi_fileid_2' in db_item and db_item['kodi_fileid_2']:
            # Dirty hack for our episodes
            kodidb.set_resume(db_item['kodi_fileid_2'],
                              time,
                              totaltime,
                              playcount,
                              last_played)
    if reload_skin:
        xbmc.executebuiltin('ReloadSkin()')
    else:
        xbmc.executebuiltin('Container.Refresh')
    task = backgroundthread.FunctionAsTask(_clean_file_table, None)
    backgroundthread.BGThreader.addTasksToFront([task])


def _playback_progress(status, ended, db_item):
    LOG.debug('First credits marker: %s', status['first_credits_marker'])
    LOG.debug('Last credits marker: %s', status['final_credits_marker'])
    LOG.debug('Using PMS setting LibraryVideoPlayedAtBehaviour=%s',
              v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR)
    LOG.debug('Kodi advancedsettings: playcountminimumpercent=%s, '
              'ignoresecondsatstart=%s, '
              'ignorepercentatend=%s',
              v.KODI_PLAYCOUNTMINIMUMPERCENT,
              v.KODI_IGNORESECONDSATSTART,
              v.KODI_IGNOREPERCENTATEND)
    totaltime = float(timing.kodi_time_to_millis(status['totaltime'])) / 1000
    # Safety net should we ever get 0
    totaltime = totaltime or 0.000001
    last_played = timing.kodi_now()
    reload_skin = False
    playcount = status['playcount']
    if playcount is None:
        LOG.debug('playcount not found, looking it up in the Kodi DB')
        with kodi_db.KodiVideoDB(lock=False) as kodidb:
            playcount = kodidb.get_playcount(db_item['kodi_fileid']) or 0
    if status['external_player']:
        # video has either been entirely watched - or not.
        # "ended" won't work, need a workaround
        ended = _external_player_correct_plex_watch_count(db_item)
        time = 0.0
        progress = 0.0
    else:
        time = float(timing.kodi_time_to_millis(status['time'])) / 1000
        progress = time / totaltime
        LOG.debug('time %s, totaltime %s, progress %s, MARK_PLAYED_AT %s',
                  time, totaltime, progress, v.MARK_PLAYED_AT)
        # If there is no first credits marker, use the last credits
        first = status['first_credits_marker'] or status['final_credits_marker']
        last = status['final_credits_marker']
        # Decide on whether video ended - based on the PMS setting
        if v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 3 \
                and first \
                and first[0] / totaltime < v.MARK_PLAYED_AT:
            # "earliest between threshold percent and first credits marker"
            ended = True if time >= first[0] else False
        elif v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 1 and last:
            # "at final credits marker position"
            ended = True if time >= last[0] else False
        elif v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR == 2 and first:
            # "at first credits marker position"
            ended = True if time >= first[0] else False
        else:
            # use threshold, corresponds to
            # v.LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 0
            ended = True if progress >= v.MARK_PLAYED_AT else False
        LOG.debug('Deduced that video has ended: %s', ended)
        # Did we reach a different decision than Kodi and must thus reload
        # the skin to reflect that?
        if not ended and progress > v.KODI_PLAYCOUNTMINIMUMPERCENT:
            reload_skin = True
        elif time > v.IGNORE_SECONDS_AT_START \
                and time < v.KODI_IGNORESECONDSATSTART:
            reload_skin = True
    if ended:
        playcount += 1
        time = 0.0
        progress = 100.0
    elif not status['external_player'] and time < v.IGNORE_SECONDS_AT_START:
        LOG.debug('Ignoring playback less than %s seconds',
                  v.IGNORE_SECONDS_AT_START)
        # Annoying Plex bug - it'll reset an already watched video to unwatched
        playcount = None
        last_played = None
        time = 0.0
        progress = 0.0
    LOG.debug('Resulting playback progress %s (%s of %s seconds) playcount %s',
              progress, time, totaltime, playcount)
    LOG.debug('Force-reload skin to force Kodi to show in-progress video: %s',
              reload_skin)
    return time, totaltime, playcount, last_played, reload_skin


def _external_player_correct_plex_watch_count(db_item):
    """
    Kodi won't safe playstate at all for external players

    There's currently no way to get a resumpoint if an external player is
    in use  We are just checking whether we should mark video as
    completely watched or completely unwatched (according to
    playcountminimumtime set in playercorefactory.xml)
    See https://kodi.wiki/view/External_players
    """
    with kodi_db.KodiVideoDB(lock=False) as kodidb:
        playcount = kodidb.get_playcount(db_item['kodi_fileid'])
    LOG.debug('External player detected. Playcount: %s', playcount)
    PF.scrobble(db_item['plex_id'], 'watched' if playcount else 'unwatched')
    return True if playcount else False


def _clean_file_table():
    """
    If we associate a playing video e.g. pointing to plugin://... to an existing
    Kodi library item, Kodi will add an additional entry for this (additional)
    path plugin:// in the file table. This leads to all sorts of wierd behavior.
    This function tries for at most 5 seconds to clean the file table.
    """
    LOG.debug('Start cleaning Kodi files table')
    if app.APP.monitor.waitForAbort(2):
        # PKC should exit
        return
    try:
        with kodi_db.KodiVideoDB() as kodidb:
            obsolete_file_ids = list(kodidb.obsolete_file_ids())
            for file_id in obsolete_file_ids:
                LOG.debug('Removing obsolete Kodi file_id %s', file_id)
                kodidb.remove_file(file_id, remove_orphans=False)
    except utils.OperationalError:
        LOG.debug('Database was locked, unable to clean file table')
    else:
        LOG.debug('Done cleaning up Kodi file table')


def _next_episode(current_api):
    """
    Returns the xml for the next episode after the current one
    Returns None if something went wrong or there is no next episode
    """
    xml = PF.show_episodes(current_api.grandparent_id())
    if xml is None:
        return
    for counter, episode in enumerate(xml):
        api = API(episode)
        if api.plex_id == current_api.plex_id:
            break
    else:
        LOG.error('Did not find the episode with Plex id %s for show %s: %s',
                  current_api.plex_id, current_api.grandparent_id(),
                  current_api.grandparent_title())
        return
    try:
        return API(xml[counter + 1])
    except IndexError:
        # Was the last episode
        pass


def _complete_artwork_keys(info):
    """
    Make sure that the minimum set of keys is present in the info dict
    """
    for key in ('tvshow.poster',
                'tvshow.fanart',
                'tvshow.landscape',
                'tvshow.clearart',
                'tvshow.clearlogo',
                'thumb'):
        if key not in info['art']:
            info['art'][key] = ''


def _videolibrary_onupdate(data):
    """
    A specific Kodi library item has been updated. This seems to happen if the
    user marks an item as watched/unwatched or if playback of the item just
    stopped

    2 kinds of messages possible, e.g.
        Method: VideoLibrary.OnUpdate Data: ("Reset resume position" and also
        fired just after stopping playback - BEFORE OnStop fires)
            {'id': 1, 'type': 'movie'}
        Method: VideoLibrary.OnUpdate Data: ("Mark as watched")
            {'item': {'id': 1, 'type': 'movie'}, 'playcount': 1}
    """
    item = data.get('item') if 'item' in data else data
    try:
        kodi_id = item['id']
        kodi_type = item['type']
    except (KeyError, TypeError):
        LOG.debug("Item is invalid for a Plex playstate update")
        return
    playcount = data.get('playcount')
    if playcount is None:
        # "Reset resume position"
        # Kodi might set as watched or unwatched!
        with KodiVideoDB(lock=False) as kodidb:
            file_id = kodidb.file_id_from_id(kodi_id, kodi_type)
            if file_id is None:
                return
            if kodidb.get_resume(file_id):
                # We do have an existing bookmark entry - not toggling to
                # either watched or unwatched on the Plex side
                return
            playcount = kodidb.get_playcount(file_id) or 0
    if app.PLAYSTATE.item and kodi_id == app.PLAYSTATE.item.kodi_id and \
            kodi_type == app.PLAYSTATE.item.kodi_type:
        # Kodi updates an item immediately after playback. Hence we do NOT
        # increase or decrease the viewcount
        return
    # Send notification to the server.
    with PlexDB(lock=False) as plexdb:
        db_item = plexdb.item_by_kodi_id(kodi_id, kodi_type)
    if not db_item:
        LOG.error("Could not find plex_id in plex database for a "
                  "video library update")
        return
    # notify the server
    if playcount > 0:
        PF.scrobble(db_item['plex_id'], 'watched')
    else:
        PF.scrobble(db_item['plex_id'], 'unwatched')


class InitVideoStreams(backgroundthread.Task):
    """
    The Kodi player takes forever to initialize all streams Especially
    subtitles, apparently. No way to tell when Kodi is done :-(
    """

    def __init__(self, item):
        self.item = item
        super().__init__()

    def run(self):
        if app.APP.monitor.waitForAbort(WAIT_BEFORE_INIT_STREAMS):
            return
        i = 0
        while True:
            try:
                self.item.init_streams()
            except Exception as err:
                i += 1
                if app.APP.monitor.waitForAbort(1):
                    return
                if i > ADDITIONAL_WAIT_BEFORE_INIT_STREAMS:
                    LOG.error('Exception encountered while init streams:')
                    LOG.error(err)
                    return
            else:
                break


class SendUpNextSignal(backgroundthread.Task):
    """
    Sends the Up Next signal after playback has started.
    We wait a bit to ensure playback is fully initialized.
    """

    def __init__(self, item, status):
        self.item = item
        self.status = status
        super().__init__()

    def run(self):
        # Wait for playback to stabilize before sending Up Next signal
        if app.APP.monitor.waitForAbort(2):
            return
        try:
            # Get notification time from Plex credits markers if available
            notification_time = upnext.get_notification_time_from_markers(self.status)
            signal_sent = upnext.send_upnext_signal(self.item.api, notification_time)
            # Store whether Up Next found a next episode
            # If False, PKC skip credits popup will still show for last episodes
            with app.APP.lock_playqueues:
                playerid = self.item.playerid
                app.PLAYSTATE.player_states[playerid]['upnext_signal_sent'] = signal_sent
            if not signal_sent:
                LOG.debug('Up Next: No next episode - PKC skip credits will handle last episode')
        except Exception as err:
            LOG.error('Exception encountered while sending Up Next signal:')
            LOG.error(err)
