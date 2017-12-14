"""
Plex Companion listener
"""
from logging import getLogger
from re import sub
from SocketServer import ThreadingMixIn
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
from urlparse import urlparse, parse_qs

from xbmc import sleep
from companion import process_command
import json_rpc as js
from clientinfo import getXArgsDeviceInfo
import variables as v

###############################################################################

LOG = getLogger("PLEX." + __name__)

###############################################################################


class MyHandler(BaseHTTPRequestHandler):
    """
    BaseHTTPRequestHandler implementation of Plex Companion listener
    """
    protocol_version = 'HTTP/1.1'

    def __init__(self, *args, **kwargs):
        BaseHTTPRequestHandler.__init__(self, *args, **kwargs)
        self.serverlist = []

    def do_HEAD(self):
        LOG.debug("Serving HEAD request...")
        self.answer_request(0)

    def do_GET(self):
        LOG.debug("Serving GET request...")
        self.answer_request(1)

    def do_OPTIONS(self):
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
        self.wfile.close()

    def sendOK(self):
        self.send_response(200)

    def response(self, body, headers=None, code=200):
        headers = {} if headers is None else headers
        try:
            self.send_response(code)
            for key in headers:
                self.send_header(key, headers[key])
            self.send_header('Content-Length', len(body))
            self.send_header('Connection', "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.close()
        except:
            pass

    def answer_request(self, send_data):
        self.serverlist = self.server.client.getServerList()
        sub_mgr = self.server.subscription_manager

        try:
            request_path = self.path[1:]
            request_path = sub(r"\?.*", "", request_path)
            url = urlparse(self.path)
            paramarrays = parse_qs(url.query)
            params = {}
            for key in paramarrays:
                params[key] = paramarrays[key][0]
            LOG.debug("remote request_path: %s", request_path)
            LOG.debug("params received from remote: %s", params)
            sub_mgr.update_command_id(self.headers.get(
                'X-Plex-Client-Identifier',
                self.client_address[0]),
                params.get('commandID', False))
            if request_path == "version":
                self.response(
                    "PlexKodiConnect Plex Companion: Running\nVersion: %s"
                    % v.ADDON_VERSION)
            elif request_path == "verify":
                self.response("XBMC JSON connection test:\n" +
                              js.ping())
            elif request_path == 'resources':
                resp = ('%s'
                        '<MediaContainer>'
                        '<Player'
                        ' title="%s"'
                        ' protocol="plex"'
                        ' protocolVersion="1"'
                        ' protocolCapabilities="timeline,playback,navigation,playqueues"'
                        ' machineIdentifier="%s"'
                        ' product="PlexKodiConnect"'
                        ' platform="%s"'
                        ' platformVersion="%s"'
                        ' deviceClass="pc"'
                        '/>'
                        '</MediaContainer>'
                        % (v.XML_HEADER,
                           v.DEVICENAME,
                           v.PKC_MACHINE_IDENTIFIER,
                           v.PLATFORM,
                           v.ADDON_VERSION))
                LOG.debug("crafted resources response: %s", resp)
                self.response(resp, getXArgsDeviceInfo(include_token=False))
            elif "/poll" in request_path:
                if params.get('wait', False) == '1':
                    sleep(950)
                command_id = params.get('commandID', 0)
                self.response(
                    sub(r"INSERTCOMMANDID",
                        str(command_id),
                        sub_mgr.msg(js.get_players())),
                    {
                        'X-Plex-Client-Identifier': v.PKC_MACHINE_IDENTIFIER,
                        'X-Plex-Protocol': '1.0',
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Max-Age': '1209600',
                        'Access-Control-Expose-Headers':
                            'X-Plex-Client-Identifier',
                        'Content-Type': 'text/xml;charset=utf-8'
                    })
            elif "/subscribe" in request_path:
                self.response(v.COMPANION_OK_MESSAGE,
                              getXArgsDeviceInfo(include_token=False))
                protocol = params.get('protocol', False)
                host = self.client_address[0]
                port = params.get('port', False)
                uuid = self.headers.get('X-Plex-Client-Identifier', "")
                command_id = params.get('commandID', 0)
                sub_mgr.add_subscriber(protocol,
                                       host,
                                       port,
                                       uuid,
                                       command_id)
            elif "/unsubscribe" in request_path:
                self.response(v.COMPANION_OK_MESSAGE,
                              getXArgsDeviceInfo(include_token=False))
                uuid = self.headers.get('X-Plex-Client-Identifier', False) \
                    or self.client_address[0]
                sub_mgr.remove_subscriber(uuid)
            else:
                # Throw it to companion.py
                process_command(request_path, params, self.server.queue)
                self.response('', getXArgsDeviceInfo(include_token=False))
                sub_mgr.notify()
        except:
            LOG.error('Error encountered. Traceback:')
            import traceback
            LOG.error(traceback.print_exc())


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """
    Using ThreadingMixIn Thread magic
    """
    daemon_threads = True

    def __init__(self, client, subscription_manager, queue, *args, **kwargs):
        """
        client: Class handle to plexgdm.plexgdm. We can thus ask for an up-to-
        date serverlist without instantiating anything

        same for SubscriptionMgr
        """
        self.client = client
        self.subscription_manager = subscription_manager
        self.queue = queue
        HTTPServer.__init__(self, *args, **kwargs)
