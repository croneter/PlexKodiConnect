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
from .playlist_func import PlaylistItem # Added import
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

    def _reset_player_state_and_item(self, playerid):
        app.PLAYSTATE.player_states[playerid] = copy.deepcopy(app.PLAYSTATE.template)
        app.PLAYSTATE.item = None
        LOG.debug("_reset_player_state_and_item: Player state and PLAYSTATE.item reset for playerid %s.", playerid)

    def _gather_initial_playback_info(self, playerid, playqueue, current_kodi_item_data):
        initial_kodi_id = current_kodi_item_data['item'].get('id')
        initial_kodi_type = current_kodi_item_data['item'].get('type')
        initial_path = current_kodi_item_data['item'].get('file')

        player_info = js.get_player_props(playerid)
        if playqueue.kodi_playlist_playback:
            pos = 0
            LOG.debug('_gather_initial_playback_info: Detected playback from a Kodi playlist, pos = 0')
        else:
            pos = player_info['position'] if player_info['position'] != -1 else 0
            LOG.debug('_gather_initial_playback_info: Detected position %s for playerid %s', pos, playerid)
        
        return initial_kodi_id, initial_kodi_type, initial_path, player_info, pos

    def _determine_initialization_need(self, playerid, playqueue, pos, current_kodi_id, current_kodi_type, current_path):
        # This helper now takes current_kodi_id, current_kodi_type, current_path as input,
        # which are initially from _gather_initial_playback_info.
        # It will update them by calling self._json_item if necessary.
        
        LOG.debug("_determine_initialization_need: Pre-check. PlayerID: %s, Pos: %s, PQ_Len: %s, KodiID: %s, KodiType: %s, Path: %s",
                  playerid, pos, len(playqueue.items), current_kodi_id, current_kodi_type, current_path)

        try:
            item_from_playqueue = playqueue.items[pos]
            LOG.debug('_determine_initialization_need: Item from playqueue at pos %s: %s', pos, item_from_playqueue)
        except IndexError:
            LOG.debug('_determine_initialization_need: Position %s not in PKC playqueue. Need initialization.', pos)
            # If item not in playqueue, kodi_id/type/path might still need _json_item if not initially available
            if not current_kodi_id or not current_kodi_type or not current_path:
                 LOG.debug("_determine_initialization_need: kodi_id/type/path missing for IndexError case, calling _json_item.")
                 current_kodi_id, current_kodi_type, current_path = self._json_item(playerid)
            return True, None, current_kodi_id, current_kodi_type, current_path

        # Item exists in playqueue, now check if it matches the playing item
        if not current_kodi_id: # current_kodi_id (from notification) might be missing
            LOG.debug("_determine_initialization_need: current_kodi_id (from notification) is missing, calling _json_item to get it.")
            # Update current_kodi_id, current_kodi_type, current_path from _json_item
            current_kodi_id, current_kodi_type, current_path = self._json_item(playerid)

        if current_kodi_id and item_from_playqueue.kodi_id:
            if item_from_playqueue.kodi_id != current_kodi_id or item_from_playqueue.kodi_type != current_kodi_type:
                LOG.debug('_determine_initialization_need: Different Kodi ID/Type. PQ_Item: (%s, %s), Current: (%s, %s). Need init.',
                          item_from_playqueue.kodi_id, item_from_playqueue.kodi_type, current_kodi_id, current_kodi_type)
                return True, item_from_playqueue, current_kodi_id, current_kodi_type, current_path
            else: # IDs and Types match
                LOG.debug('_determine_initialization_need: Kodi ID/Type match. No init needed based on ID/Type.')
                return False, item_from_playqueue, current_kodi_id, current_kodi_type, current_path
        else: # current_kodi_id (from json_item) or item_from_playqueue.kodi_id is missing. Compare by path.
            LOG.debug("_determine_initialization_need: Kodi ID missing from item_from_playqueue or current_kodi_id. Comparing by path. Current path: %s", current_path)
            if not current_path: # If path also missing from current_kodi_id source (e.g. _json_item failed)
                LOG.debug("_determine_initialization_need: current_path is also missing after _json_item. This implies _json_item might have failed to get path.")
                # It's possible current_kodi_id, current_kodi_type were also not updated if _json_item failed for path.
                # This state is tricky. If path is essential for clips, and it's missing, re-init might be safest.
                # However, if _json_item was already called because initial kodi_id was missing, calling it again for path
                # might not be useful unless it specifically failed for path before.
                # For now, let's assume if path is empty, and we are in this branch, it's problematic.
                # The original code called _json_item again if path was not set.
                # We ensure current_path refers to the most recent attempt from _json_item if kodi_id was missing.
                # If path is still empty here, it means even _json_item didn't provide it.
                if current_path == '': # Explicitly empty string from _json_item
                     LOG.debug('_determine_initialization_need: Detected empty path from _json_item. Aborting this check path.')
                     # This case should lead to returning the item and not initializing IF other checks pass.
                     # However, the original logic for empty path was an early return from the main function.
                     # Here, we can't do that. Let's say if path is empty, we can't compare. This might mean init is needed.
                     # This helper's role is to decide init, not to abort entirely.
                     # If path is empty, and we are in this branch (kodi_id missing from one source), assume init.
                     return True, item_from_playqueue, current_kodi_id, current_kodi_type, current_path


            if item_from_playqueue.file != current_path:
                LOG.debug('_determine_initialization_need: Different path. PQ_Item file: %s, Current path: %s', item_from_playqueue.file, current_path)
                try:
                    tmp_plex_id = int(utils.REGEX_PLEX_ID.findall(current_path)[0])
                except (IndexError, TypeError):
                    LOG.debug('_determine_initialization_need: No Plex ID in current_path. Need init.')
                    return True, item_from_playqueue, current_kodi_id, current_kodi_type, current_path
                else:
                    if tmp_plex_id == item_from_playqueue.plex_id:
                        LOG.debug('_determine_initialization_need: Different path but same Plex ID. Update item file. No init.')
                        item_from_playqueue.file = current_path # Update existing item's file
                        return False, item_from_playqueue, current_kodi_id, current_kodi_type, current_path
                    else:
                        LOG.debug('_determine_initialization_need: Different path and different Plex ID. Need init.')
                        return True, item_from_playqueue, current_kodi_id, current_kodi_type, current_path
            else: # Paths match
                LOG.debug('_determine_initialization_need: Paths match. No init needed.')
                return False, item_from_playqueue, current_kodi_id, current_kodi_type, current_path

    def _initialize_new_plex_item(self, playerid, playqueue, pos, current_kodi_id, current_kodi_type, current_path, playlist_id_from_info):
        LOG.debug('_initialize_new_plex_item: Initializing. PlayerID: %s, KodiID: %s, Path: %s', playerid, current_kodi_id, current_path)
        
        # Ensure kodi_id, type, path are sourced if still missing (e.g., if _determine_initialization_need decided to init due to IndexError)
        if not current_kodi_id or not current_kodi_type or not current_path:
            LOG.debug("_initialize_new_plex_item: kodi_id/type/path still missing, calling _json_item.")
            current_kodi_id, current_kodi_type, current_path = self._json_item(playerid)

        # Determine plex_id and plex_type
        plex_id, plex_type = self._get_ids(current_kodi_id, current_kodi_type, current_path)

        if not plex_id: # Fallback for plex_id if _get_ids fails
            LOG.debug('_initialize_new_plex_item: Initial plex_id fetch using _get_ids failed. Attempting fallback via playqueue.')
            try:
                item_at_pos = playqueue.items[pos] 
                if item_at_pos and hasattr(item_at_pos, 'plex_id') and item_at_pos.plex_id:
                    plex_id = item_at_pos.plex_id
                    plex_type = item_at_pos.plex_type # Assuming plex_type is also on this item
                    LOG.info('_initialize_new_plex_item: Successfully obtained plex_id (%s) and plex_type (%s) using playqueue fallback.', plex_id, plex_type)
                else:
                    LOG.debug('_initialize_new_plex_item: Fallback via playqueue.items[pos] failed: item at pos %s has no plex_id or is None.', pos)
            except IndexError:
                LOG.debug('_initialize_new_plex_item: Fallback via playqueue.items[pos] failed: playqueue has no item at pos %s. PQ_Len: %s', pos, len(playqueue.items))
            except AttributeError:
                LOG.debug('_initialize_new_plex_item: Fallback via playqueue.items[pos] failed: item at pos %s has no plex_id/plex_type attr.', pos)
            except Exception as e:
                LOG.error('_initialize_new_plex_item: Fallback via playqueue.items[pos] failed due to an unexpected error: %s', e)

        # NEW LOGIC: Handle cases based on whether plex_id was found
        if plex_id is None:
            LOG.info("_initialize_new_plex_item: No Plex ID found after _get_ids and fallback. Creating a transient PlaylistItem for path: %s, type: %s.", current_path, current_kodi_type)
            item = PlaylistItem() 
            item.plex_id = None # Explicitly None for transient
            # Ensure current_kodi_type is used for both plex_type and kodi_type for consistency with transient items
            # Default to v.KODI_TYPE_VIDEO if current_kodi_type is None (e.g. for some direct paths/pre-rolls)
            _type = current_kodi_type if current_kodi_type else v.KODI_TYPE_VIDEO
            item.plex_type = _type 
            item.kodi_type = _type # Make kodi_type consistent with plex_type for transient items
            item.file = current_path
            item.kodi_id = current_kodi_id # Can be None
            item.guid = f"transient://{current_path}" if current_path else "transient://unknown_path" # More specific guid
            
            if current_path and not current_path.startswith('plugin://'):
                item.playmethod = v.PLAYBACK_METHOD_DIRECT_PATH
            else:
                # Assume plugin if path starts with plugin:// or if path is None/empty (e.g. some pre-rolls might not have path)
                item.playmethod = v.PLAYBACK_METHOD_PLUGIN 
            
            item.playcount = 0 # New item
            item.title = current_path.split('/')[-1] if current_path else "Pre-roll Item" # Slightly more descriptive default
            item.offset = 0.0 # Start from beginning
            # item.duration: PlaylistItem initializes duration to 0, which is acceptable for transient if unknown
            item.api = None # Very important: no API object for transient items
            # Other PlaylistItem defaults (part=0, force_transcode=False, resume=None) are fine.

            container_key = None # No container for transient items
            # final_plex_type should be the type we assigned to the item
            final_plex_type = item.plex_type 
            LOG.debug("_initialize_new_plex_item: Created transient PlaylistItem: %s", item)
            return item, container_key, None, final_plex_type # plex_id is None
        
        else: # plex_id is not None, proceed with existing logic
            LOG.debug("_initialize_new_plex_item: Plex ID %s found. Proceeding with PL.init_plex_playqueue.", plex_id)
            try:
                item = PL.init_plex_playqueue(playqueue, plex_id=plex_id)
                LOG.debug("_initialize_new_plex_item: Post PL.init_plex_playqueue. Item plex_id: %s, type: %s", item.plex_id if item else "N/A", item.plex_type if item else "N/A")
                if item:
                    if current_path: # Ensure current_path is valid before assigning
                        item.file = current_path 
                else:
                    LOG.error("_initialize_new_plex_item: PL.init_plex_playqueue returned None for plex_id %s", plex_id)
                    return None, None, plex_id, plex_type # Return found plex_id/type but no item
            except exceptions.PlaylistError:
                LOG.info('_initialize_new_plex_item: Could not initialize Plex playlist for plex_id %s via PL.init_plex_playqueue.', plex_id)
                return None, None, plex_id, plex_type # Return found plex_id/type but no item

            container_key = None
            if playlist_id_from_info != -1:
                container_key = playqueue.id # Use playqueue.id directly
            if container_key is not None:
                container_key = '/playQueues/%s' % container_key
            elif plex_id is not None: # This will always be true if we are in this else block
                container_key = '/library/metadata/%s' % plex_id
            
            return item, container_key, plex_id, plex_type

    def _prepare_existing_plex_item(self, item_from_playqueue, playqueue):
        LOG.debug('_prepare_existing_plex_item: Using existing item from playqueue: %s', item_from_playqueue)
        kodi_id = item_from_playqueue.kodi_id
        kodi_type = item_from_playqueue.kodi_type
        plex_id = item_from_playqueue.plex_id
        plex_type = item_from_playqueue.plex_type
        path = item_from_playqueue.file # Use item's current file path (might have been updated)

        if not plex_id:
            LOG.error("_prepare_existing_plex_item: Item from playqueue lacks plex_id. Item: %s", item_from_playqueue)
            return None, None, kodi_id, kodi_type, None, plex_type, path # Critical failure if no plex_id

        container_key = None
        if playqueue.id:
            container_key = '/playQueues/%s' % playqueue.id
        else:
            container_key = '/library/metadata/%s' % plex_id
            
        return item_from_playqueue, container_key, kodi_id, kodi_type, plex_id, plex_type, path

    def _finalize_and_update_status(self, playerid, item, container_key, path, kodi_id, kodi_type, plex_id, plex_type, initial_player_info):
        LOG.debug('_finalize_and_update_status: Finalizing for item plex_id: %s', plex_id)
        status = app.PLAYSTATE.player_states[playerid]

        if utils.settings('enableSkipIntro') == 'true' \
                or utils.settings('enableSkipCredits') == 'true' \
                or utils.settings('enableSkipCommercials') == 'true':
            if hasattr(item, 'api') and item.api:
                status['markers'] = item.api.markers()
                status['markers_hidden'] = {}
                if utils.settings('enableSkipCredits') == 'true':
                    status['first_credits_marker'] = item.api.first_credits_marker()
                    status['final_credits_marker'] = item.api.final_credits_marker()
            else:
                LOG.warning("_finalize_and_update_status: Item has no valid 'api' attribute for markers. Item: %s", item)

        if item.playmethod is None and path and not path.startswith('plugin://'):
            item.playmethod = v.PLAYBACK_METHOD_DIRECT_PATH
        
        item.playerid = playerid
        
        LOG.info("_finalize_and_update_status: Assigning to app.PLAYSTATE.item: plex_id=%s, type=%s, file=%s", item.plex_id, item.plex_type, item.file)
        app.PLAYSTATE.item = item
        app.PLAYSTATE.active_players.add(playerid)
        
        status.update(initial_player_info)
        LOG.debug('_finalize_and_update_status: Set Plex container_key to: %s', container_key)
        status['container_key'] = container_key
        status['file'] = path
        status['kodi_id'] = kodi_id
        status['kodi_type'] = kodi_type
        status['plex_id'] = plex_id
        status['plex_type'] = plex_type
        status['playmethod'] = item.playmethod
        status['playcount'] = item.playcount
        status['external_player'] = app.APP.player.isExternalPlayer() == 1
        LOG.debug('_finalize_and_update_status: Player state updated: %s', status)

        if playerid == v.KODI_VIDEO_PLAYER_ID:
            task = InitVideoStreams(item)
            backgroundthread.BGThreader.addTask(task)
        
        return item

    def try_identify_and_set_plex_item(self, playerid, playqueue, current_kodi_item_data):
        initial_kodi_id, initial_kodi_type, initial_path, player_info, pos = \
            self._gather_initial_playback_info(playerid, playqueue, current_kodi_item_data)

        # current_kodi_id/type/path are passed to _determine_initialization_need and potentially updated by it
        # if it calls _json_item.
        should_init, item_from_pq, current_kodi_id, current_kodi_type, current_path = \
            self._determine_initialization_need(playerid, playqueue, pos, initial_kodi_id, initial_kodi_type, initial_path)
        
        # Handle empty path from _determine_initialization_need if it was from _json_item
        if current_path == '' and (not initial_path or initial_path == '') : # Path was identified as problematic (empty string)
             LOG.debug('try_identify_and_set_plex_item: Detected empty path from _determine_initialization_need. Aborting.')
             self._reset_player_state_and_item(playerid)
             return None

        item = None
        container_key = None
        final_kodi_id = current_kodi_id
        final_kodi_type = current_kodi_type
        final_plex_id = None 
        final_plex_type = None
        final_path = current_path

        if should_init:
            item, container_key, final_plex_id, final_plex_type = \
                self._initialize_new_plex_item(playerid, playqueue, pos, current_kodi_id, current_kodi_type, current_path, player_info['playlistid'])
            if item:
                final_path = item.file 
                final_kodi_id = item.kodi_id if hasattr(item, 'kodi_id') and item.kodi_id is not None else current_kodi_id
                final_kodi_type = item.kodi_type if hasattr(item, 'kodi_type') and item.kodi_type is not None else current_kodi_type
            # If item is None here, it means initialization failed to produce a usable item.
            # final_plex_id and final_plex_type might still have values if _get_ids succeeded before PL.init_plex_playqueue failed.
        elif item_from_pq:
            item, container_key, final_kodi_id, final_kodi_type, final_plex_id, final_plex_type, final_path = \
                self._prepare_existing_plex_item(item_from_pq, playqueue)
        else:
            # This case should ideally not be reached if _determine_initialization_need works correctly.
            # It implies should_init is False, but item_from_pq is also None.
            LOG.error("try_identify_and_set_plex_item: Inconsistent state - should_init is False but no item_from_pq.")
            self._reset_player_state_and_item(playerid)
            return None

        if not item or not final_plex_id: # Check if item is valid and plex_id was found
            LOG.warning("try_identify_and_set_plex_item: Failed to obtain a valid item or plex_id. Aborting. Item valid: %s, PlexID: %s", bool(item), final_plex_id)
            # Log detailed failure if plex_id is missing after trying to initialize/prepare
            if not final_plex_id and should_init : # Specifically log for the case where init was attempted
                 # This logging is similar to the one in _initialize_new_plex_item if plex_id is not found,
                 # but this one is the final decision point in the main function.
                pq_item_plex_id_info_final = "N/A"
                if pos < len(playqueue.items):
                    item_at_pos_final = playqueue.items[pos]
                    if hasattr(item_at_pos_final, "plex_id"):
                        pq_item_plex_id_info_final = "plex_id: %s" % item_at_pos_final.plex_id
                    elif hasattr(item_at_pos_final, "title"):
                        pq_item_plex_id_info_final = "title: %s (no plex_id)" % item_at_pos_final.title
                    else:
                        pq_item_plex_id_info_final = "Unknown item (no plex_id)"
                LOG.error('try_identify_and_set_plex_item: No Plex id obtained for init path. PlayerID: %s, KodiID: %s, Path: %s, PQ_info: %s',
                          playerid, current_kodi_id, current_path, pq_item_plex_id_info_final)

            self._reset_player_state_and_item(playerid)
            return None

        return self._finalize_and_update_status(playerid, item, container_key, final_path, final_kodi_id, final_kodi_type, final_plex_id, final_plex_type, player_info)

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

        json_item = None
        # Attempt 1: Get item with full properties
        try:
            json_item = js.get_item(playerid, properties=['id', 'type', 'file', 'title', 'label'])
        except KeyError: # Should ideally not happen if js.get_item handles its own KeyErrors
            LOG.debug('_json_item: KeyError during initial js.get_item call for playerid %s', playerid)
            # Proceed to minimal properties call as json_item is still None
        
        if json_item is None:
            LOG.debug("_json_item: Initial js.get_item with full properties failed for playerid %s. Trying minimal properties.", playerid)
            try:
                json_item_minimal = js.get_item(playerid, properties=['type', 'file', 'title'])
            except KeyError: # Should ideally not happen
                LOG.debug('_json_item: KeyError during minimal js.get_item call for playerid %s', playerid)
                json_item_minimal = None # Ensure it's None

            if json_item_minimal is None:
                LOG.debug("_json_item: Minimal js.get_item also failed for playerid %s. Returning (None, None, None).", playerid)
                return None, None, None
            else:
                LOG.debug("_json_item: Minimal js.get_item succeeded for playerid %s: %s", playerid, json_item_minimal)
                json_item = json_item_minimal
        else:
            LOG.debug('_json_item: Initial js.get_item with full properties succeeded for playerid %s: %s', playerid, json_item)

        # At this point, json_item is either from the first successful call, the second (minimal) successful call, or it's None if both failed (covered by return above).
        # However, the logic implies if the first call fails and json_item becomes None, the second call's result (json_item_minimal)
        # is assigned to json_item. So, if we reach here, json_item should hold some data.

        kodi_id = json_item.get('id') # Will be None if 'id' is not in json_item (e.g., from minimal call)
        item_type = json_item.get('type')
        path = json_item.get('file')

        LOG.debug('_json_item: Extracted for playerid %s - KodiID: %s, Type: %s, Path: %s', playerid, kodi_id, item_type, path)
        
        return kodi_id, item_type, path

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
