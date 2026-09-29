#!/usr/bin/env python3
"""Opt-in integration tests against the isolated Jellyfin 10.11.11 container.

Never reads the production .env. Setup writes build/jellyfin-test/connection.json.
"""
import io
import json
import pathlib
import struct
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from pds import decode_video, packets


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / 'build/jellyfin-test/connection.json').read_text())
        assert cls.config['url'] == 'http://127.0.0.1:8097', 'Integration tests require the isolated localhost server'
        cls.token = cls.config['token']
        cls.user_id = cls.config['user_id']
        cls.items = cls.api('/Playdate/api/items?search=Diagnostic')['items']
        cls.item = next(item for item in cls.items if item['name'] == 'Diagnostic')
        cls.library = cls.api('/Playdate/api/libraries')['items'][0]
        cls.measurements = {}

    @classmethod
    def request(cls, path, data=None, *, token=None, method=None):
        authorization = cls.config['auth']
        if token is None:
            token = cls.token
        if token:
            authorization += ', Token="' + token + '"'
        return urllib.request.urlopen(urllib.request.Request(cls.config['url'] + path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={'Content-Type': 'application/json', 'X-Emby-Authorization': authorization}), timeout=30)

    @classmethod
    def api(cls, path, data=None, **kwargs):
        with cls.request(path, data, **kwargs) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def assertStatus(self, expected, path, data=None, **kwargs):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.api(path, data, **kwargs)
        self.assertEqual(expected, caught.exception.code)
        caught.exception.close()

    def setUp(self):
        self.jobs = []
        self.users = []

    def tearDown(self):
        for job in self.jobs:
            self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': job['start']})
        for user in self.users:
            self.api('/Users/' + user['Id'], method='DELETE')

    def play(self, position=0, item=None):
        job = self.api('/Playdate/api/play', {'id': (item or self.item)['id'], 'position': position})
        self.jobs.append(job)
        return job

    def user(self, **policy):
        name = 'pds-test-' + uuid.uuid4().hex[:10]
        user = self.api('/Users/New', {'Name': name, 'Password': 'local-test-password'})
        self.users.append(user)
        user['Policy'].update(policy)
        self.api('/Users/' + user['Id'] + '/Policy', user['Policy'])
        auth = self.api('/Users/AuthenticateByName', {'Username': name, 'Pw': 'local-test-password'}, token='')
        return auth['AccessToken']

    def decode(self, raw):
        previous = None
        video = audio = 0
        first = None
        for kind, payload in packets(io.BytesIO(raw)):
            if kind == 'audio':
                audio += 1
            else:
                if first is None:
                    first = kind
                previous = decode_video(kind, payload, previous)
                video += 1
        self.assertEqual(0xC1, first)
        self.assertGreater(audio, 0)
        return video, audio

    def test_authentication_and_metadata(self):
        self.assertStatus(401, '/Playdate/api/status', token='')
        self.assertStatus(401, '/Playdate/api/status', token='invalid-token')
        self.assertEqual('plugin', self.api('/Playdate/api/status')['backend'])
        listing = self.api('/Playdate/api/items?parent=' + self.library['id'])
        self.assertTrue(any(item['id'] == self.item['id'] for item in listing['items']))
        self.assertEqual([], self.api('/Playdate/api/items?start=99999')['items'])
        self.assertStatus(400, '/Playdate/api/items?start=-1')
        self.assertStatus(400, '/Playdate/api/play', {'id': self.item['id'], 'position': -1})
        self.assertStatus(400, '/Playdate/api/play', {'id': self.item['id'], 'position': 99999})
        self.assertStatus(400, '/Playdate/api/play', {'id': 'http://example.com/file'})

    def test_pdi_poster_matches_sdk_fixture(self):
        self.assertTrue(self.item['poster'])
        path = '/Playdate/api/items/' + self.item['id'] + '/poster.pdi'
        with self.request(path) as response:
            raw = response.read()
        self.assertEqual(b'Playdate IMG', raw[:12])
        self.assertEqual((0, 96, 144, 12, 0, 0, 0, 0, 4), struct.unpack('<I8H', raw[12:32]))
        self.assertEqual(1760, len(raw))
        reference = ROOT / 'build/pdi-format.pdx/pattern96.pdi'
        if reference.exists():
            self.assertEqual(reference.read_bytes(), raw)
        with self.request(path) as response:
            self.assertEqual(raw, response.read())
        (ROOT / 'build/plugin-poster.pdi').write_bytes(raw)

    def test_progressive_stream_seek_and_progress(self):
        job = self.play(8)
        started = time.monotonic()
        with self.request('/Playdate' + job['path']) as response:
            first = response.read(4)
            latency = time.monotonic() - started
            raw = first + response.read()
        self.assertLess(latency, 3, 'The plugin buffered the whole conversion')
        frames, audio = self.decode(raw)
        self.assertEqual(60, frames)
        self.assertEqual('complete', self.api('/Playdate/api/session/' + job['id'])['state'])
        self.assertStatus(409, '/Playdate' + job['path'])
        self.api('/Playdate/api/session/' + job['id'] + '/progress', {'position': 9})
        stopped = self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': 9.5})
        self.assertEqual('', stopped['progressWarning'])
        self.assertEqual('stopped', stopped['state'])
        self.assertEqual(stopped, self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': 9.5}))
        self.assertAlmostEqual(9.5, self.api('/Playdate/api/items/' + self.item['id'])['resume'])
        self.assertTrue(any(item['id'] == self.item['id'] for item in self.api('/Playdate/api/items?view=resume')['items']))
        self.measurements.update(first_packet_seconds=round(latency, 3), seek_video_frames=frames,
            audio_frames=audio, stream_bytes=len(raw), saved_position=9.5)
        (ROOT / 'build/plugin-stream.pds').write_bytes(raw)

    def test_cancel_releases_encoder_and_session_ownership(self):
        job = self.play()
        other = self.user()
        for path in ('/Playdate/api/session/' + job['id'], '/Playdate' + job['path']):
            self.assertStatus(404, path, token=other)
        self.assertStatus(404, '/Playdate/api/session/' + job['id'] + '/stop', {'position': 0}, token=other)
        self.assertStatus(409, '/Playdate/api/play', {'id': self.item['id']})
        response = self.request('/Playdate' + job['path'])
        response.read(4)
        self.api('/Playdate/api/session/' + job['id'] + '/progress', {'position': .4})
        self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': .8})
        response.close()
        next_job = self.play(11)
        with self.request('/Playdate' + next_job['path']) as response:
            self.assertEqual(15, self.decode(response.read())[0])
        self.assertStatus(400, '/Playdate/api/session/' + next_job['id'] + '/progress', {'position': -1})

    def test_library_and_transcode_permissions(self):
        hidden = self.user(EnableAllFolders=False, EnabledFolders=[])
        self.assertEqual([], self.api('/Playdate/api/libraries', token=hidden)['items'])
        self.assertEqual([], self.api('/Playdate/api/items?search=Diagnostic', token=hidden)['items'])
        for suffix in ('', '/poster.pdi'):
            self.assertStatus(404, '/Playdate/api/items/' + self.item['id'] + suffix, token=hidden)
        self.assertStatus(404, '/Playdate/api/play', {'id': self.item['id']}, token=hidden)
        no_transcode = self.user(EnableVideoPlaybackTranscoding=False)
        self.assertStatus(403, '/Playdate/api/play', {'id': self.item['id']}, token=no_transcode)

    def test_pagination_and_silent_video(self):
        first = self.api('/Playdate/api/items?search=Page')
        second = self.api('/Playdate/api/items?search=Page&start=20')
        self.assertEqual(21, first['total'])
        self.assertEqual(20, len(first['items']))
        self.assertEqual(1, len(second['items']))
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        silent = self.api('/Playdate/api/items?search=Silent')['items'][0]
        # Jellyfin may extract a thumbnail or generate parent library artwork
        # asynchronously. The advertised poster must agree with the endpoint.
        poster_path = '/Playdate/api/items/' + silent['id'] + '/poster.pdi'
        if silent['poster']:
            with self.request(poster_path) as response:
                self.assertEqual(1760, len(response.read()))
        else:
            self.assertStatus(404, poster_path)
        job = self.play(item=silent)
        with self.request('/Playdate' + job['path']) as response:
            self.assertEqual(45, self.decode(response.read())[0])

    def test_segments_preserve_stream_and_have_exact_lengths(self):
        job = self.play(8)
        parts = []
        started = time.monotonic()
        for index in range(50):
            with self.request('/Playdate' + job['parts'] + '/' + str(index)) as response:
                self.assertEqual(200, response.status)
                self.assertIsNone(response.headers.get('Transfer-Encoding'))
                raw = response.read()
                self.assertEqual(len(raw), int(response.headers['Content-Length']))
                self.assertLessEqual(len(raw), 65536)
                final = response.headers['X-Pds-Final'] == '1'
                parts.append(raw)
            if index == 0:
                self.measurements['first_segment_seconds'] = round(time.monotonic() - started, 3)
                self.assertLess(self.measurements['first_segment_seconds'], 3.5)
                self.assertStatus(409, '/Playdate' + job['parts'] + '/0')
                self.assertStatus(409, '/Playdate' + job['parts'] + '/999')
            if final:
                break
        else:
            self.fail('Segment stream did not finish')
        raw = b''.join(parts)
        self.assertEqual(60, self.decode(raw)[0])
        with self.request('/Playdate' + job['parts'] + '/' + str(len(parts))) as response:
            self.assertEqual(204, response.status)
        self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': 8})
        direct = self.play(8)
        with self.request('/Playdate' + direct['path']) as response:
            self.assertEqual(raw, response.read(), 'Segmentation changed native PDS bytes')
        self.measurements.update(segments=len(parts), segmented_bytes=len(raw))

    def test_segment_cancellation_releases_encoder(self):
        job = self.play()
        with self.request('/Playdate' + job['parts'] + '/0') as response:
            self.assertGreater(len(response.read()), 0)
        self.api('/Playdate/api/session/' + job['id'] + '/stop', {'position': 0})
        self.assertStatus(409, '/Playdate' + job['parts'] + '/1')
        replacement = self.play(11)
        data = bytearray()
        for index in range(10):
            with self.request('/Playdate' + replacement['parts'] + '/' + str(index)) as response:
                data.extend(response.read())
                if response.headers['X-Pds-Final'] == '1': break
        self.assertEqual(15, self.decode(data)[0])

    def test_unconsumed_session_expires_without_watch_progress(self):
        before = self.api('/Playdate/api/items/' + self.item['id'])['resume']
        job = self.play()
        deadline = time.monotonic() + 70
        while self.api('/Playdate/api/session/' + job['id'])['state'] == 'waiting':
            self.assertLess(time.monotonic(), deadline, 'Unconsumed session was not expired')
            time.sleep(1)
        self.assertEqual('expired', self.api('/Playdate/api/session/' + job['id'])['state'])
        self.assertStatus(409, '/Playdate' + job['path'])
        self.assertEqual(before, self.api('/Playdate/api/items/' + self.item['id'])['resume'])

    @classmethod
    def tearDownClass(cls):
        (ROOT / 'build/plugin-integration-result.json').write_text(json.dumps(cls.measurements, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
