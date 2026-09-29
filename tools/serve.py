#!/usr/bin/env python3
"""Loopback diagnostic server: convert one local clip as the client receives it.

This is an experiment, not the Jellyfin bridge. It has no library or auth API.
"""
import argparse
import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from encode import encode


class Delivery:
    def __init__(self, output, options):
        self.output, self.options = output, options
        self.started = time.monotonic()
        self.first_byte = None
        self.bytes_sent = 0
        self.stalled = False

    def write(self, data):
        opt = self.options
        elapsed = time.monotonic() - self.started
        if opt.stall_after is not None and elapsed >= opt.stall_after and not self.stalled:
            self.stalled = True
            time.sleep(opt.stall_for)
            elapsed = time.monotonic() - self.started
        if opt.rate:
            delay = self.bytes_sent / opt.rate - elapsed
            if delay > 0:
                time.sleep(delay)
        if opt.disconnect_after and self.bytes_sent + len(data) >= opt.disconnect_after:
            remaining = max(0, opt.disconnect_after - self.bytes_sent)
            self.output.write(data[:remaining])
            self.output.flush()
            self.bytes_sent += remaining
            raise ConnectionAbortedError('injected mid-packet disconnect')
        self.output.write(data)
        if self.first_byte is None:
            self.first_byte = time.monotonic() - self.started
            print(json.dumps({'event': 'first_byte', 'seconds': self.first_byte}), flush=True)
        self.bytes_sent += len(data)

    def flush(self):
        self.output.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=pathlib.Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--fps', type=int, choices=(5, 10, 15, 30), default=15)
    parser.add_argument('--realtime', action='store_true')
    parser.add_argument('--rate', type=int, default=0, help='limit delivery to bytes per second')
    parser.add_argument('--stall-after', type=float)
    parser.add_argument('--stall-for', type=float, default=2)
    parser.add_argument('--disconnect-after', type=int, default=0, help='cut transport after N bytes')
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error('input must be a local video file')
    if args.rate < 0 or args.stall_for < 0 or args.disconnect_after < 0:
        parser.error('delivery limits must be nonnegative')
    busy = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        # Connection-delimited HTTP/1.0 avoids inventing a final Content-Length
        # for a conversion that is still in progress.
        def do_GET(self):
            if self.path == '/health':
                self.send_response(200)
                self.send_header('Content-Length', '3')
                self.end_headers()
                self.wfile.write(b'OK\n')
                return
            if self.path != '/stream.pds':
                self.send_error(404)
                return
            if not busy.acquire(blocking=False):
                self.send_error(503, 'One diagnostic stream at a time')
                return
            delivery = Delivery(self.wfile, args)
            try:
                self.connection.settimeout(15)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                result = encode(args.input, delivery, args.fps, args.realtime)
                print(json.dumps({'event': 'complete', 'bytes': delivery.bytes_sent,
                    'seconds': time.monotonic() - delivery.started, **result}), flush=True)
            except (OSError, ValueError, RuntimeError) as error:
                # Headers may already be sent: terminate the stream, never append
                # an HTML error page to the native decoder's binary input.
                print(json.dumps({'event': 'stream_error', 'message': str(error),
                    'bytes': delivery.bytes_sent}), flush=True)
            finally:
                self.close_connection = True
                busy.release()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({'event': 'listening', 'host': args.host, 'port': server.server_port}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
