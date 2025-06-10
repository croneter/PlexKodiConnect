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

    def try_identify_and_set_plex_item(self, playerid, playqueue, current_kodi_item_data):
        """
        Identifies the playing item and sets app.PLAYSTATE.item.
        Encapsulates logic from the original PlayBackStart.
        Returns the item if successful, None otherwise.
        """
        # kodi_id, kodi_type, path are initially from current_kodi_item_data (which is `data` in PlayBackStart context)
        # or from self._json_item() if needed later.
        kodi_id = current_kodi_item_data['item'].get('id')
        kodi_type = current_kodi_item_data['item'].get('type')
        path = current_kodi_item_data['item'].get('file')

        info = js.get_player_props(playerid) # Used for pos and later for status update
        if playqueue.kodi_playlist_playback:
            pos = 0
            LOG.debug('try_identify_and_set_plex_item: Detected playback from a Kodi playlist, pos = 0')
        else:
            pos = info['position'] if info['position'] != -1 else 0
            LOG.debug('try_identify_and_set_plex_item: Detected position %s for %s', pos, playqueue)

        status = app.PLAYSTATE.player_states[playerid]

        # Detailed logging before try: item = playqueue.items[pos]
        pq_item_details = "N/A"
        if pos < len(playqueue.items) and playqueue.items[pos] is not None:
            # Accessing playqueue.items[pos] here before 'initialize' logic might be premature
            # if the goal is to only access it if not initializing.
            # However, this log is for pre-check, so it's informative.
            current_pq_item = playqueue.items[pos]
            pq_item_plex_id = getattr(current_pq_item, 'plex_id', 'Unknown plex_id')
            pq_item_kodi_type = getattr(current_pq_item, 'kodi_type', 'Unknown kodi_type')
            pq_item_details = "plex_id: %s, kodi_type: %s" % (pq_item_plex_id, pq_item_kodi_type)
        elif pos >= len(playqueue.items):
            pq_item_details = "pos out of bounds"
        else:
            pq_item_details = "item is None"
        LOG.debug("try_identify_and_set_plex_item: Pre-init check. PlayerID: %s, Pos: %s, Playqueue len: %s, Item@pos details: %s", playerid, pos, len(playqueue.items), pq_item_details)

        initialize = False # Default to False
        item = None # Ensure item is defined

        try:
            item = playqueue.items[pos] # Attempt to get item from playqueue
            LOG.debug('try_identify_and_set_plex_item: PKC playqueue item is: %s', item)
        except IndexError:
            LOG.debug('try_identify_and_set_plex_item: Position %s not in PKC playqueue yet', pos)
            initialize = True
        else: # Item successfully retrieved from playqueue.items[pos]
            # This 'else' block contains the logic to determine if initialization is still needed
            # even if an item was found at playqueue.items[pos].

            # Ensure kodi_id, kodi_type, path are up-to-date if not available from initial current_kodi_item_data
            # This logic was inside the 'else' in the original PlayBackStart
            if not kodi_id: # kodi_id might be None if current_kodi_item_data['item'] was empty or lacked 'id'
                # This implies self._json_item might be called even if an item is found in playqueue,
                # if the initial kodi_id from current_kodi_item_data was missing.
                LOG.debug("try_identify_and_set_plex_item: kodi_id is missing, calling _json_item.")
                kodi_id, kodi_type, path = self._json_item(playerid)


            if kodi_id and item.kodi_id: # Both initial kodi_id (potentially from _json_item) and item from playqueue have kodi_id
                if item.kodi_id != kodi_id or item.kodi_type != kodi_type:
                    LOG.debug('try_identify_and_set_plex_item: Detected different Kodi id (%s vs %s) or type (%s vs %s).', item.kodi_id, kodi_id, item.kodi_type, kodi_type)
                    initialize = True
                else:
                    initialize = False # Matching kodi_id and kodi_type
            else: # Either initial kodi_id is missing, or playqueue item's kodi_id is missing
                  # This path suggests item might be a clip or something without a Kodi DB entry.
                LOG.debug("try_identify_and_set_plex_item: Initial kodi_id (%s) or playqueue item's kodi_id (%s) is missing. Comparing paths.", kodi_id, item.kodi_id)
                if not path: # Path might also be missing if kodi_id was missing and _json_item didn't yield a path
                    # This call to _json_item seems redundant if kodi_id was already fetched above.
                    # However, path might be specifically what's needed here.
                    LOG.debug("try_identify_and_set_plex_item: path is missing, calling _json_item again.")
                    _, _, path = self._json_item(playerid) # kodi_id, kodi_type from this call are not used here.

                if path == '': # Path is empty string
                    LOG.debug('try_identify_and_set_plex_item: Detected empty path: aborting playback report')
                    app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template) # Reset player state
                    app.PLAYSTATE.item = None # Ensure item is not set
                    return None

                if item.file != path: # Compare playqueue item's file with current path
                    LOG.debug('try_identify_and_set_plex_item: Detected different path for item. Playqueue item file: %s, Current path: %s', item.file, path)
                    try:
                        # This REGEX implies path should be a URL that might contain a plex_id
                        tmp_plex_id = int(utils.REGEX_PLEX_ID.findall(path)[0])
                    except (IndexError, TypeError):
                        LOG.debug('try_identify_and_set_plex_item: No Plex id in path, need to init playqueue')
                        initialize = True
                    else:
                        if tmp_plex_id == item.plex_id:
                            LOG.debug('try_identify_and_set_plex_item: Detected different path for the same plex_id. Item may have updated path.')
                            initialize = False # Paths differ but plex_id matches, treat as same item, no full re-init
                            item.file = path # Update file path for the existing item
                        else:
                            LOG.debug('try_identify_and_set_plex_item: Different Plex id in path, need to init playqueue')
                            initialize = True
                else: # Paths match
                    initialize = False

        LOG.debug("try_identify_and_set_plex_item: Initialize flag set to: %s", initialize)

        if initialize:
            LOG.debug('try_identify_and_set_plex_item: Need to initialize Plex and PKC playqueue')
            # If kodi_id, kodi_type, path were not available from current_kodi_item_data or set above.
            if not kodi_id or not kodi_type or not path:
                LOG.debug("try_identify_and_set_plex_item: kodi_id, kodi_type, or path still missing, calling _json_item.")
                kodi_id, kodi_type, path = self._json_item(playerid)

            # _get_ids uses kodi_id, kodi_type, path to find plex_id, plex_type from DB or path
            plex_id, plex_type = self._get_ids(kodi_id, kodi_type, path)

            if not plex_id:
                LOG.debug('try_identify_and_set_plex_item: Initial plex_id fetch failed. Attempting fallback via playqueue.')
                try:
                    current_item_in_playqueue = playqueue.items[pos] # pos might be out of bounds if playqueue is empty
                    if current_item_in_playqueue and hasattr(current_item_in_playqueue, 'plex_id') and current_item_in_playqueue.plex_id:
                        plex_id = current_item_in_playqueue.plex_id
                        plex_type = current_item_in_playqueue.plex_type
                        LOG.info('try_identify_and_set_plex_item: Successfully obtained plex_id (%s) and plex_type (%s) using playqueue fallback.', plex_id, plex_type)
                    else:
                        LOG.debug('try_identify_and_set_plex_item: Fallback failed: item at pos %s in playqueue has no plex_id or is None.', pos)
                except IndexError:
                    LOG.debug('try_identify_and_set_plex_item: Fallback failed: playqueue has no item at pos %s. Playqueue length: %s', pos, len(playqueue.items))
                except AttributeError:
                    LOG.debug('try_identify_and_set_plex_item: Fallback failed: item at pos %s in playqueue does not have plex_id or plex_type attribute.', pos)
                except Exception as e:
                    LOG.error('try_identify_and_set_plex_item: Fallback failed due to an unexpected error: %s', e)

            if not plex_id:
                pq_item_plex_id_info_fallback = "N/A"
                if pos < len(playqueue.items): # Check pos bounds again for logging
                    item_at_pos_fallback = playqueue.items[pos]
                    if hasattr(item_at_pos_fallback, "plex_id"):
                        pq_item_plex_id_info_fallback = "plex_id: %s" % item_at_pos_fallback.plex_id
                    elif hasattr(item_at_pos_fallback, "title"):
                        pq_item_plex_id_info_fallback = "title: %s (no plex_id)" % item_at_pos_fallback.title
                    else:
                        pq_item_plex_id_info_fallback = "Unknown item (no plex_id)"

                LOG.error('try_identify_and_set_plex_item: No Plex id obtained after all attempts - aborting playback report. PlayerID: %s, KodiID: %s, KodiType: %s, Path: %s, Playqueue items count: %s, Playqueue item at pos %s: %s. app.PLAYSTATE.item will not be set.',
                          playerid, kodi_id, kodi_type, path, len(playqueue.items), pos, pq_item_plex_id_info_fallback)
                app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                app.PLAYSTATE.item = None
                return None

            try:
                # PL.init_plex_playqueue is expected to return the item.
                item = PL.init_plex_playqueue(playqueue, plex_id=plex_id) # kodi_item is not passed here, relies on plex_id
                LOG.debug("try_identify_and_set_plex_item: Post PL.init_plex_playqueue. Item plex_id: %s, plex_type: %s", item.plex_id if item else "N/A", item.plex_type if item else "N/A")
                if item: # If PL.init_plex_playqueue succeeds and returns an item
                     item.file = path # Set the file path for the newly initialized item
                else: # PL.init_plex_playqueue failed to return an item
                    LOG.error("try_identify_and_set_plex_item: PL.init_plex_playqueue did not return an item for plex_id %s", plex_id)
                    app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                    app.PLAYSTATE.item = None
                    return None
            except exceptions.PlaylistError:
                LOG.info('try_identify_and_set_plex_item: Could not initialize the Plex playlist for plex_id %s', plex_id)
                app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                app.PLAYSTATE.item = None
                return None

            # Set the Plex container key
            container_key = None
            if info['playlistid'] != -1:
                container_key = app.PLAYQUEUES[playerid].id
            if container_key is not None:
                container_key = '/playQueues/%s' % container_key
            elif plex_id is not None: # plex_id should be valid here
                container_key = '/library/metadata/%s' % plex_id
            # status['container_key'] will be set later

        else: # No need to initialize playqueues, item was already good from playqueue.items[pos]
            LOG.debug('try_identify_and_set_plex_item: No need to initialize playqueues')
            # Use details from the existing item
            kodi_id = item.kodi_id
            kodi_type = item.kodi_type
            plex_id = item.plex_id # This is critical, ensure item has plex_id
            plex_type = item.plex_type
            path = item.file # Use item's file path

            if not plex_id:
                LOG.error("try_identify_and_set_plex_item: Item from playqueue lacks plex_id. Item: %s", item)
                app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
                app.PLAYSTATE.item = None
                return None

            container_key = None
            if playqueue.id:
                container_key = '/playQueues/%s' % playqueue.id
            else:
                container_key = '/library/metadata/%s' % plex_id

        if not item:
            LOG.error("try_identify_and_set_plex_item: Item is None before final assignment. This should not happen.")
            app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
            app.PLAYSTATE.item = None
            return None

        # Mechanik for Plex skip intro/credits/commercials feature
        if utils.settings('enableSkipIntro') == 'true' \
                or utils.settings('enableSkipCredits') == 'true' \
                or utils.settings('enableSkipCommercials') == 'true':
            if hasattr(item, 'api') and item.api: # Ensure item.api is valid
                status['markers'] = item.api.markers()
                status['markers_hidden'] = {}
                if utils.settings('enableSkipCredits') == 'true':
                    status['first_credits_marker'] = item.api.first_credits_marker()
                    status['final_credits_marker'] = item.api.final_credits_marker()
            else:
                LOG.warning("try_identify_and_set_plex_item: Item has no valid 'api' attribute for markers. Item: %s", item)


        if item.playmethod is None and path and not path.startswith('plugin://'):
            item.playmethod = v.PLAYBACK_METHOD_DIRECT_PATH

        item.playerid = playerid

        LOG.info("try_identify_and_set_plex_item: Assigning to app.PLAYSTATE.item: plex_id=%s, plex_type=%s, file=%s", item.plex_id, item.plex_type, item.file)
        app.PLAYSTATE.item = item
        app.PLAYSTATE.active_players.add(playerid)

        # Update status dictionary
        status.update(info) # info from js.get_player_props(playerid) at the beginning
        LOG.debug('try_identify_and_set_plex_item: Set the Plex container_key to: %s', container_key)
        status['container_key'] = container_key
        status['file'] = path
        status['kodi_id'] = kodi_id
        status['kodi_type'] = kodi_type
        status['plex_id'] = plex_id # Should be item.plex_id
        status['plex_type'] = plex_type # Should be item.plex_type
        status['playmethod'] = item.playmethod
        status['playcount'] = item.playcount
        status['external_player'] = app.APP.player.isExternalPlayer() == 1
        LOG.debug('try_identify_and_set_plex_item: Set the player state: %s', status)

        if playerid == v.KODI_VIDEO_PLAYER_ID:
            task = InitVideoStreams(item) # InitVideoStreams needs item
            backgroundthread.BGThreader.addTask(task)

        return item


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
        self._already_slept = False # Reset sleep flag for _json_item

        try:
            playerid = data['player']['playerid']
            # Initial kodi_type for playerid determination if playerid is -1
            kodi_type_for_playerid_lookup = data['item'].get('type')
        except (TypeError, KeyError):
            LOG.info('PlayBackStart: Aborting playback report - item invalid for updates %s', data)
            return

        if data['item'].get('channeltype') == 'tv':
            LOG.info('PlayBackStart: TV playback detected, aborting Plex playback report')
            return

        if playerid == -1:
            LOG.debug("PlayBackStart: PlayerID is -1, attempting to find active player.")
            try:
                playerid = js.get_player_ids()[0]
            except IndexError:
                LOG.debug("PlayBackStart: No active player found via js.get_player_ids(). Trying playlist type lookup.")
                if kodi_type_for_playerid_lookup in v.KODI_VIDEOTYPES:
                    playlist_type = v.KODI_TYPE_VIDEO_PLAYLIST
                elif kodi_type_for_playerid_lookup in v.KODI_AUDIOTYPES:
                    playlist_type = v.KODI_TYPE_AUDIO_PLAYLIST
                else:
                    LOG.error('PlayBackStart: Unexpected kodi_type %s for playerid=-1 lookup, data %s', kodi_type_for_playerid_lookup, data)
                    return
                playerid = js.get_playlist_id(playlist_type)
                if not playerid: # playerid can be 0, which is a valid ID.
                    LOG.error('PlayBackStart: Could not get playerid for data %s via playlist type %s', data, playlist_type)
                    return
            LOG.debug("PlayBackStart: Found playerid: %s", playerid)

        playqueue = app.PLAYQUEUES[playerid]

        # Call the new refactored method
        # current_kodi_item_data is 'data' passed to PlayBackStart
        identified_item = self.try_identify_and_set_plex_item(playerid, playqueue, data)

        if identified_item:
            LOG.info("PlayBackStart: Successfully identified and set plex item: %s", identified_item.plex_id if hasattr(identified_item, 'plex_id') else "Unknown plex_id")
        else:
            LOG.warning("PlayBackStart: Failed to identify and set plex item for playerid %s.", playerid)
            # try_identify_and_set_plex_item handles resetting player_states and PLAYSTATE.item to None on failure.

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
