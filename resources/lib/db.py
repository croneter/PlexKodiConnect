#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sqlite3
from functools import wraps
from logging import getLogger

from . import variables as v, app
from .exceptions import LockedDatabase

DB_WRITE_ATTEMPTS = 30
DB_WRITE_ATTEMPTS_TIMEOUT = 0.05  # initial backoff in seconds
DB_WRITE_ATTEMPTS_TIMEOUT_MAX = 1  # cap in seconds
DB_CONNECTION_TIMEOUT = 10

logger = getLogger('PLEX.db')


def catch_operationalerrors(method):
    """
    sqlite.OperationalError is raised immediately if another DB connection
    is open, reading something that we're trying to change

    So let's catch it and try again.

    A type check for sqlite3.OperationalError does NOT work at least for
    OSMC, so use general "catch all"

    Also see https://github.com/mattn/go-sqlite3/issues/274
    """
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        attempts = DB_WRITE_ATTEMPTS
        timeout = DB_WRITE_ATTEMPTS_TIMEOUT
        while True:
            try:
                return method(self, *args, **kwargs)
            except Exception as err:
                if 'database is locked' not in str(err):
                    # Not an error we want to catch, so reraise it
                    raise
                attempts -= 1
                logger.debug('DB locked, retrying in %.2fs (%d left)',
                             timeout, attempts)
                if attempts == 0:
                    # Reraise in order to NOT catch nested OperationalErrors
                    raise LockedDatabase('Database is locked')
                # Release our transaction so VACUUM (or other
                # exclusive operations) can proceed
                _close_transaction(self.kodiconn)
                if self.artconn:
                    _close_transaction(self.artconn)
                if app.APP.monitor.waitForAbort(timeout):
                    return
                timeout = min(timeout * 2, DB_WRITE_ATTEMPTS_TIMEOUT_MAX)
                _begin_transaction(self.kodiconn)
                if self.artconn:
                    _begin_transaction(self.artconn)
    return wrapper


def _close_transaction(conn):
    try:
        conn.commit()
    except Exception:
        pass


def _begin_transaction(conn):
    try:
        conn.execute('BEGIN')
    except Exception:
        pass


def _initial_db_connection_setup(conn):
    """
    Set-up DB e.g. for WAL journal mode, if that hasn't already been done
    before. Also start a transaction
    """
    conn.execute('PRAGMA journal_mode = WAL;')
    conn.execute('PRAGMA cache_size = -8000;')
    conn.execute('PRAGMA synchronous = NORMAL;')
    conn.execute('BEGIN')


def connect(media_type=None):
    """
    Open a connection to the Kodi database.
        media_type: 'video' (standard if not passed), 'plex', 'music', 'texture'
    """
    if media_type == "plex":
        db_path = v.DB_PLEX_PATH
    elif media_type == 'plex-copy':
        db_path = v.DB_PLEX_COPY_PATH
    elif media_type == "music":
        db_path = v.DB_MUSIC_PATH
    elif media_type == "texture":
        db_path = v.DB_TEXTURE_PATH
    else:
        db_path = v.DB_VIDEO_PATH
    conn = sqlite3.connect(db_path,
                           timeout=DB_CONNECTION_TIMEOUT,
                           isolation_level=None)
    attempts = DB_WRITE_ATTEMPTS
    timeout = DB_WRITE_ATTEMPTS_TIMEOUT
    while True:
        try:
            _initial_db_connection_setup(conn)
        except sqlite3.OperationalError as err:
            if err.args[0] and 'database is locked' not in err.args[0]:
                # Not an error we want to catch, so reraise it
                raise
            attempts -= 1
            if attempts == 0:
                # Reraise in order to NOT catch nested OperationalErrors
                raise LockedDatabase('Database is locked')
            if app.APP.monitor.waitForAbort(timeout):
                # PKC needs to quit
                raise LockedDatabase('Database was locked and we need to exit')
            timeout = min(timeout * 2, DB_WRITE_ATTEMPTS_TIMEOUT_MAX)
        else:
            break
    return conn
