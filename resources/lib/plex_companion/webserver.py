#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Plex Companion listener
"""
from logging import getLogger
from re import sub
from socketserver import ThreadingMixIn
from http.server import HTTPServer, BaseHTTPRequestHandler
import xml.etree.ElementTree as etree

from . import common
from .processing import process_command

from .. import utils, variables as v
from .. import json_rpc as js
from .. import app

log = getLogger('PLEX.companion.webserver')


def CompanionHandlerClassFactory(playstate_mgr):
    """
    This class factory makes playstate_mgr available for CompanionHandler
    """

    class CompanionHandler(BaseHTTPRequestHandler):
        """
        BaseHTTPRequestHandler implementation of Plex Companion listener
        """
        protocol_version = 'HTTP/1.1'

        def __init__(self, *args, **kwargs):
            self.ok_msg = common.b_ok_message()
            self.sending_headers = common.proxy_headers()
            super().__init__(*args, **kwargs)

        def log_message(self, *args, **kwargs):
            """Mute all requests, don't log them."""
            pass

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except ConnectionError as error:
                # Catches e.g. ConnectionResetError: [WinError 10054]
                log.debug('Silencing error: %s: %s', type(error), error)
                self.close_connection = True
            except Exception as error:
                # Catch anything in order to not let our web server crash
                log.error('Webserver ignored the following exception: %s, %s',
                          type(error), error)
                self.close_connection = True

        def do_HEAD(self):
            log.debug("Serving HEAD request...")
            self.answer_request()

        def do_GET(self):
            log.debug("Serving GET request...")
            self.answer_request()

        def do_OPTIONS(self):
            log.debug("Serving OPTIONS request...")
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.send_header('X-Plex-Client-Identifier', v.PKC_MACHINE_IDENTIFIER)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Max-Age', '1209600')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods',
                             'POST, GET, OPTIONS, DELETE, PUT, HEAD')
            self.send_header(
                'Access-Control-Allow-Headers',
                'x-plex-version, x-plex-platform-version, x-plex-username, '
                'x-plex-client-identifier, x-plex-target-client-identifier, '
                'x-plex-device-name, x-plex-platform, x-plex-product, accept, '
                'x-plex-device, x-plex-device-screen-resolution')
            self.end_headers()

        def response(self, body, code=200):
            self.send_response(code)
            for key, value in self.sending_headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', len(body) if body else 0)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def ok_message(self):
            self.response(self.ok_msg, code=200)

        def nok_message(self, error_message, code):
            log.warn('Sending Not OK message: %s', error_message)
            self.response(f'Failure: {error_message}'.encode('utf8'),
                          code=code)

        def poll(self, params):
            """
            Case for Plex Web contacting us via Plex Companion
            Let's NOT register this Companion client - it will poll us
            continuously
            """
            if not playstate_mgr.update_command_id(self.uuid, self.command_id):
                log.warn("Poll: Client not registered – auto-registering")
                if not self.check_subscription(params):
                    self.nok_message(
                        f'Failed to auto-register polling client with {params}',
                        code=400,
                    )
                    return

            if params.get('wait') == '1':
                # Plex Web asks us to wait until we start playback
                i = 20
                while not app.APP.is_playing and i > 0:
                    if app.APP.monitor.waitForAbort(1):
                        return
                    i -= 1
            message = common.timeline(js.get_players())
            self.response(etree.tostring(message, encoding='utf8'),
                          code=200)

        def send_resources_xml(self):
            xml = etree.Element('MediaContainer', attrib={'size': '1'})
            etree.SubElement(xml, 'Player', attrib=common.player())
            self.response(etree.tostring(xml, encoding='utf8'), code=200)

        def check_subscription(self, params):
            if self.uuid in playstate_mgr.subscribers:
                return True
            protocol = params.get('protocol')
            port = params.get('port')
            if protocol is None or port is None:
                log.error('Received invalid params for subscription: %s',
                          params)
                return False
            url = f"{protocol}://{self.client_address[0]}:{port}"
            playstate_mgr.subscribe(self.uuid, self.command_id, url)
            return True

        def answer_request(self):
            request_path = self.path[1:]
            request_path = sub(r"\?.*", "", request_path)
            parseresult = utils.urlparse(self.path)
            paramarrays = utils.parse_qs(parseresult.query)
            params = {}
            for key in paramarrays:
                params[key] = paramarrays[key][0]
            log.debug('remote request_path: %s, received from %s. headers: %s',
                      request_path, self.client_address, self.headers.items())
            log.debug('params received from remote: %s', params)

            conntype = self.headers.get('Connection', '')
            if conntype.lower() == 'keep-alive':
                self.sending_headers['Connection'] = 'Keep-Alive'
                self.sending_headers['Keep-Alive'] = 'timeout=20'
            else:
                self.sending_headers['Connection'] = 'Close'
            self.command_id = int(params.get('commandID', 0))
            uuid = self.headers.get('X-Plex-Client-Identifier')
            if uuid is None:
                log.error('No X-Plex-Client-Identifier received')
                self.nok_message('No X-Plex-Client-Identifier received',
                                 code=400)
                return
            self.uuid = common.UUIDStr(uuid)

            # Here we DO NOT track subscribers
            if request_path == 'player/timeline/poll':
                # This seems to be only done by Plex Web, polling us
                # continuously
                self.poll(params)
                return
            elif request_path == 'resources':
                self.send_resources_xml()
                return

            # Here we TRACK subscribers
            if request_path == 'player/timeline/subscribe':
                if self.check_subscription(params):
                    self.ok_message()
                else:
                    self.nok_message(f'Received invalid parameters: {params}',
                                     code=400)
                return
            elif request_path == 'player/timeline/unsubscribe':
                playstate_mgr.unsubscribe(self.uuid)
                self.ok_message()
            else:
                if not playstate_mgr.update_command_id(self.uuid,
                                                       self.command_id):
                    self.nok_message(f'Plex Companion Client not yet registered',
                                     code=500)
                    return
                if process_command(cmd=None, path=request_path, params=params):
                    self.ok_message()
                else:
                    self.nok_message(f'Unknown request path: {request_path}',
                                     code=500)

    return CompanionHandler


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """
    Using ThreadingMixIn Thread magic
    """
    daemon_threads = True
