#!/usr/bin/env python3
"""Create private bridge/client configuration without printing credentials."""
import argparse
import json
import os
import pathlib
import secrets
from urllib.parse import urlsplit
from settings import read_env

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-url', help='address the Playdate uses to reach this computer')
    parser.add_argument('--jellyfin-url')
    args = parser.parse_args()
    path = ROOT / '.env'
    values = read_env(ROOT / '.env.example') | read_env(path)
    if args.bridge_url:
        values['BRIDGE_URL'] = args.bridge_url
    if args.jellyfin_url:
        values['JELLYFIN_URL'] = args.jellyfin_url
    if not values.get('BRIDGE_TOKEN'):
        values['BRIDGE_TOKEN'] = secrets.token_hex(24)
    for name in ('BRIDGE_URL', 'JELLYFIN_URL'):
        url = urlsplit(values[name])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.query or url.fragment:
            parser.error(f'{name} must be an http(s) URL without credentials, query, or fragment')
    if len(values['BRIDGE_TOKEN']) < 24:
        parser.error('BRIDGE_TOKEN must contain at least 24 characters')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('# Raw values: no quotes or shell expansion. Keep this file private.\n')
        for key, value in values.items():
            f.write(f'{key}={value}\n')
    config = ROOT / 'client/local_config.lua'
    config.parent.mkdir(exist_ok=True)
    config.write_text('-- Generated private bridge connection. No Jellyfin credentials.\n'
        'CLIENT_CONFIG = { url = '+json.dumps(values['BRIDGE_URL'].rstrip('/'))+
        ', token = '+json.dumps(values['BRIDGE_TOKEN'])+' }\n')
    config.chmod(0o600)
    print('Saved .env and client/local_config.lua. Fill in the Jellyfin login in .env, then build the client.')


if __name__ == '__main__':
    main()
