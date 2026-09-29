#!/usr/bin/env python3
"""Prepare generated media or initialize the isolated localhost Jellyfin test server."""
import argparse
import json
import os
import pathlib
import secrets
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST = ROOT / 'build/jellyfin-test'
BASE = 'http://127.0.0.1:8097'
AUTH = 'MediaBrowser Client="Playdate Test", Device="Simulator", DeviceId="pds-plugin-test", Version="0.3.0"'


def prepare():
    for directory in ('config/plugins/Playdate', 'cache', 'media/Diagnostic', 'media/Silent'):
        (TEST / directory).mkdir(parents=True, exist_ok=True)
    video = TEST / 'media/Diagnostic/Diagnostic.mkv'
    if not video.exists():
        subprocess.run([sys.executable, str(ROOT/'tools/make_fixture.py'), str(video)], check=True)
    silent = TEST / 'media/Silent/Silent.mkv'
    if not silent.exists():
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(video), '-t', '3',
                        '-an', '-c:v', 'copy', str(silent)], check=True)
    for i in range(21):
        dest = TEST / f'media/Page{i:02}/Page{i:02}.mkv'
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            os.link(video, dest)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + bytes(255 if (x//3+y//5)%2 else 0 for x in range(96)) for y in range(144))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 96, 144, 8, 0, 0, 0, 0)) +
           chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    (TEST/'media/Diagnostic/poster.png').write_bytes(png)
    print('Prepared original diagnostic video, silent video, pagination fixtures, and poster.')


def initialize():
    def api(path, data=None, token=''):
        headers = {'Content-Type':'application/json', 'X-Emby-Authorization':AUTH + (', Token="'+token+'"' if token else '')}
        with urllib.request.urlopen(urllib.request.Request(BASE+path, headers=headers,
                data=None if data is None else json.dumps(data).encode()), timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    deadline = time.monotonic()+45
    while True:
        try:
            status = api('/System/Info/Public')
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() > deadline:
                raise
            time.sleep(.5)
    path = TEST/'connection.json'
    if path.exists():
        private = json.loads(path.read_text())
        assert private['url'] == BASE
        username, password = private['username'], private['password']
    else:
        assert status.get('StartupWizardCompleted') is False, 'Refusing to reconfigure an existing unrelated Jellyfin server'
        username, password = 'playdate-test', secrets.token_hex(20)
        api('/Startup/Configuration', {'ServerName':'Playdate plugin test', 'UICulture':'en-US',
            'MetadataCountryCode':'US', 'PreferredMetadataLanguage':'en'})
        api('/Startup/User')
        api('/Startup/User', {'Name':username, 'Password':password})
        api('/Startup/Complete', {})
    login = api('/Users/AuthenticateByName', {'Username':username, 'Pw':password})
    token = login['AccessToken']
    private = {'url':BASE, 'token':token, 'user_id':login['User']['Id'],
        'username':username, 'password':password, 'auth':AUTH}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(private, output)
    config = api('/System/Configuration', token=token)
    config['MinResumeDurationSeconds'] = 1
    api('/System/Configuration', config, token)
    libraries = api('/Library/VirtualFolders', token=token)
    if not any(item['Name'] == 'Diagnostics' for item in libraries):
        api('/Library/VirtualFolders?name=Diagnostics&collectionType=movies&refreshLibrary=true',
            {'LibraryOptions': {'PathInfos':[{'Path':'/media'}], 'EnableInternetProviders':False,
            'TypeOptions':[{'Type':'Movie','MetadataFetchers':[],'ImageFetchers':[]}]}}, token)
    api('/Library/Refresh', {}, token)
    deadline = time.monotonic()+60
    while True:
        listing = api('/Playdate/api/items?search=Diagnostic', token=token)
        pages = api('/Playdate/api/items?search=Page', token=token)
        silent = api('/Playdate/api/items?search=Silent', token=token)
        if listing['items'] and listing['items'][0]['poster'] and pages['total'] == 21 and silent['items']:
            break
        if time.monotonic() > deadline:
            raise TimeoutError('Generated library did not finish indexing')
        time.sleep(.5)
    print('Initialized isolated Jellyfin 10.11.11 and indexed all generated fixtures.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    prepare() if args.prepare else initialize()
