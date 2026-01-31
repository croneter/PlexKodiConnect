#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Up Next integration for PlexKodiConnect
Sends episode information to the Up Next add-on so it can display
"next episode" notifications during playback.

See https://github.com/im85288/service.upnext/wiki/Integration
"""
from logging import getLogger
from json import dumps
from base64 import b64encode

import xbmc

from . import variables as v
from . import plex_functions as PF
from .plex_api import API

LOG = getLogger('PLEX.upnext')


def _get_art_from_api(api):
    """
    Returns a dict with artwork URLs for Up Next from a plex API object.
    """
    art = {
        'thumb': '',
        'tvshow.clearart': '',
        'tvshow.clearlogo': '',
        'tvshow.fanart': '',
        'tvshow.landscape': '',
        'tvshow.poster': '',
    }
    # Get episode thumb
    thumb = api.one_artwork('thumb')
    if thumb:
        art['thumb'] = thumb
    # Get show artwork
    grandparent_thumb = api.one_artwork('grandparentThumb')
    if grandparent_thumb:
        art['tvshow.poster'] = grandparent_thumb
    grandparent_art = api.one_artwork('grandparentArt')
    if grandparent_art:
        art['tvshow.fanart'] = grandparent_art
    return art


def _episode_info(api):
    """
    Returns a dict with episode information for Up Next from a plex API object.
    """
    return {
        'episodeid': api.plex_id,
        'tvshowid': api.grandparent_id(),
        'title': api.title() or '',
        'art': _get_art_from_api(api),
        'season': api.season_number() or 0,
        'episode': api.index() or 0,
        'showtitle': api.grandparent_title() or '',
        'plot': api.plot() or '',
        'playcount': api.viewcount() or 0,
        'rating': api.rating() or 0,
        'firstaired': api.premiere_date() or '',
        'runtime': api.runtime() or 0,
    }


def _get_next_episode_api(current_api):
    """
    Returns the API object for the next episode after the current one.
    Returns None if something went wrong or there is no next episode.
    """
    xml = PF.show_episodes(current_api.grandparent_id())
    if xml is None:
        return None
    for counter, episode in enumerate(xml):
        api = API(episode)
        if api.plex_id == current_api.plex_id:
            break
    else:
        LOG.debug('Did not find the episode with Plex id %s for show %s: %s',
                  current_api.plex_id, current_api.grandparent_id(),
                  current_api.grandparent_title())
        return None
    try:
        return API(xml[counter + 1])
    except IndexError:
        # Was the last episode
        LOG.debug('No next episode - this was the last one')
        return None


def _upnext_signal(data):
    """
    Sends the Up Next signal via JSON RPC.
    This is the recommended way according to Up Next documentation.
    """
    sender = '%s.SIGNAL' % v.ADDON_ID
    encoded_data = b64encode(dumps(data).encode('utf-8')).decode('ascii')
    params = {
        'sender': sender,
        'message': 'upnext_data',
        'data': [encoded_data],
    }
    result = xbmc.executeJSONRPC(dumps({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'JSONRPC.NotifyAll',
        'params': params,
    }))
    LOG.debug('Up Next signal sent. Result: %s', result)


def send_upnext_signal(current_api, notification_time=None):
    """
    Send the Up Next signal if there is a next episode.

    Args:
        current_api: The API object of the currently playing episode
        notification_time: Optional time in seconds before the end to show
                          the notification. If None, Up Next uses its default.

    Returns:
        True if signal was sent, False otherwise
    """
    if current_api.plex_type != v.PLEX_TYPE_EPISODE:
        LOG.debug('Not an episode - skipping Up Next signal')
        return False

    next_api = _get_next_episode_api(current_api)
    if next_api is None:
        LOG.debug('No next episode available for Up Next')
        return False

    LOG.debug('Preparing Up Next signal for episode "%s" -> "%s"',
              current_api.title(), next_api.title())

    # Build the play_url for the next episode
    # This URL will be called by Up Next to start playback
    play_url = 'plugin://%s?plex_id=%s&plex_type=%s&mode=play' % (
        v.ADDON_ID,
        next_api.plex_id,
        v.PLEX_TYPE_EPISODE
    )

    # Build the data structure for Up Next
    upnext_data = {
        'current_episode': _episode_info(current_api),
        'next_episode': _episode_info(next_api),
        'play_url': play_url,
    }

    # Add notification_time if provided (e.g., from Plex credits markers)
    if notification_time is not None:
        upnext_data['notification_time'] = notification_time

    LOG.debug('Sending Up Next data: current="%s" S%02dE%02d, next="%s" S%02dE%02d',
              current_api.grandparent_title(),
              current_api.season_number() or 0,
              current_api.index() or 0,
              next_api.grandparent_title(),
              next_api.season_number() or 0,
              next_api.index() or 0)

    _upnext_signal(upnext_data)
    return True


def _get_total_seconds_from_kodi_time(total_time):
    """
    Convert Kodi time dict to total seconds.

    Args:
        total_time: Dict with 'hours', 'minutes', 'seconds' keys

    Returns:
        Total seconds as int, or 0 if total_time is None
    """
    if not total_time:
        return 0
    return (total_time.get('hours', 0) * 3600 +
            total_time.get('minutes', 0) * 60 +
            total_time.get('seconds', 0))


def _calculate_notification_time(marker, total_seconds, marker_name):
    """
    Calculate notification time from a credits marker.

    Args:
        marker: Tuple (start_time, end_time) in seconds
        total_seconds: Total duration in seconds
        marker_name: Name of the marker for logging

    Returns:
        Notification time in seconds before end, or None if invalid
    """
    if marker and total_seconds > 0 and marker[0] < total_seconds:
        notification_time = total_seconds - marker[0]
        LOG.debug('Using %s for Up Next: %s seconds before end',
                  marker_name, notification_time)
        return notification_time
    return None


def get_notification_time_from_markers(status):
    """
    Get the notification time from Plex credits markers if available.

    Args:
        status: The player status dict containing markers info

    Returns:
        The notification time in seconds before the end, or None if not available
    """
    total_seconds = _get_total_seconds_from_kodi_time(status.get('totaltime'))

    # First check for first credits marker (intro to credits)
    first_credits = status.get('first_credits_marker')
    notification_time = _calculate_notification_time(
        first_credits, total_seconds, 'first credits marker')
    if notification_time is not None:
        return notification_time

    # Fall back to final credits marker
    final_credits = status.get('final_credits_marker')
    return _calculate_notification_time(
        final_credits, total_seconds, 'final credits marker')
