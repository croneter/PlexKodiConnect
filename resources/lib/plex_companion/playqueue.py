#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import copy

from ..plex_api import API
from .. import variables as v
from .. import app
from .. import utils
from .. import plex_functions as PF
from .. import playlist_func as PL
from .. import exceptions

log = getLogger('PLEX.companion.playqueue')

PLUGIN = 'plugin://%s' % v.ADDON_ID


def init_playqueue_from_plex_children(plex_id, transient_token=None):
    """
    Init a new playqueue e.g. from an album. Alexa does this

    Returns the playqueue
    """
    xml = PF.GetAllPlexChildren(plex_id)
    try:
        xml[0].attrib
    except (TypeError, IndexError, AttributeError):
        log.error('Could not download the PMS xml for %s', plex_id)
        return
    playqueue = app.PLAYQUEUES.from_plex_type(xml[0].attrib['type'])
    playqueue.clear()
    for i, child in enumerate(xml):
        api = API(child)
        try:
            PL.add_item_to_playlist(playqueue, i, plex_id=api.plex_id)
        except exceptions.PlaylistError:
            log.error('Could not add Plex item to our playlist: %s, %s',
                      child.tag, child.attrib)
    playqueue.plex_transient_token = transient_token
    log.debug('Firing up Kodi player')
    app.APP.player.play(playqueue.kodi_pl, None, False, 0)
    return playqueue


def compare_playqueues(playqueue, new_kodi_playqueue):
    """
    Used to poll the Kodi playqueue and update the Plex playqueue if needed
    """
    old = list(playqueue.items)
    # We might append to new_kodi_playqueue but will need the original
    # still back in the main loop
    new = copy.deepcopy(new_kodi_playqueue)
    index = list(range(0, len(old)))
    log.debug('compare_playqueues: Entered. Playqueue ID: %s, Old PKC len: %s, New Kodi len: %s', playqueue.id if playqueue else "N/A", len(old), len(new_kodi_playqueue))
    # log.debug('Comparing new Kodi playqueue %s with our play queue %s', new, old) # Original, more verbose log

    for i, new_item_data in enumerate(new): # Renamed new_item to new_item_data to avoid confusion with PlaylistItem instances
        if (new_item_data['file'].startswith('plugin://') and
                not new_item_data['file'].startswith(PLUGIN)):
            # Ignore new media added by other addons
            log.debug("compare_playqueues: Playqueue %s, item %s is from other addon, skipping.", playqueue.id if playqueue else "N/A", new_item_data.get('file', 'N/A'))
            continue
        for j, old_item_instance in enumerate(old): # Renamed old_item to old_item_instance

            if app.APP.stop_pkc:
                # Chances are that we got an empty Kodi playlist due to
                # Kodi exit
                return
            try:
                if (old_item_instance.file.startswith('plugin://') and not old_item_instance.file.startswith(PLUGIN)): # 여기가 수정되었습니다.
                    # Ignore media by other addons
                    continue
            except AttributeError:
                # were not passed a filename; ignore
                pass
            if 'id' in new_item_data: # Using new_item_data
                identical = (old_item_instance.kodi_id == new_item_data['id'] and # Using old_item_instance and new_item_data
                             old_item_instance.kodi_type == new_item_data['type']) # Using old_item_instance and new_item_data
            else:
                try:
                    plex_id = int(utils.REGEX_PLEX_ID.findall(new_item_data['file'])[0]) # Using new_item_data
                except IndexError:
                    log.debug('compare_playqueues: Playqueue %s, comparing paths directly as a fallback for item %s', playqueue.id if playqueue else "N/A", new_item_data.get('file', 'N/A'))
                    identical = old_item_instance.file == new_item_data['file'] # Using old_item_instance and new_item_data
                else:
                    identical = plex_id == old_item_instance.plex_id # Using old_item_instance
            if j == 0 and identical:
                log.debug("compare_playqueues: Playqueue %s, item at pos 0 (Kodi ID: %s) is identical, removing from consideration.", playqueue.id if playqueue else "N/A", old_item_instance.kodi_id if old_item_instance else "N/A")
                del old[0], index[0]
                break
            elif identical:
                log.debug('compare_playqueues: Playqueue %s, item %s (Kodi ID: %s) moved from old pos %s to new pos %s',
                          playqueue.id if playqueue else "N/A", old_item_instance.plex_id if old_item_instance else "N/A", old_item_instance.kodi_id if old_item_instance else "N/A", index[j], i)
                try:
                    PL.move_playlist_item(playqueue, index[j], i)
                except exceptions.PlaylistError:
                    log.error('compare_playqueues: Playqueue %s, could not modify playqueue positions.', playqueue.id if playqueue else "N/A")
                    log.error('This is likely caused by mixing audio and '
                              'video tracks in the Kodi playqueue')
                del old[j], index[j] # index[i] should be index[j] if we are removing the j-th element from old and index lists
                break
        else: # This else belongs to the inner for loop (for j, old_item_instance...)
            log.debug('compare_playqueues: Playqueue %s, detected new Kodi element at new pos %s: %s ',
                      playqueue.id if playqueue else "N/A", i, new_item_data)
            try:
                newly_added_item = None
                if playqueue.id is None:
                    # This will internally call playlist.items.append(item)
                    newly_added_item = PL.init_plex_playqueue(playqueue, kodi_item=new_item_data)
                    log.debug("compare_playqueues: Playqueue %s, initialized with new item, plex_id %s", playqueue.id if playqueue else "N/A", newly_added_item.plex_id if newly_added_item else "N/A")
                else:
                    # This will internally call playlist.items.append(item) and then move it
                    newly_added_item = PL.add_item_to_plex_playqueue(playqueue,
                                                  i,
                                                  kodi_item=new_item_data)
                    log.debug("compare_playqueues: Playqueue %s, adding item with inferred plex_id %s at pos %s", playqueue.id if playqueue else "N/A", newly_added_item.plex_id if newly_added_item else "N/A", i)
            except exceptions.PlaylistError as e:
                log.error('compare_playqueues: Playqueue %s, PlaylistError adding/initing item: %s. Item data: %s. Error: %s', playqueue.id if playqueue else "N/A", new_item_data.get('file', 'N/A'), new_item_data, e)
                # Could not add the element
                pass
            except KeyError as e:
                # Catches KeyError from PL.verify_kodi_item()
                # Hack: Kodi already started playback of a new item and we
                # started playback already using kodimonitors
                # PlayBackStart(), but the Kodi playlist STILL only shows
                # the old element. Hence ignore playlist difference here
                log.debug('compare_playqueues: Playqueue %s, KeyError (likely outdated Kodi playlist), ignoring. Item data: %s. Error: %s', playqueue.id if playqueue else "N/A", new_item_data, e)
                return
            except IndexError as e:
                # This is really a hack - happens when using Addon Paths
                # and repeatedly  starting the same element. Kodi will then
                # not pass kodi id nor file path AND will also not
                # start-up playback. Hence kodimonitor kicks off playback.
                # Also see kodimonitor.py - _playlist_onadd()
                log.debug('compare_playqueues: Playqueue %s, IndexError (likely Addon Paths issue), ignoring. Item data: %s. Error: %s', playqueue.id if playqueue else "N/A", new_item_data, e)
                pass
            else:
                # This for loop seems to adjust indices for items that were shifted due to an insert.
                # However, PL.add_item_to_plex_playqueue already handles moving the item to the correct position 'i'.
                # If PL.init_plex_playqueue was called, 'old' and 'index' were empty or reset, so this loop might not be relevant then.
                # Consider if this index adjustment is still needed as PL.add_item_to_plex_playqueue now handles position.
                # If an item is added at 'i', items originally at 'i' and later in 'old'/'index' are effectively shifted.
                # This loop correctly adjusts their original indices in the 'index' list.
                log.debug("compare_playqueues: Playqueue %s, adjusting indices from pos %s due to new item insertion.", playqueue.id if playqueue else "N/A", i)
                for k_loop_var in range(i, len(index)): # Renamed j to k_loop_var to avoid clash
                    index[k_loop_var] += 1

    # After iterating through new items, any remaining items in 'old' (tracked by 'index') are deletions.
    for i_loop_var in reversed(index): # Renamed i to i_loop_var
        if app.APP.stop_pkc:
            # Chances are that we got an empty Kodi playlist due to
            # Kodi exit
            log.debug("compare_playqueues: Playqueue %s, PKC stopping, returning from deletion loop.", playqueue.id if playqueue else "N/A")
            return
        
        item_to_delete_plex_id = "N/A"
        if playqueue and i_loop_var < len(playqueue.items) and hasattr(playqueue.items[i_loop_var], 'plex_id'):
             item_to_delete_plex_id = playqueue.items[i_loop_var].plex_id

        log.debug('compare_playqueues: Playqueue %s, detected deletion of element at old PKC pos %s (plex_id: %s)', playqueue.id if playqueue else "N/A", i_loop_var, item_to_delete_plex_id)
        try:
            PL.delete_playlist_item_from_PMS(playqueue, i_loop_var)
        except exceptions.PlaylistError:
            log.error('compare_playqueues: Playqueue %s, could not delete PMS element from position %s.', playqueue.id if playqueue else "N/A", i_loop_var)
            log.error('This is likely caused by mixing audio and '
                      'video tracks in the Kodi playqueue')
    log.debug('compare_playqueues: Finished. Playqueue ID: %s, Final PKC item count: %s', playqueue.id if playqueue else "N/A", len(playqueue.items) if playqueue else "N/A")
 
