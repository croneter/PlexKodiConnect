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
    log.debug('Comparing new Kodi playqueue %s with our play queue %s',
              new, old)
    for i, new_item in enumerate(new):
        if (new_item['file'].startswith('plugin://') and
                not new_item['file'].startswith(PLUGIN)):
            # Ignore new media added by other addons
            continue
        for j, old_item in enumerate(old):

            if app.APP.stop_pkc:
                # Chances are that we got an empty Kodi playlist due to
                # Kodi exit
                return
            try:
                if (old_item.file.startswith('plugin://') and not old_item.file.startswith(PLUGIN)):
                    # Ignore media by other addons
                    continue
            except AttributeError:
                # were not passed a filename; ignore
                pass
            if 'id' in new_item:
                identical = (old_item.kodi_id == new_item['id'] and
                             old_item.kodi_type == new_item['type'])
            else:
                try:
                    plex_id = int(utils.REGEX_PLEX_ID.findall(new_item['file'])[0])
                except IndexError:
                    log.debug('Comparing paths directly as a fallback')
                    identical = old_item.file == new_item['file']
                else:
                    identical = plex_id == old_item.plex_id
            if j == 0 and identical:
                del old[0], index[0]
                break
            elif identical:
                log.debug('Playqueue item %s moved to position %s',
                          index[j], i)
                try:
                    PL.move_playlist_item(playqueue, index[j], i)
                except exceptions.PlaylistError:
                    log.error('Could not modify playqueue positions')
                    log.error('This is likely caused by mixing audio and '
                              'video tracks in the Kodi playqueue')
                del old[j], index[i]
                break
        else:
            log.debug('Detected new Kodi element at position %s: %s ',
                      i, new_item)
            try:
                if playqueue.id is None:
                    PL.init_plex_playqueue(playqueue, kodi_item=new_item)
                else:
                    PL.add_item_to_plex_playqueue(playqueue,
                                                  i,
                                                  kodi_item=new_item)
            except exceptions.PlaylistError:
                # Could not add the element
                pass
            except KeyError:
                # Catches KeyError from PL.verify_kodi_item()
                # Hack: Kodi already started playback of a new item and we
                # started playback already using kodimonitors
                # PlayBackStart(), but the Kodi playlist STILL only shows
                # the old element. Hence ignore playlist difference here
                log.debug('Detected an outdated Kodi playlist - ignoring')
                return
            except IndexError:
                # This is really a hack - happens when using Addon Paths
                # and repeatedly  starting the same element. Kodi will then
                # not pass kodi id nor file path AND will also not
                # start-up playback. Hence kodimonitor kicks off playback.
                # Also see kodimonitor.py - _playlist_onadd()
                pass
            else:
                for j in range(i, len(index)):
                    index[j] += 1
    for i in reversed(index):
        if app.APP.stop_pkc:
            # Chances are that we got an empty Kodi playlist due to
            # Kodi exit
            return
        log.debug('Detected deletion of playqueue element at pos %s', i)
        try:
            PL.delete_playlist_item_from_PMS(playqueue, i)
        except exceptions.PlaylistError:
            log.error('Could not delete PMS element from position %s', i)
            log.error('This is likely caused by mixing audio and '
                      'video tracks in the Kodi playqueue')
    log.debug('Done comparing playqueues')
