#!/usr/bin/env python3
"""One-account Jellyfin-to-native-PDS bridge. Run behind a trusted LAN boundary."""
import argparse
import hmac
import json
import math
import secrets
import signal
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from encode import encode
from jellyfin import ApiError, Jellyfin, brief
from settings import settings


def number(value, minimum=0, maximum=7*24*3600):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ApiError(400, 'Expected a finite playback position')
    if not minimum <= value <= maximum:
        raise ApiError(400, 'Playback position is outside the video')
    return value


@dataclass
class Job:
    media: dict
    start: float
    id: str = field(default_factory=lambda: secrets.token_hex(16))
    source_key: str = field(default_factory=lambda: secrets.token_hex(24))
    cancel: threading.Event = field(default_factory=threading.Event)
    report_lock: threading.Lock = field(default_factory=threading.Lock)
    created: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    state: str = 'waiting'
    position: float = 0
    bytes_sent: int = 0
    error: str = ''
    reporting_error: str = ''
    reported_start: bool = False
    reported_stop: bool = False

    def public(self):
        return {'id': self.id, 'path': '/api/stream/'+self.id+'.pds', 'fps': 15,
            'start': self.start, 'duration': self.media['duration'],
            'state': self.state, 'bytes': self.bytes_sent, 'error': self.error,
            'progressWarning': self.reporting_error}


class Bridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, jf, token):
        if len(token) < 24 or any(c.isspace() for c in token):
            raise ValueError('Generate BRIDGE_TOKEN with tools/configure.py first')
        self.jf, self.token = jf, token
        self.jobs, self.lock = {}, threading.RLock()
        self.encoder_slot = threading.Lock()
        super().__init__(address, Handler)

    def job(self, key):
        with self.lock:
            job = self.jobs.get(key)
        if job is None:
            raise ApiError(404, 'Playback session has expired')
        return job

    def create_job(self, item_id, start):
        # Only reserve a new session after resolving the actual user-visible item.
        media = self.jf.playback(item_id)
        start = number(start, maximum=max(0, media['duration'] - .1))
        with self.lock:
            now = time.monotonic()
            for job in list(self.jobs.values()):
                if job.state == 'waiting' and now-job.created > 60:
                    job.state = 'expired'
                    job.cancel.set()
                if job.state in ('waiting', 'streaming') and not job.cancel.is_set():
                    raise ApiError(409, 'Another video is playing; stop it before starting a new one')
                if now-job.created > 24*3600 or (len(self.jobs) >= 16 and job.state != 'streaming'):
                    self.jobs.pop(job.id, None)
            job = Job(media, start, position=start)
            self.jobs[job.id] = job
            return job

    def report(self, job, kind, position, paused=False):
        number(position, maximum=job.media['duration'])
        with job.report_lock:
            if job.reported_stop:
                return
            job.last_seen = time.monotonic()
            job.position = position
            try:
                if not job.reported_start:
                    self.jf.report(job, 'start', position, paused)
                    job.reported_start = True
                self.jf.report(job, kind, position, paused)
                job.reporting_error = ''
                if kind == 'stop':
                    job.reported_stop = True
            except ApiError:
                job.reporting_error = 'Jellyfin could not save playback progress'

    def cancel_all(self):
        with self.lock:
            for job in self.jobs.values():
                job.cancel.set()

    def service_actions(self):
        # Recover abandoned sessions after an app/device disappears without a
        # stop request. Only the last client-confirmed position is reported.
        expired = []
        with self.lock:
            now = time.monotonic()
            for job in self.jobs.values():
                if job.state == 'waiting' and now-job.created > 60:
                    job.state = 'expired'
                    job.cancel.set()
                elif job.reported_start and not job.reported_stop and job.state != 'expired' and now-job.last_seen > 45:
                    job.state = 'expired'
                    job.cancel.set()
                    expired.append(job)
        for job in expired:
            threading.Thread(target=self.report, args=(job, 'stop', job.position), daemon=True).start()


class StreamOutput:
    def __init__(self, output, job):
        self.output, self.job = output, job

    def write(self, data):
        if self.job.cancel.is_set():
            raise ConnectionAbortedError('playback cancelled')
        self.output.write(data)
        self.job.bytes_sent += len(data)

    def flush(self):
        self.output.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = 'JellyfinPDS/0.2'

    def log_message(self, format, *args):
        # Request paths may contain private media IDs. Never log headers, URLs,
        # passwords, source access keys, or upstream exception bodies.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def json(self, status, value):
        body = json.dumps(value, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                raise ValueError()
            raw = self.rfile.read(length)
            value = json.loads(raw)
            if len(raw) != length or not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise ApiError(400, 'Expected a JSON object of at most 4096 bytes') from None

    def authenticated(self):
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer '+self.server.token).encode()):
            raise ApiError(401, 'Bridge access key is missing or incorrect; rebuild with your private config')

    def do_GET(self):
        self.handle_api(False)

    def do_POST(self):
        self.handle_api(True)

    def handle_api(self, post):
        self.streaming_headers = False
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == '/health' and not post:
                self.json(200, {'ok': True, 'service': 'jellyfin-pds', 'version': '0.2.0'})
                return
            if path.startswith('/internal/source/') and not post:
                self.proxy_source(path.removeprefix('/internal/source/'))
                return
            self.authenticated()
            if not post and path == '/api/status':
                self.json(200, self.server.jf.status())
            elif not post and path == '/api/libraries':
                self.json(200, self.server.jf.libraries())
            elif not post and path == '/api/items':
                try:
                    start = int(query.get('start', ['0'])[0])
                    if not 0 <= start <= 1_000_000:
                        raise ValueError()
                except ValueError:
                    raise ApiError(400, 'Invalid page offset') from None
                self.json(200, self.server.jf.items(query.get('parent', [None])[0], start,
                    query.get('search', [''])[0], query.get('view', [''])[0] == 'resume'))
            elif not post and path.startswith('/api/items/'):
                self.json(200, brief(self.server.jf.item(path.removeprefix('/api/items/'))))
            elif post and path == '/api/play':
                data = self.body()
                self.json(201, self.server.create_job(data.get('id'), data.get('position', 0)).public())
            elif not post and path.startswith('/api/stream/') and path.endswith('.pds'):
                self.stream(self.server.job(path.removeprefix('/api/stream/')[:-4]))
            elif path.startswith('/api/session/'):
                parts = path.removeprefix('/api/session/').split('/')
                job = self.server.job(parts[0])
                if not post and len(parts) == 1:
                    self.json(200, job.public())
                elif post and len(parts) == 2 and parts[1] in ('progress', 'stop'):
                    data = self.body()
                    position = number(data.get('position', job.position), maximum=job.media['duration'])
                    paused = data.get('paused', False)
                    if not isinstance(paused, bool):
                        raise ApiError(400, 'paused must be a boolean')
                    if parts[1] == 'stop':
                        job.cancel.set()
                        job.state = 'stopped'
                    self.server.report(job, parts[1], position, paused)
                    self.json(200, job.public())
                else:
                    raise ApiError(404, 'Unknown playback action')
            else:
                raise ApiError(404, 'Unknown bridge endpoint')
        except ApiError as error:
            if not self.streaming_headers:
                self.json(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionError, TimeoutError):
            pass
        except Exception:
            # Do not leak upstream authorization headers or private movie paths.
            if not self.streaming_headers:
                self.json(500, {'error': 'The bridge could not complete this request'})
        finally:
            self.close_connection = True

    def proxy_source(self, key):
        if self.client_address[0] not in ('127.0.0.1', '::1'):
            raise ApiError(403, 'Source transport is internal to the bridge')
        with self.server.lock:
            job = next((job for job in self.server.jobs.values()
                        if hmac.compare_digest(job.source_key, key)), None)
        if job is None or job.cancel.is_set() or job.state != 'streaming':
            raise ApiError(404, 'Source session has expired')
        with self.server.jf.source(job.media['item']['id'], job.media['source_id'], self.headers.get('Range')) as response:
            self.send_response(response.status)
            for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                if response.headers.get(name):
                    self.send_header(name, response.headers[name])
            self.send_header('Connection', 'close')
            self.end_headers()
            self.streaming_headers = True
            while not job.cancel.is_set():
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def stream(self, job):
        if job.cancel.is_set() or job.state != 'waiting':
            raise ApiError(409, 'Start a new playback session to resume this video')
        if not self.server.encoder_slot.acquire(timeout=5):
            raise ApiError(409, 'The previous conversion is still stopping; try again shortly')
        try:
            with self.server.lock:
                if job.state != 'waiting' or job.cancel.is_set():
                    raise ApiError(409, 'This playback session was already used')
                job.state = 'streaming'
            self.connection.settimeout(30)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.streaming_headers = True
            source = f'http://127.0.0.1:{self.server.server_port}/internal/source/{job.source_key}'
            encode(source, StreamOutput(self.wfile, job), realtime=True, start=job.start,
                duration=job.media['duration']-job.start, audio_index=job.media['audio_index'],
                has_audio=job.media['has_audio'], cancel=job.cancel)
            if not job.cancel.is_set():
                job.state = 'complete'
        except ApiError:
            raise
        except (BrokenPipeError, ConnectionError, TimeoutError):
            job.state = 'stopped' if job.cancel.is_set() else 'disconnected'
            job.cancel.set()
        except (OSError, ValueError, RuntimeError):
            if job.cancel.is_set():
                job.state = 'stopped'
            else:
                job.state = 'error'
                job.error = 'Conversion failed; check that Jellyfin can directly stream this video'
        finally:
            self.server.encoder_slot.release()
            print(json.dumps({'event': 'conversion_finished', 'state': job.state,
                              'bytes': job.bytes_sent}), flush=True)
            if job.state in ('disconnected', 'error') and job.reported_start:
                self.server.report(job, 'stop', job.position)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    config = settings(args.env_file)
    server = Bridge((args.host, args.port), Jellyfin(config), config.get('BRIDGE_TOKEN', ''))
    def shutdown(signum, frame):
        server.cancel_all()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(json.dumps({'event': 'listening', 'host': args.host, 'port': server.server_port}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.cancel_all()
        server.server_close()


if __name__ == '__main__':
    main()
