#!/usr/bin/env python3
"""Run real plugin playback and downloaded PDI images in the Playdate Simulator."""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulator', default='PlaydateSimulator')
    parser.add_argument('--sdk', default=os.environ.get('PLAYDATE_SDK_PATH'))
    parser.add_argument('--data', required=True, type=pathlib.Path)
    parser.add_argument('--proxy', action='store_true', help='test through the isolated Nginx on port 8098')
    parser.add_argument('--segment-delay', type=float, default=0,
                        help='simulate slow segment setup using a local forwarding server on port 8098')
    parser.add_argument('--isolate-usb', action='store_true', help='hide device nodes using Linux bubblewrap')
    parser.add_argument('--media', default='Diagnostic', help='generated fixture title')
    args = parser.parse_args()
    source = ROOT/'build/plugin-native-source'
    source.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT/'build/jellyfin-test/connection.json').read_text())
    assert config['url'] == 'http://127.0.0.1:8097'
    if args.proxy and args.segment_delay:
        parser.error('choose Nginx or the delayed forwarding server')
    server = None
    if args.segment_delay:
        upstream = config['url']
        class DelayedProxy(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *unused): pass
            def forward(self):
                if '/parts/' in self.path: time.sleep(args.segment_delay)
                body = self.rfile.read(int(self.headers.get('Content-Length', 0))) if self.command == 'POST' else None
                headers = {k:v for k,v in self.headers.items() if k.lower() not in ('host', 'connection', 'content-length')}
                request = urllib.request.Request(upstream+self.path, data=body, headers=headers, method=self.command)
                try: response = urllib.request.urlopen(request, timeout=30)
                except urllib.error.HTTPError as error: response = error
                with response:
                    data = response.read()
                    self.send_response(response.status)
                    for key in ('Content-Type','X-Pds-Final','X-Pds-Progress-Warning'):
                        if response.headers.get(key): self.send_header(key,response.headers[key])
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Connection','close')
                self.end_headers()
                self.wfile.write(data)
            do_GET = forward
            do_POST = forward
        server = ThreadingHTTPServer(('127.0.0.1',8098),DelayedProxy)
        threading.Thread(target=server.serve_forever,daemon=True).start()
    if args.proxy or server:
        config['url'] = 'http://127.0.0.1:8098'
    shutil.copy(ROOT/'tests/native_plugin/main.lua', source/'main.lua')
    for name in ('net.lua', 'posters.lua', 'player.lua', 'ui.lua'):
        shutil.copy(ROOT/'client'/name, source/name)
    shutil.copytree(ROOT/'client/fonts', source/'fonts', dirs_exist_ok=True)
    (source/'config.lua').write_text('CLIENT_CONFIG = {backend="plugin", url='+json.dumps(config['url'])+
                                   ', token='+json.dumps(config['token'])+'}\nTEST_MEDIA_NAME='+json.dumps(args.media)+'\n')
    (source/'config.lua').chmod(0o600)
    (source/'pdxinfo').write_text('name=Jellyfin plugin test\nbundleID=com.scribhneoir.jf-plugin-smoke\nversion=0.3.0\n')
    dest = ROOT/'build/PluginSmoke.pdx'
    subprocess.run([str(pathlib.Path(args.sdk)/'bin/pdc'), '-sdkpath', args.sdk, str(source), str(dest)], check=True)
    result_path = args.data/'result.json'
    result_path.unlink(missing_ok=True)
    with (ROOT/'build/plugin-native-simulator.log').open('w') as output:
        command = [args.simulator, str(dest)]
        if args.isolate_usb: command = ['bwrap', '--bind', '/', '/', '--dev', '/dev', *command]
        try:
            subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=105, check=True)
        finally:
            if server: server.shutdown(); server.server_close()
    result = json.loads(result_path.read_text())
    (ROOT/'build/plugin-native-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
