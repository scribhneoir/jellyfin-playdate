#!/usr/bin/env python3
"""Sign in to the installed plugin and save a private Playdate connection."""
import argparse
import json
import os
import pathlib
from jellyfin import ApiError, Jellyfin
from settings import read_env

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=pathlib.Path, default=ROOT / '.env')
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'client/local_config.lua')
    args = parser.parse_args()
    try:
        jf = Jellyfin(read_env(args.env))
        jf.auth_base = ('MediaBrowser Client="Playdate Jellyfin", Device="Playdate", '
                       'DeviceId="playdate-pds-client", Version="0.4.0"')
        jf.login()
        status = jf.request('/Playdate/api/status')
        if status.get('backend') != 'plugin':
            raise ValueError('Install the Playdate plugin on this Jellyfin server first')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write('-- Private Jellyfin user token; never share this file or its compiled app.\n'
                         'CLIENT_CONFIG = { backend = "plugin", url = ' + json.dumps(jf.base) +
                         ', token = ' + json.dumps(jf.token) + ' }\n')
        print('Connected to the Playdate plugin. Saved private client configuration; run make client-package.')
        return 0
    except (ApiError, ValueError, OSError) as error:
        print('Plugin setup failed: ' + str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
