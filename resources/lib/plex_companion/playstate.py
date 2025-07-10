#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import requests
from threading import Thread

from .common import communicate, log_error, UUIDStr, Subscriber, timeline, \
    stopped_timeline, create_requests_session, proxy_params
from .playqueue import compare_playqueues
from .webserver import ThreadedHTTPServer, CompanionHandlerClassFactory
from .plexgdm import plexgdm

from .. import json_rpc as js
from .. import variables as v
from .. import backgroundthread
from .. import app
from .. import timing


# Disable annoying requests warnings
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()

log = getLogger('PLEX.companion.playstate')

TIMEOUT = (5, 5)

# How many seconds do we wait until we check again whether we are registered
# as a GDM Plex Companion Client?
GDM_COMPANION_CHECK = 120


def update_player_info(players):
    """
    Update the playstate info for other PKC "consumers"
    """
    for player in players.values():
        playerid = player['playerid']
        app.PLAYSTATE.player_states[playerid].update(js.get_player_props(playerid))
        app.PLAYSTATE.player_states[playerid]['volume'] = js.get_volume()
        app.PLAYSTATE.player_states[playerid]['muted'] = js.get_muted()


class PlaystateMgr(backgroundthread.KillableThread):
    """
    If Kodi plays something, tell the PMS about it and - if a Companion client
    is connected - tell the PMS Plex Companion piece of the PMS about it.
    Also checks whether an intro is currently playing, enabling the user to
    skip it.
    """
    daemon = True

    def __init__(self, companion_enabled):
        self.companion_enabled = companion_enabled
        self.subscribers = dict()
        self.s = None
        self.httpd = None
        self.stopped_timeline = stopped_timeline()
        self.gdm = plexgdm()
        msg = stopped_timeline()
        self.last_pms_msg = {
            0: msg[0].attrib,
            1: msg[1].attrib,
            2: msg[2].attrib
        }
        super().__init__()

    def _start_webserver(self):
        if self.httpd is None and self.companion_enabled:
            log.debug('Starting PKC Companion webserver on port %s', v.COMPANION_PORT)
            server_address = ('', v.COMPANION_PORT)
            HandlerClass = CompanionHandlerClassFactory(self)
            self.httpd = ThreadedHTTPServer(server_address, HandlerClass)
            self.httpd.timeout = 10.0
            t = Thread(target=self.httpd.serve_forever)
            t.start()

    def _stop_webserver(self):
        if self.httpd is not None:
            log.debug('Shutting down PKC Companion webserver')
            try:
                self.httpd.shutdown()
            except AttributeError:
                # Ensure thread-safety
                pass
            self.httpd = None

    def _get_requests_session(self):
        if self.s is None:
            self.s = create_requests_session()
        return self.s

    def _close_requests_session(self):
        if self.s is not None:
            try:
                self.s.close()
            except AttributeError:
                # "thread-safety" - Just in case s was set to None in the
                # meantime
                pass
            self.s = None

    def close_connections(self):
        """May also be called from another thread"""
        with app.APP.lock_subscriber:
            self._stop_webserver()
            self._close_requests_session()
            self.subscribers = dict()

    def send_stop(self):
        """
        If we're still connected to a PMS, tells the PMS that playback stopped
        """
        self.pms_timeline(None, self.stopped_timeline)
        self.companion_timeline(self.stopped_timeline)

    def check_subscriber(self, cmd):
        if not cmd.get('clientIdentifier'):
            return
        uuid = UUIDStr(cmd.get('clientIdentifier'))
        with app.APP.lock_subscriber:
            if cmd.get('path') == '/player/timeline/unsubscribe':
                if uuid in self.subscribers:
                    log.debug('Stop Plex Companion subscription for %s', uuid)
                    del self.subscribers[uuid]
            elif uuid not in self.subscribers:
                log.debug('Start new Plex Companion subscription for %s', uuid)
                self.subscribers[uuid] = Subscriber(self, cmd=cmd)
            else:
                try:
                    self.subscribers[uuid].command_id = int(cmd.get('commandID'))
                except TypeError:
                    pass

    def subscribe(self, uuid, command_id, url):
        log.debug('New Plex Companion subscriber %s: %s', uuid, url)
        with app.APP.lock_subscriber:
            self.subscribers[UUIDStr(uuid)] = Subscriber(self,
                                                         cmd=None,
                                                         uuid=uuid,
                                                         command_id=command_id,
                                                         url=url)

    def unsubscribe(self, uuid):
        log.debug('Unsubscribing Plex Companion client %s', uuid)
        with app.APP.lock_subscriber:
            try:
                del self.subscribers[UUIDStr(uuid)]
            except KeyError:
                pass

    def update_command_id(self, uuid, command_id):
        with app.APP.lock_subscriber:
            if uuid not in self.subscribers:
                return False
            self.subscribers[uuid].command_id = command_id
        return True

    def companion_timeline(self, message):
        state = 'stopped'
        for entry in message:
            if entry.get('state') != 'stopped':
                state = entry.get('state')
        with app.APP.lock_subscriber:
            for subscriber in self.subscribers.values():
                subscriber.send_timeline(message, state)

    def pms_timeline_per_player(self, playerid, message):
        """
        Sending the "normal", non-Companion playstate to the PMS works a bit
        differently
        """
        url = f'{app.CONN.server}/:/timeline'
        self._get_requests_session()

        # Check for transient items
        if app.PLAYSTATE.item and app.PLAYSTATE.item.plex_id is None:
            log.debug("PlaystateMgr: Skipping PMS timeline update for transient item (plex_id is None). PlayerID: %s, Item: %s", playerid, app.PLAYSTATE.item.title if hasattr(app.PLAYSTATE.item, 'title') else 'Unknown Title')
            # Update last_pms_msg state to stopped if the transient item itself has stopped,
            # to prevent sending stale 'playing' states for previous real items.
            if message[playerid].attrib.get('state') == 'stopped':
                 # Ensure playerid exists in last_pms_msg before updating
                if playerid not in self.last_pms_msg:
                    self.last_pms_msg[playerid] = {} # Initialize if not present
                self.last_pms_msg[playerid].update({'state': 'stopped'})
            return

        if message[playerid].attrib.get('state') != 'stopped':
            params = proxy_params()
            params.update(message[playerid].attrib)
            self.last_pms_msg[playerid] = params
        else:
            self.last_pms_msg[playerid].update({'state': 'stopped'})
            params = self.last_pms_msg[playerid]
        # Tell the PMS about our playstate progress
        try:
            req = communicate(self.s.get,
                              url,
                              timeout=TIMEOUT,
                              params=params)
        except requests.RequestException as error:
            log.error('Could not send the PMS timeline: %s', error)
            return
        except SystemExit:
            return
        if not req.ok:
            log_error(log.error, 'Failed reporting playback progress', req)

    def pms_timeline(self, players, message):
        players = players if players else \
            {0: {'playerid': 0}, 1: {'playerid': 1}, 2: {'playerid': 2}}
        for player in players.values():
            self.pms_timeline_per_player(player['playerid'], message)

    def wait_while_suspended(self):
        should_shutdown = super().wait_while_suspended()
        if not should_shutdown:
            self._start_webserver()
        return should_shutdown

    def run(self):
        app.APP.register_thread(self)
        log.info("----===## Starting PlaystateMgr ##===----")
        try:
            self._run()
        finally:
            # Make sure we're telling the PMS that playback will stop
            self.send_stop()
            # Cleanup
            self.close_connections()
            app.APP.deregister_thread(self)
            log.info("----===## PlaystateMgr stopped ##===----")

    def _run(self):
        signaled_playback_stop = True
        self._start_webserver()
        self.gdm.start()
        last_check = timing.unix_timestamp()
        while not self.should_cancel():
            if self.should_suspend():
                self.close_connections()
                if self.wait_while_suspended():
                    break
            # Check for Kodi playlist changes first
            with app.APP.lock_playqueues:
                for playqueue in app.PLAYQUEUES:
                    kodi_pl = js.playlist_get_items(playqueue.playlistid)
                    if playqueue.old_kodi_pl != kodi_pl:
                        is_music_playqueue = hasattr(playqueue, 'type') and playqueue.type == v.KODI_TYPE_AUDIO_PLAYLIST # Ensure 'v' is imported

                        if playqueue.id is None and (not app.SYNC.direct_paths or
                                                     app.PLAYSTATE.context_menu_play):
                            # Only initialize if directly fired up using direct
                            # paths. Otherwise let default.py do its magic
                            log.debug('Not yet initiating playback')
                        else:
                            if is_music_playqueue:
                                log.debug("PlaystateMgr: Music playqueue change detected. Calling compare_playqueues for playqueue.id: %s. Old len: %s, New len: %s",
                                          playqueue.id, len(playqueue.old_kodi_pl) if playqueue.old_kodi_pl else "N/A", len(kodi_pl))
                            
                            # compare old and new playqueue
                            compare_playqueues(playqueue, kodi_pl)
                            
                            if is_music_playqueue:
                                current_plex_ids = "N/A"
                                try:
                                    current_plex_ids = [item.plex_id for item in playqueue.items if hasattr(item, 'plex_id')]
                                except Exception as e:
                                    log.debug("PlaystateMgr: Error getting plex_ids for logging: %s", e)
                                log.debug("PlaystateMgr: compare_playqueues finished for music playqueue.id: %s. New playqueue.items len: %s, plex_ids: %s",
                                          playqueue.id, len(playqueue.items), current_plex_ids)

                        playqueue.old_kodi_pl = list(kodi_pl)
            # Make sure we are registered as a player
            now = timing.unix_timestamp()
            if now - last_check > GDM_COMPANION_CHECK:
                self.gdm.check_client_registration()
                last_check = now
            # Then check for Kodi playback
            players = js.get_players()
            if not players and signaled_playback_stop:
                self.sleep(1)
                continue
            elif not players:
                # Playback has just stopped, need to tell Plex
                self.send_stop()
                signaled_playback_stop = True
                self.sleep(1)
                continue
            elif not app.PLAYSTATE.item:
                # Not a Plex item currently playing - try to recover
                log.debug("PlaystateMgr: No app.PLAYSTATE.item set. Active players (from js.get_players()): %s. Attempting recovery.", players)
                if players:
                    active_player_ids = js.get_player_ids()
                    if active_player_ids:
                        playerid_to_recover = active_player_ids[0]
                        log.debug("PlaystateMgr: Attempting recovery for playerid: %s", playerid_to_recover)
                        try:
                            item_props = js.get_item(playerid_to_recover)
                            current_kodi_item_data_for_recovery = {
                                'player': {'playerid': playerid_to_recover},
                                'item': item_props if item_props else {} 
                            }
                            playqueue_to_recover = app.PLAYQUEUES[playerid_to_recover]

                            if app.APP.monitor: # Ensure monitor object exists
                                recovered_item = app.APP.monitor.try_identify_and_set_plex_item(
                                    playerid_to_recover,
                                    playqueue_to_recover,
                                    current_kodi_item_data_for_recovery
                                )
                                if recovered_item and app.PLAYSTATE.item:
                                    log.info("PlaystateMgr: Successfully recovered and set app.PLAYSTATE.item for playerid %s: %s", playerid_to_recover, app.PLAYSTATE.item.plex_id if hasattr(app.PLAYSTATE.item, 'plex_id') else "Unknown plex_id")
                                    # If recovery was successful, we might not want to sleep and continue immediately
                                    # but the original logic has a continue after sleep, so we'll maintain that pattern.
                                else:
                                    log.warning("PlaystateMgr: Failed to recover app.PLAYSTATE.item for playerid %s.", playerid_to_recover)
                            else:
                                log.error("PlaystateMgr: app.APP.monitor is not available. Cannot attempt recovery.")
                        except Exception as e:
                            log.error("PlaystateMgr: Error during recovery attempt for playerid %s: %s", playerid_to_recover, e, exc_info=True)
                    else:
                        log.debug("PlaystateMgr: Recovery requested, but no active player IDs found by js.get_player_ids().")
                else:
                    log.debug("PlaystateMgr: No app.PLAYSTATE.item and no active players detected by js.get_players() at recovery point.")
                
                self.sleep(1)
                continue
            else:
                # Update the playstate info, such as playback progress
                update_player_info(players)
                try:
                    message = timeline(players)
                except (TypeError, IndexError):
                    # We haven't had a chance to set the kodi_stream_index for
                    # the currently playing item. Just skip for now
                    self.sleep(1)
                    continue
                else:
                    # Kodi will started with 'stopped' - make sure we're
                    # waiting here until we got something playing or on pause.
                    for entry in message:
                        if entry.get('state') != 'stopped':
                            break
                    else:
                        continue
                    signaled_playback_stop = False
            # Send the playback progress info to the PMS
            self.pms_timeline(players, message)
            # Send the info to all Companion devices via the PMS
            self.companion_timeline(message)
            self.sleep(1)
