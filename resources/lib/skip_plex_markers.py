#!/usr/bin/env python
# -*- coding: utf-8 -*-
import xbmc

from .windows.skip_marker import SkipMarkerDialog
from . import app, utils, variables as v


# Supported types of markers that can be skipped; values here will be
# displayed to the user when skipping is available
MARKERS = {
    'intro': (utils.lang(30525), 'enableSkipIntro', 'enableAutoSkipIntro'), # Skip intro
    'credits': (utils.lang(30526), 'enableSkipCredits', 'enableAutoSkipCredits'),  # Skip credits
    'commercial': (utils.lang(30530), 'enableSkipCommercials', 'enableAutoSkipCommercials'),  # Skip commercial
}


def _should_skip_credits_popup():
    """
    Returns True if we should suppress the PKC credits popup.
    This happens when Up Next is enabled AND found a next episode.
    If there's no next episode (last episode), we still show PKC credits popup.
    """
    if not xbmc.getCondVisibility('System.AddonIsEnabled(service.upnext)'):
        return False

    # Check if Up Next actually sent a signal (found next episode)
    with app.APP.lock_playqueues:
        if len(app.PLAYSTATE.active_players) != 1:
            return False
        playerid = list(app.PLAYSTATE.active_players)[0]
        player_state = app.PLAYSTATE.player_states.get(playerid, {})
        return player_state.get('upnext_signal_sent', False)

def skip_markers(markers, markers_hidden):
    try:
        progress = app.APP.player.getTime()
    except RuntimeError:
        # XBMC is not playing any media file yet
        return
    within_marker = None
    marker_definition = None
    for start, end, typus, _ in markers:
        marker_definition = MARKERS[typus]
        # Skip the PKC credits popup if Up Next is enabled (Up Next handles it)
        if typus == 'credits' and _should_skip_credits_popup():
            continue
        # The "-1" is important since timestamps/seeks are not exact and we
        # could end up in an endless loop within start & end
        # see https://github.com/croneter/PlexKodiConnect/issues/2002
        if utils.settings(marker_definition[1]) == "true" and start <= progress < end - 1:
            within_marker = typus
            break
        elif typus in markers_hidden:
            # reset the marker when escaping its time window
            # this allows the skip button to show again if you rewind
            del markers_hidden[typus]
    if within_marker is not None:
        if within_marker in markers_hidden:
            # the user did not click the button within the enableAutoHideSkipTime time
            # so it was hidden. don't show this marker
            return

        if app.APP.skip_markers_dialog is None:
            # WARNING: This Dialog only seems to work if called from the main
            # thread. Otherwise, onClick and onAction won't work
            app.APP.skip_markers_dialog = SkipMarkerDialog(
                'script-plex-skip_marker.xml',
                v.ADDON_PATH,
                'default',
                '1080i',
                marker_message=marker_definition[0],
                marker_end=end,
                creation_time=progress)
            if utils.settings(marker_definition[2]) == "true":
                app.APP.skip_markers_dialog.seekTimeToEnd()
            else:
                app.APP.skip_markers_dialog.show()

        elif utils.settings("enableAutoHideSkip") == "true" and \
            app.APP.skip_markers_dialog.creation_time is not None and \
            (progress - app.APP.skip_markers_dialog.creation_time) > int(utils.settings("enableAutoHideSkipTime")):
            # the dialog has been open for more than X seconds, so close it and
            # mark it as hidden so it won't show up again within the start/end window
            markers_hidden[within_marker] = True
            app.APP.skip_markers_dialog.close()
            app.APP.skip_markers_dialog = None

    elif app.APP.skip_markers_dialog is not None:
        app.APP.skip_markers_dialog.close()
        app.APP.skip_markers_dialog = None

def check():
    with app.APP.lock_playqueues:
        if len(app.PLAYSTATE.active_players) != 1:
            return
        playerid = list(app.PLAYSTATE.active_players)[0]
        markers = app.PLAYSTATE.player_states[playerid]['markers']
        markers_hidden = app.PLAYSTATE.player_states[playerid]['markers_hidden']
    if not markers:
        return
    skip_markers(markers, markers_hidden)
