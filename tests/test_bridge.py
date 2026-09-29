import contextlib
import io
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bridge import Bridge
from jellyfin import Jellyfin, ApiError
from inspect_pds import inspect

ITEM = '1'*32
LIBRARY = '2'*32
USER = '3'*32
TOKEN = 'a-test-bridge-token-with-enough-entropy-for-tests'


class FakeJellyfin(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        if getattr(self.server, 'reject_next_token', False):
            self.server.reject_next_token = False
            return False
        return 'Token="user-token"' in self.headers.get('X-Emby-Authorization', '')

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/Users/AuthenticateByName':
            valid = data == {'Username': 'test', 'Pw': 'spaces $ # = are literal'}
            self.server.auth_requests.append(dict(self.headers))
            self.reply({'AccessToken': 'user-token', 'User': {'Id': USER}} if valid else {}, 200 if valid else 401)
        elif not self.authorized():
            self.reply({}, 401)
        elif self.path == f'/Items/{ITEM}/PlaybackInfo':
            self.reply({'PlaySessionId': 'play-session', 'MediaSources': [{
                'Id': ITEM, 'RunTimeTicks': 30_000_000, 'DefaultAudioStreamIndex': 1,
                'MediaStreams': [{'Type': 'Video', 'Index': 0}, {'Type': 'Audio', 'Index': 1}]}]})
        elif self.path.startswith('/Sessions/Playing'):
            self.server.reports.append((self.path, data))
            self.reply({})
        else:
            self.reply({}, 404)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/System/Info/Public':
            self.reply({'ServerName': 'Test Jellyfin', 'Version': '10.11.11'})
            return
        if not self.authorized():
            self.reply({}, 401)
            return
        if path == '/UserViews':
            self.reply({'Items': [{'Id': LIBRARY, 'Name': 'Movies', 'Type': 'CollectionFolder',
                                  'IsFolder': True, 'CollectionType': 'movies'}]})
        elif path in ('/Items', '/Items/Resume'):
            self.reply({'TotalRecordCount': 1, 'Items': [{
                'Id': ITEM, 'Name': 'Generated diagnostic', 'Type': 'Movie', 'RunTimeTicks': 30_000_000,
                'Overview': 'Original media used for tests', 'UserData': {'PlaybackPositionTicks': 10_000_000}}]})
        elif path == f'/Videos/{ITEM}/stream':
            content = self.server.media
            header = self.headers.get('Range')
            start, end = 0, len(content)-1
            if header:
                left, right = header.removeprefix('bytes=').split('-')
                start, end = int(left or '0'), int(right) if right else end
                self.server.ranges.append(header)
            if start >= len(content):
                self.reply({}, 416)
                return
            content = content[start:end+1]
            self.send_response(206 if header else 200)
            self.send_header('Content-Type', 'video/x-matroska')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Accept-Ranges', 'bytes')
            if header:
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(self.server.media)}')
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(content)
        else:
            self.reply({}, 404)


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for bridge integration')
class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        clip = pathlib.Path(cls.tmp.name)/'fixture.mkv'
        subprocess.run([sys.executable, str(ROOT/'tools/make_fixture.py'), str(clip), '--seconds', '3'],
                       check=True, capture_output=True)
        cls.media = clip.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), FakeJellyfin)
        self.upstream.media = self.media
        self.upstream.reports, self.upstream.ranges, self.upstream.auth_requests = [], [], []
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        jf = Jellyfin({'JELLYFIN_URL': f'http://127.0.0.1:{self.upstream.server_port}',
            'JELLYFIN_USERNAME': 'test', 'JELLYFIN_PASSWORD': 'spaces $ # = are literal'})
        self.bridge = Bridge(('127.0.0.1', 0), jf, TOKEN)
        self.thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.bridge.server_port}'

    def tearDown(self):
        self.bridge.cancel_all()
        self.bridge.shutdown()
        self.bridge.server_close()
        self.thread.join()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def request(self, path, data=None, auth=True):
        request = urllib.request.Request(self.base+path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization': 'Bearer '+TOKEN} if auth else {})
        try:
            return urllib.request.urlopen(request, timeout=12)
        except urllib.error.HTTPError as error:
            error.close()
            raise

    def json(self, path, data=None, auth=True):
        with self.request(path, data, auth) as response:
            return json.load(response)

    def test_authentication_and_library_metadata(self):
        self.assertTrue(self.json('/health', auth=False)['ok'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.json('/api/libraries', auth=False)
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(self.json('/api/status')['version'], '10.11.11')
        self.assertEqual(self.json('/api/libraries')['items'][0]['id'], LIBRARY)
        item = self.json('/api/items?parent='+LIBRARY)['items'][0]
        self.assertEqual(item['resume'], 1)
        self.assertEqual(item['duration'], 3)
        self.assertEqual(len(self.upstream.auth_requests), 1)
        headers = self.upstream.auth_requests[0]
        self.assertEqual(headers['User-Agent'], 'JellyfinPDS/0.2.0')
        self.assertIn('MediaBrowser', headers['X-Emby-Authorization'])
        self.assertNotIn('user-token', json.dumps(item))

    def test_actual_jellyfin_http_source_becomes_progressive_native_stream(self):
        job = self.json('/api/play', {'id': ITEM})
        with self.request(job['path']) as response:
            first = response.read(2)
            self.assertEqual(first, b'\xff\xc1')
            self.assertEqual(self.bridge.job(job['id']).state, 'streaming')
            wire = first + response.read()
        result = inspect(io.BytesIO(wire))
        self.assertEqual(result['video_frames'], 45)
        self.assertAlmostEqual(result['audio_seconds'], 3, delta=.1)
        self.assertEqual(self.json('/api/session/'+job['id'])['state'], 'complete')
        self.assertTrue(self.upstream.ranges, 'FFmpeg never requested source byte ranges')
        # Encoding alone must not claim the movie was watched.
        self.assertEqual(self.upstream.reports, [])

    def test_expired_user_token_is_renewed_once(self):
        self.json('/api/libraries')
        self.upstream.reject_next_token = True
        self.assertEqual(self.json('/api/libraries')['items'][0]['id'], LIBRARY)
        self.assertEqual(len(self.upstream.auth_requests), 2)

    def test_seek_reencodes_from_requested_position_and_reports_actual_progress(self):
        job = self.json('/api/play', {'id': ITEM, 'position': 1})
        with self.request(job['path']) as response:
            result = inspect(io.BytesIO(response.read()))
        self.assertEqual(result['video_frames'], 30)
        base = '/api/session/'+job['id']
        self.json(base+'/progress', {'position': 1.7})
        self.json(base+'/stop', {'position': 2.3})
        self.json(base+'/stop', {'position': 2.3})
        reports = self.upstream.reports
        self.assertEqual([path for path, _ in reports], ['/Sessions/Playing', '/Sessions/Playing/Progress', '/Sessions/Playing/Stopped'])
        self.assertEqual(reports[-1][1]['PositionTicks'], 23_000_000)
        self.assertEqual(reports[-1][1]['ItemId'], ITEM)

    def test_cancel_stops_encoder_and_releases_single_stream_slot(self):
        job = self.json('/api/play', {'id': ITEM})
        with self.request(job['path']) as response:
            self.assertEqual(response.read(2), b'\xff\xc1')
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.json('/api/play', {'id': ITEM})
            self.assertEqual(error.exception.code, 409)
            self.json('/api/session/'+job['id']+'/stop', {'position': .2})
            response.read()
        self.assertTrue(self.bridge.encoder_slot.acquire(timeout=3), 'encoder survived cancellation')
        self.bridge.encoder_slot.release()
        replacement = self.json('/api/play', {'id': ITEM, 'position': .2})
        self.assertNotEqual(replacement['id'], job['id'])

    def test_validation_and_no_arbitrary_source_proxy(self):
        for payload in ({'id': '../../etc/passwd'}, {'id': ITEM, 'position': float('nan')},
                        {'id': ITEM, 'position': -1}, {'id': ITEM, 'position': 4}):
            with self.subTest(payload=payload), self.assertRaises(urllib.error.HTTPError) as error:
                self.json('/api/play', payload)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.json('/internal/source/anything', auth=False)
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(self.upstream.reports, [])

    def test_abandoned_session_expires_without_claiming_playback(self):
        job = self.json('/api/play', {'id': ITEM})
        self.bridge.job(job['id']).created -= 61
        next_job = self.json('/api/play', {'id': ITEM})
        self.assertNotEqual(next_job['id'], job['id'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(job['path'])
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.upstream.reports, [])


if __name__ == '__main__':
    unittest.main()
