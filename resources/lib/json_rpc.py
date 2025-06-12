#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Collection of functions using the Kodi JSON RPC interface.
See http://kodi.wiki/view/JSON-RPC_API
"""
from json import loads, dumps
from logging import getLogger # Added import
from xbmc import executeJSONRPC

from . import kodi_constants, timing, variables as v

log = getLogger('PLEX.json_rpc') # Added logger instance

JSON_FROM_KODITYPE = {
    v.KODI_TYPE_MOVIE: ('VideoLibrary.GetMovieDetails',
                        kodi_constants.FIELDS_MOVIES),
    v.KODI_TYPE_SHOW: ('VideoLibrary.GetTVShowDetails',
                       kodi_constants.FIELDS_TVSHOWS),
    v.KODI_TYPE_SEASON: ('VideoLibrary.GetSeasonDetails',
                         kodi_constants.FIELDS_SEASON),
    v.KODI_TYPE_EPISODE: ('VideoLibrary.GetEpisodeDetails',
                          kodi_constants.FIELDS_EPISODES),
    v.KODI_TYPE_ARTIST: ('AudioLibrary.GetArtistDetails',
                         kodi_constants.FIELDS_ARTISTS),
    v.KODI_TYPE_ALBUM: ('AudioLibrary.GetAlbumDetails',
                        kodi_constants.FIELDS_ALBUMS),
    v.KODI_TYPE_SONG: ('AudioLibrary.GetSongDetails',
                       kodi_constants.FIELDS_SONGS),
    v.KODI_TYPE_SET: ('VideoLibrary.GetMovieSetDetails',
                      []),
}


class JsonRPC(object):
    """
    Used for all Kodi JSON RPC calls.
    """
    id_ = 1
    version = "2.0"

    def __init__(self, method, **kwargs):
        """
        Initialize with the Kodi method, e.g. 'Player.GetActivePlayers'
        """
        self.method = method
        self.params = None
        for arg in kwargs:
            self.arg = arg

    def _query(self):
        query = {
            'jsonrpc': self.version,
            'id': self.id_,
            'method': self.method,
        }
        if self.params is not None:
            query['params'] = self.params
        return dumps(query)

    def execute(self, params=None):
        """
        Pass any params as a dict. Will return Kodi's answer as a dict.
        """
        self.params = params
        return loads(executeJSONRPC(self._query()))


def get_players():
    """
    Returns all the active Kodi players (usually 3) in a dict:
    {
        'video': {'playerid': int, 'type': 'video'}
        'audio': ...
        'picture': ...
    }
    """
    ret = {}
    for player in JsonRPC("Player.GetActivePlayers").execute()['result']:
        player['playerid'] = int(player['playerid'])
        ret[player['type']] = player
    return ret


def get_player_ids():
    """
    Returns a list of all the active Kodi player ids (usually 3) as int
    """
    ret = []
    for player in list(get_players().values()):
        ret.append(player['playerid'])
    return ret


def get_playlist_id(typus):
    """
    Returns the corresponding Kodi playlist id as an int
        typus: Kodi playlist types: 'video', 'audio' or 'picture'

    Returns None if nothing was found
    """
    for playlist in get_playlists():
        if playlist.get('type') == typus:
            return playlist.get('playlistid')


def get_playlists():
    """
    Returns a list of all the Kodi playlists, e.g.
        [
            {u'playlistid': 0, u'type': u'audio'},
            {u'playlistid': 1, u'type': u'video'},
            {u'playlistid': 2, u'type': u'picture'}
        ]
    """
    try:
        ret = JsonRPC('Playlist.GetPlaylists').execute()['result']
    except KeyError:
        ret = []
    return ret


def get_volume():
    """
    Returns the Kodi volume as an int between 0 (min) and 100 (max)
    """
    return JsonRPC('Application.GetProperties').execute(
        {"properties": ['volume']})['result']['volume']


def set_volume(volume):
    """
    Set's the volume (for Kodi overall, not only a player).
    Feed with an int
    """
    return JsonRPC('Application.SetVolume').execute({"volume": volume})


def get_muted():
    """
    Returns True if Kodi is muted, False otherwise
    """
    return JsonRPC('Application.GetProperties').execute(
        {"properties": ['muted']})['result']['muted']


def play():
    """
    Toggles all Kodi players to play
    """
    for playerid in get_player_ids():
        JsonRPC("Player.PlayPause").execute({"playerid": playerid,
                                             "play": True})


def pause():
    """
    Pauses playback for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.PlayPause").execute({"playerid": playerid,
                                             "play": False})


def stop():
    """
    Stops playback for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.Stop").execute({"playerid": playerid})


def seek_to(offset):
    """
    Seeks all Kodi players to offset [int] in seconds
    """
    for playerid in get_player_ids():
        return JsonRPC("Player.Seek").execute(
            {"playerid": playerid,
             "value": {'time': timing.millis_to_kodi_time(int(offset * 1000))}})


def smallforward():
    """
    Small step forward for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.Seek").execute({"playerid": playerid,
                                        "value": "smallforward"})


def smallbackward():
    """
    Small step backward for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.Seek").execute({"playerid": playerid,
                                        "value": "smallbackward"})


def skipnext():
    """
    Skips to the next item to play for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.GoTo").execute({"playerid": playerid,
                                        "to": "next"})


def skipprevious():
    """
    Skips to the previous item to play for all Kodi players
    Using a HACK to make sure we're not just starting same item over again
    """
    for playerid in get_player_ids():
        try:
            skipto(get_position(playerid) - 1)
        except (KeyError, TypeError):
            pass


def wont_work_skipprevious():
    """
    Skips to the previous item to play for all Kodi players
    """
    for playerid in get_player_ids():
        JsonRPC("Player.GoTo").execute({"playerid": playerid,
                                        "to": "previous"})


def skipto(position):
    """
    Skips to the position [int] of the current playlist
    """
    for playerid in get_player_ids():
        JsonRPC("Player.GoTo").execute({"playerid": playerid,
                                        "to": position})


def input_up():
    """
    Tells Kodi the user pushed up
    """
    return JsonRPC("Input.Up").execute()


def input_down():
    """
    Tells Kodi the user pushed down
    """
    return JsonRPC("Input.Down").execute()


def input_left():
    """
    Tells Kodi the user pushed left
    """
    return JsonRPC("Input.Left").execute()


def input_right():
    """
    Tells Kodi the user pushed left
    """
    return JsonRPC("Input.Right").execute()


def input_select():
    """
    Tells Kodi the user pushed select
    """
    return JsonRPC("Input.Select").execute()


def input_home():
    """
    Tells Kodi the user pushed home
    """
    return JsonRPC("Input.Home").execute()


def input_back():
    """
    Tells Kodi the user pushed back
    """
    return JsonRPC("Input.Back").execute()


def input_sendtext(text):
    """
    Tells Kodi the user sent text [unicode]
    """
    return JsonRPC("Input.SendText").execute({'test': text, 'done': False})


def playlist_get_items(playlistid):
    """
        playlistid:    [int] id of the Kodi playlist

    Returns a list of Kodi playlist items as dicts with the keys specified in
    properties. Or an empty list if unsuccessful. Example:
    [
        {
            u'file':u'smb://nas/PlexMovies/3 Idiots 2009 pt1.mkv',
            u'title': u'3 Idiots',
            u'type': u'movie',          # IF possible! Else key missing
            u'id': 3,                   # IF possible! Else key missing
            u'label': u'3 Idiots'}]
    """
    reply = JsonRPC('Playlist.GetItems').execute({
        'playlistid': playlistid,
        'properties': ['title', 'file']
    })
    try:
        reply = reply['result']['items']
    except KeyError:
        reply = []
    return reply


def playlist_add(playlistid, item):
    """
    Adds an item to the Kodi playlist with id playlistid. item is either the
    dict
        {'file': filepath as string}
    or
        {kodi_type: kodi_id}

    Returns a dict with the key 'error' if unsuccessful.
    """
    return JsonRPC('Playlist.Add').execute({'playlistid': playlistid,
                                            'item': item})


def playlist_insert(params):
    """
    Insert item(s) into playlist. Does not work for picture playlists (aka
    slideshows). params is the dict
    {
        'playlistid': [int]
        'position': [int]
        'item': <item>
    }
    item is either the dict
            {'file': filepath as string}
        or
            {kodi_type: kodi_id}
    Returns a dict with the key 'error' if something went wrong.
    """
    return JsonRPC('Playlist.Insert').execute(params)


def playlist_remove(playlistid, position):
    """
    Removes the playlist item at position from the playlist
        position:   [int]

    Returns a dict with the key 'error' if something went wrong.
    """
    return JsonRPC('Playlist.Remove').execute({'playlistid': playlistid,
                                               'position': position})


def get_setting(setting):
    """
    Returns the Kodi setting (GetSettingValue), a [str], or None if not
    possible
    """
    try:
        ret = JsonRPC('Settings.GetSettingValue').execute(
            {'setting': setting})['result']['value']
    except (KeyError, TypeError):
        ret = None
    return ret


def set_setting(setting, value):
    """
    Sets the Kodi setting, a [str], to value
    """
    return JsonRPC('Settings.SetSettingValue').execute(
        {'setting': setting, 'value': value})


def get_tv_shows(params):
    """
    Returns a list of tv shows for params (check the Kodi wiki)
    """
    ret = JsonRPC('VideoLibrary.GetTVShows').execute(params)
    try:
        ret = ret['result']['tvshows']
    except (KeyError, TypeError):
        ret = []
    return ret


def get_episodes(params):
    """
    Returns a list of tv show episodes for params (check the Kodi wiki)
    """
    ret = JsonRPC('VideoLibrary.GetEpisodes').execute(params)
    try:
        ret = ret['result']['episodes']
    except (KeyError, TypeError):
        ret = []
    return ret


def get_item(playerid):
            # Docstring remains as is
            try:
                # Request only 'title' and 'file' properties
                response = JsonRPC('Player.GetItem').execute({
                    'playerid': playerid,
                    'properties': ['title', 'file']
                })
                # Safer access to dictionary keys
                if response and 'result' in response and isinstance(response['result'], dict) and 'item' in response['result']:
                    return response['result']['item']
                else:
                    log.debug("get_item: Unexpected response structure for playerid %s: %s", playerid, response)
                    return None
            except Exception as e:
                log.debug("get_item: Failed to get item for playerid %s - %s: %s", playerid, type(e).__name__, e)
                return None


def get_current_audio_stream_index(playerid):
    """
    Returns the currently active audio stream index [int]
    """
    return JsonRPC('Player.GetProperties').execute({
        'playerid': playerid,
        'properties': ['currentaudiostream']})['result']['currentaudiostream']['index']


def get_current_video_stream_index(playerid):
    """
    Returns the currently active video stream index [int]
    """
    return JsonRPC('Player.GetProperties').execute({
        'playerid': playerid,
        'properties': ['currentvideostream']})['result']['currentvideostream']['index']


def get_current_subtitle_stream_index(playerid):
    """
    Returns the currently active subtitle stream index [int] or None if there
    are no subs
    """
    try:
        return JsonRPC('Player.GetProperties').execute({
            'playerid': playerid,
            'properties': ['currentsubtitle', ]})['result']['currentsubtitle']['index']
    except (KeyError, TypeError):
        pass


def get_subtitle_enabled(playerid):
    """
    Returns True if a subtitle is currently enabled, False otherwise.
    """
    return JsonRPC('Player.GetProperties').execute({
        'playerid': playerid,
        'properties': ['subtitleenabled', ]})['result']['subtitleenabled']


def get_player_props(playerid):
    """
    Returns a dict for the active Kodi player with the following values:
    {
        'type'          [str] the Kodi player type, e.g. 'video'
        'time'          The current item's time in Kodi time
        'totaltime'     The current item's total length in Kodi time
        'speed'         [int] playback speed, 0 is paused, 1 is playing
        'shuffled'      [bool] True if shuffled
        'repeat'        [str] 'off', 'one', 'all'
        'position'      [int] position in playlist (or -1)
        'playlistid'    [int] the Kodi playlist id (or -1)
    }
    """
    return JsonRPC('Player.GetProperties').execute({
        'playerid': playerid,
        'properties': ['type',
                       'time',
                       'totaltime',
                       'speed',
                       'shuffled',
                       'repeat',
                       'position',
                       'playlistid',
                       'currentvideostream',
                       'currentaudiostream',
                       'subtitleenabled',
                       'currentsubtitle']})['result']


def get_position(playerid):
    """
    Returns the currently playing item's position [int] within the playlist
    """
    return JsonRPC('Player.GetProperties').execute({
        'playerid': playerid,
        'properties': ['position']})['result']['position']


def current_audiostream(playerid):
    """
    Returns a dict of the active audiostream for playerid [int]:
    {
        'index':    [int], audiostream index
        'language': [str]
        'name':     [str]
        'codec':    [str]
        'bitrate':  [int]
        'channels': [int]
    }
    or an empty dict if unsuccessful
    """
    ret = JsonRPC('Player.GetProperties').execute(
        {'properties': ['currentaudiostream'], 'playerid': playerid})
    try:
        ret = ret['result']['currentaudiostream']
    except (KeyError, TypeError):
        ret = {}
    return ret


def current_subtitle(playerid):
    """
    Returns a dict of the active subtitle for playerid [int]:
    {
        'index':    [int], subtitle index
        'language': [str]
        'name':     [str]
    }
    or an empty dict if unsuccessful
    """
    ret = JsonRPC('Player.GetProperties').execute(
        {'properties': ['currentsubtitle'], 'playerid': playerid})
    try:
        ret = ret['result']['currentsubtitle']
    except (KeyError, TypeError):
        ret = {}
    return ret


def subtitle_enabled(playerid):
    """
    Returns True if a subtitle is enabled, False otherwise
    """
    ret = JsonRPC('Player.GetProperties').execute(
        {'properties': ['subtitleenabled'], 'playerid': playerid})
    try:
        ret = ret['result']['subtitleenabled']
    except (KeyError, TypeError):
        ret = False
    return ret


def ping():
    """
    Pings the JSON RPC interface
    """
    return JsonRPC('JSONRPC.Ping').execute()


def activate_window(window, parameters):
    """
    Pass the parameters as str/unicode to open the corresponding window
    """
    return JsonRPC('GUI.ActivateWindow').execute({'window': window,
                                                  'parameters': [parameters]})


def settings_getsections():
    '''
    Retrieve all Kodi settings sections
    '''
    return JsonRPC('Settings.GetSections').execute({'level': 'expert'})


def settings_getcategories():
    '''
    Retrieve all Kodi settings categories (one level below sections)
    '''
    return JsonRPC('Settings.GetCategories').execute({'level': 'expert'})


def settings_getsettings(filter_params):
    '''
    Get all the settings for
        filter_params = {'category': <str>, 'section': <str>}
        e.g.          = {'category':'videoplayer', 'section':'player'}
    '''
    return JsonRPC('Settings.GetSettings').execute({
        'level': 'expert',
        'filter': filter_params
    })


def settings_getsettingvalue(setting):
    '''
    Pass in the setting id as a string (as retrieved from settings_getsettings),
    e.g. 'videoplayer.autoplaynextitem' or None is something went wrong
    '''
    ret = JsonRPC('Settings.GetSettingValue').execute({'setting': setting})
    try:
        ret = ret['result']['value']
    except (TypeError, KeyError):
        ret = None
    return ret


def settings_setsettingvalue(setting, value):
    '''
    Set the Kodi setting (str) to value (type depends, see JSON wiki)
    '''
    return JsonRPC('Settings.SetSettingValue').execute({
        'setting': setting,
        'value': value
    })


def item_details(kodi_id, kodi_type):
    '''
    Returns the Kodi item dict for this item
    '''
    json, fields = JSON_FROM_KODITYPE[kodi_type]
    ret = JsonRPC(json).execute({'%sid' % kodi_type: kodi_id,
                                'properties': fields})
    try:
        ret = ret['result']['%sdetails' % kodi_type]
    except (KeyError, TypeError):
        return {}
    if kodi_type == v.KODI_TYPE_SHOW:
        # append watched counts to tvshow details
        ret["extraproperties"] = {
            "totalseasons": str(ret["season"]),
            "totalepisodes": str(ret["episode"]),
            "watchedepisodes": str(ret["watchedepisodes"]),
            "unwatchedepisodes": str(ret["episode"] - ret["watchedepisodes"])
        }
    return ret

