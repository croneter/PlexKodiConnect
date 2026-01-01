[![Installation-Instructions](https://img.shields.io/badge/wiki-installation-brightgreen.svg?maxAge=60&style=flat)](https://github.com/croneter/PlexKodiConnect/wiki/Installation)
[![FAQ](https://img.shields.io/badge/wiki-FAQ-brightgreen.svg?maxAge=60&style=flat)](https://github.com/croneter/PlexKodiConnect/wiki/faq)
[![Forum](https://img.shields.io/badge/forum-plex-orange.svg?maxAge=60&style=flat)](https://forums.plex.tv/discussion/210023/plexkodiconnect-let-kodi-talk-to-your-plex)
[![Donate](https://img.shields.io/badge/donate-kofi-blue.svg)](https://ko-fi.com/A8182EB)

[![GitHub issues](https://img.shields.io/github/issues/croneter/PlexKodiConnect.svg?maxAge=60&style=flat)](https://github.com/croneter/PlexKodiConnect/issues) [![GitHub pull requests](https://img.shields.io/github/issues-pr/croneter/PlexKodiConnect.svg?maxAge=60&style=flat)](https://github.com/croneter/PlexKodiConnect/pulls)

# PlexKodiConnect (PKC)
**Combine the best frontend media player Kodi with the best multimedia backend server Plex**

PKC synchronizes your media from your Plex server to the native Kodi database. Hence:
- Use virtually any other Kodi add-on
- Use any Kodi skin, completely customize Kodi's look
- Browse your media very fluently (cached artwork)
- Automatically get additional artwork (more than Plex offers)
- Use Plex features with a Kodi interface

Have a look at [some screenshots](https://github.com/croneter/PlexKodiConnect/wiki/Some-PKC-Screenshots) to see what's possible. 

### Content
* [**Download and Installation**](#download-and-installation)
* [**Warning**](#warning)
* [**PKC Features**](#pkc-features)
* [**Additional Artwork**](#additional-artwork)
* [**Donations**](#donations)
* [**Request a New Feature**](#request-a-new-feature)
* [**Issues and Bugs**](#issues-and-bugs)
* [**Credits**](#credits)

### Download and Installation
See here for detailed instructions: [Installation](https://github.com/croneter/PlexKodiConnect/wiki/Installation)

### Warning
Use at your own risk! This plugin assumes that you manage all your videos with Plex (and none with Kodi). You might lose data already stored in the Kodi video and music databases as this plugin directly changes them. Don't worry if you want Plex to manage all your media (like you should ;-)). 

Some people argue that PKC is 'hacky' because of the way it directly accesses the Kodi database. See [here for a more thorough discussion](https://github.com/croneter/PlexKodiConnect/wiki/Is-PKC-a-hack). 

### PKC Features

- Support for Kodi 21 Omega, Kodi 20 Nexus, Kodi 19 Matrix
- Preliminary support for Kodi 22 Piers. Keep in mind that Piers is still in early alpha version - any issues you encounter are probably caused by that. Please do not use nightly versions as they tend to break stuff. To install PKC on Kodi Piers, simply use the PKC repo for Kodi Omega
- Kodi 18 Leia is supported up until PKC version 3.10.7
- [Skip commercials/advertisements](https://support.plex.tv/articles/115003944134-removing-commercials/), [skip intros](https://support.plex.tv/articles/skip-content/) and [skip credits](https://support.plex.tv/articles/credits-detection/)
- [Amazon Alexa voice recognition](https://www.plex.tv/apps/streaming-devices/amazon-alexa)
- [Cinema Trailers & Extras](https://support.plex.tv/articles/202934883-cinema-trailers-extras/)
- If Plex did not provide a trailer, automatically get one using the Kodi add-on [The Movie Database](https://kodi.wiki/view/Add-on:The_Movie_Database)
- [Plex Watch Later / Plex It!](https://support.plex.tv/hc/en-us/sections/200211783-Plex-It-)
- [Plex Companion](https://support.plex.tv/hc/en-us/sections/200276908-Plex-Companion): fling Plex media (or anything else) from other Plex devices to PlexKodiConnect
- Automatically sync Plex playlists to Kodi playlists and vice-versa
- [Plex Transcoding](https://support.plex.tv/hc/en-us/articles/200250377-Transcoding-Media)
- Automatically download more artwork from [Fanart.tv](https://fanart.tv/), just like the Kodi addon [Artwork Downloader](http://kodi.wiki/view/Add-on:Artwork_Downloader)
- Automatically group movies into [movie sets](http://kodi.wiki/view/movie_sets)
- [Direct play](https://github.com/croneter/PlexKodiConnect/wiki/Direct-Play) from network paths (e.g. "\\\\server\\Plex\\movie.mkv"), something unique to PKC
- Delete PMS items from the Kodi context menu
- PKC is available in the following languages. [Please help and easily translate PKC!](https://www.transifex.com/croneter/pkc)
    + English
    + German
    + Czech, thanks @Pavuucek
    + Spanish, thanks @bartolomesoriano, @danichispa 
    + Danish, thanks @FIGHT
    + Italian, thanks @nikkux, @chicco83
    + Dutch, thanks @mvanbaak
    + French, thanks @lflforce, @ahivert, @Nox71, @CotzaDev, @vinch100, @Polymorph31, @jbnitro, @Elixir59 
    + Chinese Traditional, thanks @old2tan
    + Chinese Simplified, thanks @everdream
    + Norwegian, thanks @mjorud
    + Portuguese, thanks @goncalo532 
    + Russian, thanks @UncleStark
    + Hungarian, thanks @savage93
    + Ukrainian, thanks @uniss
    + Lithuanian, thanks @egidusm
    + Korean, thanks @so-o-bima

### Additional Artwork
PKC uses additional artwork for free from [TheMovieDB](https://www.themoviedb.org). Many thanks for lettings us use the API, guys!
[![Logo of TheMovieDB](themoviedb.png)](https://www.themoviedb.org)

### Donations
I'm not in any way affiliated with Plex. Thank you very much for a small donation via ko-fi.com and PayPal, Bitcoin or Ether if you appreciate PKC.  
**Full disclaimer:** I will see your name and address if you use PayPal. Rest assured that I will not share this with anyone. 

<a href='https://ko-fi.com/A8182EB' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi6.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
    
**Ethereum address for donations:    
0x0f57D98E08e617292D8bC0B3448dd79BF4Cf8e7F**

**Bitcoin address for donations:    
3BhwvUsqAGtAZodGUx4mTP7pTECjf1AejT**


### Request a New Feature
Kindly leave a detailed description as a new [GitHub issue](https://github.com/croneter/PlexKodiConnect/issues). 

### Issues and Bugs

Have a look at the [Github Issues Page](https://github.com/croneter/PlexKodiConnect/issues). Before you open your own issue, please read [How to report a bug](https://github.com/croneter/PlexKodiConnect/wiki/How-to-Report-A-Bug).


### Credits

- PlexKodiConnect shamelessly uses pretty much all the code of "Emby for Kodi" by the awesome Emby team (see https://github.com/MediaBrowser/plugin.video.emby). Thanks for sharing guys!!
- Plex Companion ("PlexBMC Helper") and other stuff were adapted from @Hippojay 's great work (see https://github.com/hippojay).
- The foundation of the Plex API is all iBaa's work (https://github.com/iBaa/PlexConnect).
