# Test Plan: Music Playback Reporting and Regression

**1. Objective:**
   - To verify the implemented fixes for continuous music playback reporting to Plex Media Server.
   - To ensure that changes made for music playback do not introduce regressions in movie or TV show playback reporting.

**2. Prerequisites:**
   - Kodi (version compatible with the addon) installed with the modified PlexKodiConnect addon.
   - A Plex Media Server (PMS) accessible to the Kodi instance, with an active Plex account.
   - A music library in Plex with several playlists, at least one containing a minimum of 10-15 tracks.
   - Movie and TV show libraries populated in Plex.
   - Ability to access and review Kodi debug logs.
   - Ability to monitor the Plex Media Server's "Now Playing" or activity dashboard (e.g., via Plex Web App).

**3. Test Cases:**

   **3.1. Music Playback Reporting (Primary Test)**
      - **Test Case ID:** TP-MUSIC-001
      - **Description:** Verify continuous playback reporting to Plex for a long music playlist, ensuring each track transition is correctly handled.
      - **Steps:**
         1. Enable debug logging in Kodi (Settings -> System -> Logging -> Enable debug logging).
         2. Restart Kodi after enabling debug logs to ensure a clean log session.
         3. Navigate to a music playlist in Kodi (via the PlexKodiConnect addon interface) that contains at least 10-15 tracks.
         4. Start playing the playlist from the first track. Allow it to play through several tracks naturally.
         5. While tracks are playing, monitor the Plex server's "Now Playing" dashboard.
         6. After at least 5-7 tracks have played (or the whole playlist if shorter), stop playback.
         7. Observe Kodi debug logs in real-time if possible, or collect them after the test for detailed review (see TP-LOG-MUSIC-002).
      - **Expected Results:**
         1. Each track that starts playing in Kodi should appear promptly as "Now Playing" on the Plex server dashboard.
         2. The playback progress (timeline) for the currently playing music track should update regularly on the Plex server.
         3. Upon track completion, play counts should be correctly updated on the Plex server according to scrobble settings (e.g., marked as played).
         4. Kodi debug logs should show the message `PlayBackStart: Assigning to app.PLAYSTATE.item...` (or `try_identify_and_set_plex_item: Assigning to app.PLAYSTATE.item...`) for each music track that begins playing.
         5. Logs should *not* show repeated errors like `No Plex id obtained after all attempts - aborting playback report...` for music tracks.
         6. Logs related to `PlaystateMgr` recovery (`PlaystateMgr: Attempting recovery for playerid...`) should ideally *not* appear frequently during continuous music playback. If they do, it should be noted if the recovery attempt was logged as successful.
      - **Pass/Fail Criteria:** Pass if all played music tracks (minimum 5-7) are reported correctly to Plex, their progress is updated, and play counts are accurate, without any interruptions in reporting.

   **3.2. Log Review for Music Playback**
      - **Test Case ID:** TP-LOG-MUSIC-002
      - **Description:** Analyze Kodi debug logs for specific messages related to the implemented fixes and new refactoring after performing music playback tests (like TP-MUSIC-001).
      - **Steps:**
         1. Perform Test Case TP-MUSIC-001 ensuring debug logging is active.
         2. Collect the Kodi debug log file.
         3. Using a text editor or log viewer, search for the following log patterns (note: some log messages might be from `try_identify_and_set_plex_item` now):
            * `PlayBackStart: Pre-init check...` or `try_identify_and_set_plex_item: Pre-init check...`
            * `PlayBackStart: Initialize flag set to...` or `try_identify_and_set_plex_item: Initialize flag set to...`
            * `PlayBackStart: Post PL.init_plex_playqueue...` or `try_identify_and_set_plex_item: Post PL.init_plex_playqueue...` (should appear if `initialize` was true for an item).
            * `PlayBackStart: Assigning to app.PLAYSTATE.item...` or `try_identify_and_set_plex_item: Assigning to app.PLAYSTATE.item...` (should appear for each track).
            * `Initial plex_id fetch failed. Attempting fallback via playqueue.` (note occurrences and if the fallback was logged as successful).
            * `No Plex id obtained after all attempts - aborting playback report...` (this should NOT occur for music tracks that are correctly part of the Plex library).
            * `PlaystateMgr: Music playqueue change detected... Calling compare_playqueues...` (should appear as playlists are processed).
            * `PlaystateMgr: compare_playqueues finished for music playqueue...`
            * `PlaystateMgr: No app.PLAYSTATE.item set. Active players... Attempting recovery...` (note occurrences).
            * `PlaystateMgr: Successfully recovered and set app.PLAYSTATE.item...` or `PlaystateMgr: Failed to recover app.PLAYSTATE.item...` (note outcome if recovery was attempted).
            * Key logs from `init_plex_playqueue` like `init_plex_playqueue: Entered...`, `init_plex_playqueue: Adding item with plex_id...`.
            * Key logs from `compare_playqueues` like `compare_playqueues: Entered...`, `compare_playqueues: Playqueue ... detected new Kodi element...`, `compare_playqueues: Playqueue ... adding item with inferred plex_id...`, `compare_playqueues: Playqueue ... detected deletion...`.
      - **Expected Results:**
         1. Logs confirm that `app.PLAYSTATE.item` is being set consistently and correctly for each music track.
         2. If the `plex_id` fallback mechanism is triggered, logs should indicate it was successful.
         3. Critical error logs indicating a failure to obtain `plex_id` for valid music tracks should be absent.
         4. Playlist synchronization logs from `PlaystateMgr` and `compare_playqueues` should show logical activity corresponding to playlist changes or playback progression.
         5. The recovery logic in `PlaystateMgr` (`Attempting recovery...`) should ideally not be triggered frequently during normal music playlist playback. If it is, the logs should indicate whether the recovery was successful in setting `app.PLAYSTATE.item`.
         6. Logs from `try_identify_and_set_plex_item` should show the flow of item identification, including the `initialize` flag status and results from `PL.init_plex_playqueue` if called.
      - **Pass/Fail Criteria:** Pass if the Kodi debug logs align with the expected behavior of the fixes and refactoring, and do not show persistent failures or errors that the implemented changes were intended to resolve.

   **3.3. Movie Playback Reporting (Regression Test)**
      - **Test Case ID:** TP-MOVIE-003
      - **Description:** Verify that standard movie playback is still correctly reported to Plex after the recent changes.
      - **Steps:**
         1. Ensure Kodi debug logging is enabled.
         2. Navigate to your movie library within Kodi (via PKC).
         3. Select and start playing any movie.
         4. Monitor the Plex server's "Now Playing" dashboard.
         5. After a few minutes of playback, check the Kodi debug logs for messages related to `try_identify_and_set_plex_item` (specifically the `Assigning to app.PLAYSTATE.item...` log) for the movie.
      - **Expected Results:**
         1. The movie that is playing in Kodi should appear as "Now Playing" on the Plex server dashboard.
         2. Playback progress for the movie should update correctly on the Plex server.
         3. The Kodi logs should show `try_identify_and_set_plex_item: Assigning to app.PLAYSTATE.item...` with the correct `plex_id` and `file` for the playing movie.
         4. No new errors related to movie playback reporting should be observed.
      - **Pass/Fail Criteria:** Pass if movie playback is reported to Plex correctly, including "Now Playing" status and progress, and no new issues are introduced.

   **3.4. TV Show Playback Reporting (Regression Test)**
      - **Test Case ID:** TP-TVSHOW-004
      - **Description:** Verify that standard TV show episode playback is still correctly reported to Plex.
      - **Steps:**
         1. Ensure Kodi debug logging is enabled.
         2. Navigate to a TV show series and then to an episode within Kodi (via PKC).
         3. Start playing the selected TV show episode.
         4. Monitor the Plex server's "Now Playing" dashboard.
         5. After a few minutes of playback, check the Kodi debug logs for messages related to `try_identify_and_set_plex_item` for the episode.
      - **Expected Results:**
         1. The TV show episode playing in Kodi should appear as "Now Playing" on the Plex server dashboard.
         2. Playback progress for the episode should update correctly on the Plex server.
         3. The Kodi logs should show `try_identify_and_set_plex_item: Assigning to app.PLAYSTATE.item...` with the correct `plex_id` and `file` for the playing episode.
         4. No new errors related to TV show playback reporting should be observed.
      - **Pass/Fail Criteria:** Pass if TV show episode playback is reported to Plex correctly, including "Now Playing" status and progress, and no new issues are introduced.

**4. Notes/Observations:**
   - (This section is for the tester to fill in during test execution)
   -
   -
   -
