#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import platform
import re

import xbmc
import xbmcvfs
from xbmcaddon import Addon

from . import path_ops

# Paths are in unicode, otherwise Windows will throw fits
# For any file operations with KODI function, use encoded strings!

# PLEX SETTINGS
# Percent of playback progress for watching item as partially watched. Anything
# more and item will NOT be marked as partially, but fully watched
MARK_PLAYED_AT = 0.9
# Decide whether to use end credits markers
# 0:at selected threshold percentage
# 1:at final credits marker position
# 2:at first credits marker position
# 3:earliest between threshold percent and first credits marker
LIBRARY_VIDEO_PLAYED_AT_BEHAVIOUR = 3
# How many seconds of playback do we ignore before marking an item as partially
# watched?
# This setting cannot (yet) be changed in the PMS settings
IGNORE_SECONDS_AT_START = 60
# Corresponding KODI SETTINGS
# for playback and marking a video as watched
# For default values listed here see
# https://kodi.wiki/view/HOW-TO:Modify_automatic_watch_and_resume_points
KODI_IGNOREPERCENTATEND = 0.08
KODI_PLAYCOUNTMINIMUMPERCENT = 0.9
KODI_IGNORESECONDSATSTART = 180

_ADDON = Addon('plugin.video.plexkodiconnect')
ADDON_NAME = 'PlexKodiConnect'
ADDON_ID = 'plugin.video.plexkodiconnect'
ADDON_VERSION = _ADDON.getAddonInfo('version')
ADDON_PATH = _ADDON.getAddonInfo('path')
ADDON_FOLDER = xbmcvfs.translatePath('special://home')
ADDON_PROFILE = xbmcvfs.translatePath(_ADDON.getAddonInfo('profile'))

# Used e.g. for json_rpc
KODI_AUDIO_PLAYER_ID = 0
KODI_VIDEO_PLAYER_ID = 1
KODI_PHOTO_PLAYER_ID = 2

KODILANGUAGE = xbmc.getLanguage(xbmc.ISO_639_1)
KODIVERSION = int(xbmc.getInfoLabel("System.BuildVersion")[:2])
KODILONGVERSION = xbmc.getInfoLabel('System.BuildVersion')
KODI_PROFILE = xbmcvfs.translatePath("special://profile")

if xbmc.getCondVisibility('system.platform.osx'):
    DEVICE = "MacOSX"
elif xbmc.getCondVisibility("system.platform.uwp"):
    DEVICE = "Microsoft UWP"
elif xbmc.getCondVisibility('system.platform.atv2'):
    DEVICE = "AppleTV2"
elif xbmc.getCondVisibility('system.platform.ios'):
    DEVICE = "iOS"
elif xbmc.getCondVisibility('system.platform.windows'):
    DEVICE = "Windows"
elif xbmc.getCondVisibility('system.platform.raspberrypi'):
    DEVICE = "RaspberryPi"
elif xbmc.getCondVisibility('system.platform.linux'):
    DEVICE = "Linux"
elif xbmc.getCondVisibility('system.platform.android'):
    DEVICE = "Android"
else:
    DEVICE = "Unknown"

try:
    MODEL = platform.release() or 'Unknown'
except IOError:
    # E.g. iOS
    # It seems that Kodi doesn't allow python to spawn subprocesses in order to
    # determine the system name
    # See https://github.com/psf/requests/issues/4434
    MODEL = 'Unknown'

DEVICENAME = _ADDON.getSetting('deviceName')
if not DEVICENAME:
    DEVICENAME = xbmc.getInfoLabel('System.FriendlyName')
    _ADDON.setSetting('deviceName', DEVICENAME)
DEVICENAME = DEVICENAME.replace(":", "")
DEVICENAME = DEVICENAME.replace("/", "-")
DEVICENAME = DEVICENAME.replace("\\", "-")
DEVICENAME = DEVICENAME.replace("<", "")
DEVICENAME = DEVICENAME.replace(">", "")
DEVICENAME = DEVICENAME.replace("*", "")
DEVICENAME = DEVICENAME.replace("?", "")
DEVICENAME = DEVICENAME.replace('|', "")
DEVICENAME = DEVICENAME.replace('(', "")
DEVICENAME = DEVICENAME.replace(')', "")
DEVICENAME = DEVICENAME.strip()

COMPANION_PORT = int(_ADDON.getSetting('companionPort'))

# Unique ID for this Plex client; also see clientinfo.py
PKC_MACHINE_IDENTIFIER = None

# Minimal PKC version needed for the Kodi database - otherwise need to recreate
MIN_DB_VERSION = '3.9.4'

DB_VIDEO_VERSION = None
DB_VIDEO_PATH = None
DB_MUSIC_VERSION = None
DB_MUSIC_PATH = None
DB_TEXTURE_VERSION = None
DB_TEXTURE_PATH = None
DB_PLEX_PATH = xbmcvfs.translatePath("special://database/plex.db")
DB_PLEX_COPY_PATH = xbmcvfs.translatePath("special://database/plex-copy.db")

EXTERNAL_SUBTITLE_TEMP_PATH = xbmcvfs.translatePath(
    "special://profile/addon_data/%s/temp/" % ADDON_ID)


# Multiply Plex time by this factor to receive Kodi time
PLEX_TO_KODI_TIMEFACTOR = 1.0 / 1000.0


# Playlist stuff
PLAYLIST_PATH = os.path.join(KODI_PROFILE, 'playlists')
PLAYLIST_PATH_MIXED = os.path.join(PLAYLIST_PATH, 'mixed')
PLAYLIST_PATH_VIDEO = os.path.join(PLAYLIST_PATH, 'video')
PLAYLIST_PATH_MUSIC = os.path.join(PLAYLIST_PATH, 'music')

PLEX_TYPE_AUDIO_PLAYLIST = 'audio'
PLEX_TYPE_VIDEO_PLAYLIST = 'video'
PLEX_TYPE_PHOTO_PLAYLIST = 'photo'
KODI_TYPE_AUDIO_PLAYLIST = 'music'
KODI_TYPE_VIDEO_PLAYLIST = 'video'
KODI_TYPE_PHOTO_PLAYLIST = None  # Not supported yet
KODI_PLAYLIST_TYPE_FROM_PLEX = {
    PLEX_TYPE_AUDIO_PLAYLIST: KODI_TYPE_AUDIO_PLAYLIST,
    PLEX_TYPE_VIDEO_PLAYLIST: KODI_TYPE_VIDEO_PLAYLIST
}
PLEX_PLAYLIST_TYPE_FROM_KODI = {
    KODI_TYPE_AUDIO_PLAYLIST: PLEX_TYPE_AUDIO_PLAYLIST,
    KODI_TYPE_VIDEO_PLAYLIST: PLEX_TYPE_VIDEO_PLAYLIST
}


# All the Plex types as communicated in the PMS xml replies
PLEX_TYPE_VIDEO = 'video'
PLEX_TYPE_MOVIE = 'movie'
PLEX_TYPE_CLIP = 'clip'  # e.g. trailers
PLEX_TYPE_SET = 'collection'  # sets/collections
PLEX_TYPE_GENRE = 'genre'
PLEX_TYPE_MIXED = 'mixed'

PLEX_TYPE_EPISODE = 'episode'
PLEX_TYPE_SEASON = 'season'
PLEX_TYPE_SHOW = 'show'

PLEX_TYPE_AUDIO = 'music'
PLEX_TYPE_SONG = 'track'
PLEX_TYPE_ALBUM = 'album'
PLEX_TYPE_ARTIST = 'artist'
PLEX_TYPE_MUSICVIDEO = 'musicvideo'

PLEX_TYPE_PHOTO = 'photo'

PLEX_TYPE_PLAYLIST = 'playlist'
PLEX_TYPE_CHANNEL = 'channel'

PLEX_TYPE_GAME = 'game'

# E.g. PMS answer when hitting the PMS endpoint /hubs/search
PLEX_TYPE_TAG = 'tag'

# PlexKodiConnect does not support all (content) types
# e.g. Plex Arcade games
UNSUPPORTED_PLEX_TYPES = (PLEX_TYPE_GAME, )

# Used for /:/timeline XML messages
PLEX_PLAYLIST_TYPE_VIDEO = 'video'
PLEX_PLAYLIST_TYPE_AUDIO = 'music'
PLEX_PLAYLIST_TYPE_PHOTO = 'photo'

KODI_PLAYLIST_TYPE_VIDEO = 'video'
KODI_PLAYLIST_TYPE_AUDIO = 'audio'
KODI_PLAYLIST_TYPE_PHOTO = 'picture'

KODI_PLAYLIST_TYPE_FROM_PLEX_PLAYLIST_TYPE = {
    PLEX_PLAYLIST_TYPE_VIDEO: KODI_PLAYLIST_TYPE_VIDEO,
    PLEX_PLAYLIST_TYPE_AUDIO: KODI_PLAYLIST_TYPE_AUDIO,
    PLEX_PLAYLIST_TYPE_PHOTO: KODI_PLAYLIST_TYPE_PHOTO
}

# All the Kodi types as e.g. used in the JSON API
KODI_TYPE_VIDEO = 'video'
KODI_TYPE_MOVIE = 'movie'
KODI_TYPE_SET = 'set'  # for movie sets of several movies
KODI_TYPE_CLIP = 'video'  # e.g. trailers

KODI_TYPE_EPISODE = 'episode'
KODI_TYPE_SEASON = 'season'
KODI_TYPE_SHOW = 'tvshow'

KODI_TYPE_AUDIO = 'audio'
KODI_TYPE_SONG = 'song'
KODI_TYPE_ALBUM = 'album'
KODI_TYPE_ARTIST = 'artist'
KODI_TYPE_MUSICVIDEO = 'musicvideo'

KODI_TYPE_PHOTO = 'photo'

KODI_TYPE_PLAYLIST = 'playlist'
KODI_TYPE_GENRE = 'genre'

# Kodi content types, primarily used for xbmcplugin.setContent()
CONTENT_TYPE_MOVIE = 'movies'
CONTENT_TYPE_SHOW = 'tvshows'
CONTENT_TYPE_SEASON = 'seasons'
CONTENT_TYPE_EPISODE = 'episodes'
CONTENT_TYPE_ARTIST = 'artists'
CONTENT_TYPE_ALBUM = 'albums'
CONTENT_TYPE_SONG = 'songs'
CONTENT_TYPE_CLIP = 'movies'
CONTENT_TYPE_SET = 'sets'
CONTENT_TYPE_PHOTO = 'images'
CONTENT_TYPE_GENRE = 'genres'
CONTENT_TYPE_VIDEO = 'videos'
CONTENT_TYPE_PLAYLIST = 'playlists'
CONTENT_TYPE_FILE = 'files'
CONTENT_TYPE_MUSICVIDEO = 'musicvideos'


KODI_VIDEOTYPES = (
    KODI_TYPE_VIDEO,
    KODI_TYPE_MOVIE,
    KODI_TYPE_SHOW,
    KODI_TYPE_SEASON,
    KODI_TYPE_EPISODE,
    KODI_TYPE_SET,
    KODI_TYPE_CLIP
)

PLEX_VIDEOTYPES = (
    PLEX_TYPE_VIDEO,
    PLEX_TYPE_MOVIE,
    PLEX_TYPE_SHOW,
    PLEX_TYPE_SEASON,
    PLEX_TYPE_EPISODE,
    PLEX_TYPE_SET,
    PLEX_TYPE_CLIP,
    PLEX_TYPE_MIXED,  # MIXED SEEMS TO ALWAYS REFER TO VIDEO!
)

KODI_AUDIOTYPES = (
    KODI_TYPE_SONG,
    KODI_TYPE_ALBUM,
    KODI_TYPE_ARTIST,
)

PLEX_AUDIOTYPES = (
    PLEX_TYPE_SONG,
    PLEX_TYPE_ALBUM,
    PLEX_TYPE_ARTIST,
)

# Translation tables

ADDON_TYPE = {
    PLEX_TYPE_MOVIE: 'plugin.video.plexkodiconnect.movies',
    PLEX_TYPE_CLIP: 'plugin.video.plexkodiconnect.movies',
    PLEX_TYPE_EPISODE: 'plugin.video.plexkodiconnect.tvshows',
    PLEX_TYPE_SEASON: 'plugin.video.plexkodiconnect.tvshows',
    PLEX_TYPE_SHOW: 'plugin.video.plexkodiconnect.tvshows',
    PLEX_TYPE_SONG: 'plugin.video.plexkodiconnect',
    PLEX_TYPE_ALBUM: 'plugin.video.plexkodiconnect',
    PLEX_TYPE_ARTIST: 'plugin.video.plexkodiconnect',
    PLEX_TYPE_PLAYLIST: 'plugin.video.plexkodiconnect'
}

ITEMTYPE_FROM_PLEXTYPE = {
    PLEX_TYPE_MOVIE: 'Movies',
    PLEX_TYPE_SEASON: 'TVShows',
    KODI_TYPE_EPISODE: 'TVShows',
    PLEX_TYPE_SHOW: 'TVShows',
    PLEX_TYPE_ARTIST: 'Music',
    PLEX_TYPE_ALBUM: 'Music',
    PLEX_TYPE_SONG: 'Music',
}

ITEMTYPE_FROM_KODITYPE = {
    KODI_TYPE_MOVIE: 'Movies',
    KODI_TYPE_SEASON: 'TVShows',
    KODI_TYPE_EPISODE: 'TVShows',
    KODI_TYPE_SHOW: 'TVShows',
    KODI_TYPE_ARTIST: 'Music',
    KODI_TYPE_ALBUM: 'Music',
    KODI_TYPE_SONG: 'Music',
}

KODITYPE_FROM_PLEXTYPE = {
    PLEX_TYPE_MOVIE: KODI_TYPE_MOVIE,
    PLEX_TYPE_CLIP: KODI_TYPE_CLIP,
    PLEX_TYPE_EPISODE: KODI_TYPE_EPISODE,
    PLEX_TYPE_SEASON: KODI_TYPE_SEASON,
    PLEX_TYPE_SHOW: KODI_TYPE_SHOW,
    PLEX_TYPE_SONG: KODI_TYPE_SONG,
    PLEX_TYPE_ARTIST: KODI_TYPE_ARTIST,
    PLEX_TYPE_ALBUM: KODI_TYPE_ALBUM,
    PLEX_TYPE_PHOTO: KODI_TYPE_PHOTO,
    'XXXXXX': 'musicvideo',
    'XXXXXXX': 'genre',
    PLEX_TYPE_PLAYLIST: KODI_TYPE_PLAYLIST
}

PLEX_TYPE_FROM_KODI_TYPE = {
    KODI_TYPE_VIDEO: PLEX_TYPE_VIDEO,
    KODI_TYPE_MOVIE: PLEX_TYPE_MOVIE,
    KODI_TYPE_SET: PLEX_TYPE_SET,
    KODI_TYPE_EPISODE: PLEX_TYPE_EPISODE,
    KODI_TYPE_SEASON: PLEX_TYPE_SEASON,
    KODI_TYPE_SHOW: PLEX_TYPE_SHOW,
    KODI_TYPE_ARTIST: PLEX_TYPE_ARTIST,
    KODI_TYPE_ALBUM: PLEX_TYPE_ALBUM,
    KODI_TYPE_SONG: PLEX_TYPE_SONG,
    KODI_TYPE_AUDIO: PLEX_TYPE_AUDIO,
    KODI_TYPE_PHOTO: PLEX_TYPE_PHOTO
}

KODI_PLAYLIST_TYPE_FROM_PLEX_TYPE = {
    PLEX_TYPE_VIDEO: KODI_TYPE_VIDEO,
    PLEX_TYPE_MOVIE: KODI_TYPE_VIDEO,
    PLEX_TYPE_EPISODE: KODI_TYPE_VIDEO,
    PLEX_TYPE_SEASON: KODI_TYPE_VIDEO,
    PLEX_TYPE_SHOW: KODI_TYPE_VIDEO,
    PLEX_TYPE_CLIP: KODI_TYPE_VIDEO,
    PLEX_TYPE_ARTIST: KODI_TYPE_AUDIO,
    PLEX_TYPE_ALBUM: KODI_TYPE_AUDIO,
    PLEX_TYPE_SONG: KODI_TYPE_AUDIO,
    PLEX_TYPE_AUDIO: KODI_TYPE_AUDIO,
    PLEX_TYPE_PHOTO: KODI_TYPE_PHOTO
}

KODI_PLAYLIST_TYPE_FROM_KODI_TYPE = {
    KODI_TYPE_VIDEO: KODI_TYPE_VIDEO,
    KODI_TYPE_MOVIE: KODI_TYPE_VIDEO,
    KODI_TYPE_EPISODE: KODI_TYPE_VIDEO,
    KODI_TYPE_SEASON: KODI_TYPE_VIDEO,
    KODI_TYPE_SHOW: KODI_TYPE_VIDEO,
    KODI_TYPE_CLIP: KODI_TYPE_VIDEO,
    KODI_TYPE_ARTIST: KODI_TYPE_AUDIO,
    KODI_TYPE_ALBUM: KODI_TYPE_AUDIO,
    KODI_TYPE_SONG: KODI_TYPE_AUDIO,
    KODI_TYPE_AUDIO: KODI_TYPE_AUDIO,
    KODI_TYPE_PHOTO: KODI_TYPE_PHOTO
}

REMAP_TYPE_FROM_PLEXTYPE = {
    PLEX_TYPE_MOVIE: 'movie',
    PLEX_TYPE_CLIP: 'movie',
    PLEX_TYPE_SHOW: 'tv',
    PLEX_TYPE_SEASON: 'tv',
    PLEX_TYPE_EPISODE: 'tv',
    PLEX_TYPE_ARTIST: 'music',
    PLEX_TYPE_ALBUM: 'music',
    PLEX_TYPE_SONG: 'music',
    PLEX_TYPE_PHOTO: 'photo'
}


ICON_FROM_PLEXTYPE = {
    PLEX_TYPE_VIDEO: 'DefaultAddonAlbumInfo.png',
    PLEX_TYPE_MOVIE: 'DefaultMovies.png',
    PLEX_TYPE_EPISODE: 'DefaultTvShows.png',
    PLEX_TYPE_SEASON: 'DefaultTvShows.png',
    PLEX_TYPE_SHOW: 'DefaultTvShows.png',
    PLEX_TYPE_CLIP: 'DefaultAddonAlbumInfo.png',
    PLEX_TYPE_ARTIST: 'DefaultArtist.png',
    PLEX_TYPE_ALBUM: 'DefaultAlbumCover.png',
    PLEX_TYPE_SONG: 'DefaultMusicSongs.png',
    PLEX_TYPE_AUDIO: 'DefaultAddonAlbumInfo.png',
    PLEX_TYPE_PHOTO: 'DefaultPicture.png',
    PLEX_TYPE_PLAYLIST: 'DefaultPlaylist.png',
    'mixed': 'DefaultAddonAlbumInfo.png'
}

TRANSLATION_FROM_PLEXTYPE = {
    PLEX_TYPE_MOVIE: 342,
    PLEX_TYPE_EPISODE: 20360,
    PLEX_TYPE_SEASON: 20373,
    PLEX_TYPE_SHOW: 20343,
    PLEX_TYPE_SONG: 134,
    PLEX_TYPE_ARTIST: 133,
    PLEX_TYPE_ALBUM: 132,
    PLEX_TYPE_PHOTO: 1,
}

PLEX_TYPE_FROM_WEBSOCKET = {
    1: PLEX_TYPE_MOVIE,
    2: PLEX_TYPE_SHOW,
    3: PLEX_TYPE_SEASON,
    4: PLEX_TYPE_EPISODE,
    8: PLEX_TYPE_ARTIST,
    9: PLEX_TYPE_ALBUM,
    10: PLEX_TYPE_SONG,
    12: PLEX_TYPE_CLIP,
    15: 'playlist',
    18: PLEX_TYPE_SET
}

PLEX_TYPE_NUMBER_FROM_PLEX_TYPE = {
    PLEX_TYPE_MOVIE: 1,
    PLEX_TYPE_SHOW: 2,
    PLEX_TYPE_SEASON: 3,
    PLEX_TYPE_EPISODE: 4,
    PLEX_TYPE_ARTIST: 8,
    PLEX_TYPE_ALBUM: 9,
    PLEX_TYPE_SONG: 10,
    PLEX_TYPE_CLIP: 12,
    PLEX_TYPE_PLAYLIST: 15,
    PLEX_TYPE_SET: 18
}


# To be used with e.g. Kodi Widgets
CONTENT_FROM_PLEX_TYPE = {
    PLEX_TYPE_MOVIE: CONTENT_TYPE_MOVIE,
    PLEX_TYPE_SHOW: CONTENT_TYPE_SHOW,
    PLEX_TYPE_SEASON: CONTENT_TYPE_SEASON,
    PLEX_TYPE_EPISODE: CONTENT_TYPE_EPISODE,
    PLEX_TYPE_ARTIST: CONTENT_TYPE_ARTIST,
    PLEX_TYPE_ALBUM: CONTENT_TYPE_ALBUM,
    PLEX_TYPE_SONG: CONTENT_TYPE_SONG,
    PLEX_TYPE_CLIP: CONTENT_TYPE_CLIP,
    PLEX_TYPE_SET: CONTENT_TYPE_SET,
    PLEX_TYPE_PHOTO: CONTENT_TYPE_PHOTO,
    PLEX_TYPE_GENRE: CONTENT_TYPE_GENRE,
    PLEX_TYPE_VIDEO: CONTENT_TYPE_VIDEO,
    PLEX_TYPE_PLAYLIST: CONTENT_TYPE_PLAYLIST,
    PLEX_TYPE_CHANNEL: CONTENT_TYPE_FILE,
    PLEX_TYPE_TAG: CONTENT_TYPE_FILE,
    'mixed': CONTENT_TYPE_SHOW,
    None: CONTENT_TYPE_FILE
}


# Plex profile for transcoding and direct streaming
# Uses the empty Generic.xml at Plex Media Server/Resources/Profiles for any
# Playback decisions
PLATFORM = 'Generic'
# Version seems to be irrelevant for the generic platform
PLATFORM_VERSION = '1.0.0'
# Overrides (replace=true) any existing entries in generic.xml
STREAMING_HEADERS = {
    'X-Plex-Client-Profile-Extra':
        # Would allow to DirectStream anything, but seems to be rather faulty
        # 'add-transcode-target('
        #  'type=videoProfile&'
        #  'context=streaming&'
        #  'protocol=hls&'
        #  'container=mpegts&'
        #  'videoCodec=h264,*&'
        #  'audioCodec=aac,*&'
        #  'subtitleCodec=ass,pgs,vobsub,srt,*&'
        #  'replace=true)'
        ('add-transcode-target('
         'type=videoProfile&'
         'context=streaming&'
         'protocol=hls&'
         'container=mpegts&'
         'videoCodec=h264&'
         'audioCodec=aac,flac,vorbis,opus,ac3,eac3,mp3,mp2,pcm&'
         'replace=true)'
         # '+add-transcode-target('
         # 'type=subtitleProfile&'
         # 'context=all&'
         # 'container=ass&'
         # 'codec=ass&'
         # 'replace=true)'
         '+add-direct-play-profile('
         'type=videoProfile&'
         'container=*&'
         'videoCodec=*&'
         'audioCodec=*&'
         'subtitleCodec=*&'
         'replace=true)')
}

PLAYBACK_METHOD_DIRECT_PATH = 0
PLAYBACK_METHOD_DIRECT_PLAY = 1
PLAYBACK_METHOD_DIRECT_STREAM = 2
PLAYBACK_METHOD_TRANSCODE = 3

EXPLICIT_PLAYBACK_METHOD = {
    PLAYBACK_METHOD_DIRECT_PATH: 'DirectPath',
    PLAYBACK_METHOD_DIRECT_PLAY: 'DirectPlay',
    PLAYBACK_METHOD_DIRECT_STREAM: 'DirectStream',
    PLAYBACK_METHOD_TRANSCODE: 'Transcode',
    None: None
}


KODI_TO_PLEX_ARTWORK = {
    'poster': 'thumb',
    'banner': 'banner',
    'fanart': 'art'
}

KODI_TO_PLEX_ARTWORK_EPISODE = {
    'season.poster': 'parentThumb',
    'tvshow.poster': 'grandparentThumb',
    'tvshow.fanart': 'grandparentArt',
    # 'tvshow.banner': 'banner'  # Not included in PMS episode metadata
}

# Might be implemented in the future: 'icon', 'landscape' (16:9)
ALL_KODI_ARTWORK = (
    'thumb',
    'poster',
    'banner',
    'clearart',
    'clearlogo',
    'fanart',
    'discart',
    'landscape'
)

# we need to use a little mapping between fanart.tv arttypes and kodi artttypes
FANART_TV_TO_KODI_TYPE = [
    ('poster', 'poster'),
    ('logo', 'clearlogo'),
    ('musiclogo', 'clearlogo'),
    ('disc', 'discart'),
    ('clearart', 'clearart'),
    ('banner', 'banner'),
    ('clearlogo', 'clearlogo'),
    ('background', 'fanart'),
    ('showbackground', 'fanart'),
    ('characterart', 'characterart'),
    ('thumb', 'landscape')
]
# How many different backgrounds do we want to load from fanart.tv?
MAX_BACKGROUND_COUNT = 10


# extensions from:
# http://kodi.wiki/view/Features_and_supported_codecs#Format_support (RAW image
# formats, BMP, JPEG, GIF, PNG, TIFF, MNG, ICO, PCX and Targa/TGA)
KODI_SUPPORTED_IMAGES = (
    '.bmp',
    '.jpg',
    '.jpeg',
    '.gif',
    '.png',
    '.tiff',
    '.mng',
    '.ico',
    '.pcx',
    '.tga'
)


# Translation table from Alexa websocket commands to Plex Companion
ALEXA_TO_COMPANION = {
    'queryKey': 'key',
    'queryOffset': 'offset',
    'queryMachineIdentifier': 'machineIdentifier',
    'queryProtocol': 'protocol',
    'queryAddress': 'address',
    'queryPort': 'port',
    'queryContainerKey': 'containerKey',
    'queryToken': 'token',
}

# Kodi sort methods for xbmcplugin.addSortMethod()
SORT_METHODS_DIRECTORY = (
    'SORT_METHOD_UNSORTED',  # sorted as returned from Plex
    'SORT_METHOD_LABEL',
)

SORT_METHODS_PHOTOS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_LABEL',
    'SORT_METHOD_DATE',
    'SORT_METHOD_DATEADDED',
)

SORT_METHODS_CLIPS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_DURATION',
)

SORT_METHODS_MOVIES = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_DATEADDED',
    'SORT_METHOD_GENRE',
    'SORT_METHOD_VIDEO_RATING',
    'SORT_METHOD_VIDEO_USER_RATING',
    'SORT_METHOD_MPAA_RATING',
    'SORT_METHOD_DURATION',
    'SORT_METHOD_COUNTRY',
    'SORT_METHOD_STUDIO',
)

SORT_METHOD_TVSHOWS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_DATEADDED',
    'SORT_METHOD_VIDEO_RATING',
    'SORT_METHOD_VIDEO_USER_RATING',
    'SORT_METHOD_MPAA_RATING',
    'SORT_METHOD_COUNTRY',
    'SORT_METHOD_GENRE',
)

SORT_METHODS_EPISODES = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_EPISODE',
    'SORT_METHOD_DATEADDED',
    'SORT_METHOD_VIDEO_RATING',
    'SORT_METHOD_VIDEO_USER_RATING',
    'SORT_METHOD_MPAA_RATING',
    'SORT_METHOD_DURATION',
    'SORT_METHOD_FILE',
    'SORT_METHOD_FULLPATH',
)

SORT_METHODS_SONGS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_TRACKNUM',
    'SORT_METHOD_DURATION',
    'SORT_METHOD_ARTIST',
    'SORT_METHOD_ALBUM',
    'SORT_METHOD_SONG_RATING',
    'SORT_METHOD_SONG_USER_RATING'
)

SORT_METHODS_ARTISTS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_TRACKNUM',
    'SORT_METHOD_DURATION',
    'SORT_METHOD_ARTIST',
    'SORT_METHOD_ALBUM',
)

SORT_METHODS_ALBUMS = (
    'SORT_METHOD_UNSORTED',
    'SORT_METHOD_TITLE',
    'SORT_METHOD_TRACKNUM',
    'SORT_METHOD_DURATION',
    'SORT_METHOD_ARTIST',
    'SORT_METHOD_ALBUM',
)


PLEX_REPEAT_FROM_KODI_REPEAT = {
    'off': '0',
    'one': '1',
    'all': '2'   # does this work?!?
}

# Stream in PMS xml contains a streamType to distinguish the kind of stream
PLEX_STREAM_TYPE_FROM_STREAM_TYPE = {
    'video': '1',
    'audio': '2',
    'subtitle': '3'
}

# Encoding to be used for our m3u playlist files
# m3u files do not have encoding specified by definition, unfortunately.
# The filesystem encoding is generally utf-8
# For Windows, it can be either utf-8 or mbcs
# See https://docs.python.org/3/library/sys.html#sys.getfilesystemencoding
M3U_ENCODING = sys.getfilesystemencoding()


def _find_latest_db(db_kind_regex):
    '''
    Returns the tuple (filepath [str], version [int]) for the regex
    db_kind_regex with the highest version number at the end.
    '''
    database_path = xbmcvfs.translatePath('special://database')
    matches = []
    for root, dirs, files in path_ops.walk(database_path):
        for file in files:
            match = re.search(db_kind_regex, file, re.IGNORECASE)
            if not match:
                continue
            matches.append((path_ops.path.join(root, file),
                            int(match.group(1))))
    if not matches:
        raise RuntimeError(f'Database {db_kind_regex} not found in {files}')
    return max(matches, key=lambda x: x[1])


def database_paths():
    '''
    Set the Kodi database paths - PKC will choose the HIGHEST available
    database version. You should thus never downgrade Kodi.
    Will raise RuntimeError if the current Kodi version is not supported or
    if a certain DB is not found.
    '''
    if KODIVERSION not in (19, 20, 21, 22):
        raise RuntimeError(f'Kodiversion {KODIVERSION} not supported by PKC')

    thismodule = sys.modules[__name__]
    types = ((r'^MyVideos(\d+)\.db$', 'DB_VIDEO_PATH', 'DB_VIDEO_VERSION'),
             (r'^MyMusic(\d+)\.db$', 'DB_MUSIC_PATH', 'DB_MUSIC_VERSION'),
             (r'^Textures(\d+)\.db$', 'DB_TEXTURE_PATH', 'DB_TEXTURE_VERSION'))
    for regex, pathstring, versionstring in types:
        db_path, db_version = _find_latest_db(regex)
        setattr(thismodule, pathstring, db_path)
        setattr(thismodule, versionstring, db_version)
