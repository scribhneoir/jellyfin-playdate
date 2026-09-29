#!/usr/bin/env python3
"""Install from a repository in disposable Docker containers; never reads .env."""
import hashlib
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH = 'MediaBrowser Client="Repository Test", Device="Test", DeviceId="pds-repository-test", Version="0.3.0"'


def docker(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout.strip()


def main():
    name = 'pds-repository-test-' + secrets.token_hex(4)
    containers = []
    network = False
    with tempfile.TemporaryDirectory(prefix='repository-test-', dir=ROOT / 'build') as temporary:
        work = pathlib.Path(temporary)
        subprocess.run([sys.executable, str(ROOT / 'tools/package_plugin.py'),
                        '--repository-url', 'http://repository', '--output-dir', str(work)], check=True)
        manifest = json.loads((work / 'repository/manifest.json').read_text())[0]
        version = manifest['versions'][0]
        archive = work / 'repository' / version['sourceUrl'].rsplit('/', 1)[1]
        assert hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest() == version['checksum']
        with zipfile.ZipFile(archive) as package:
            assert package.namelist() == ['Jellyfin.Plugin.Playdate.dll']
            assert package.read(package.namelist()[0]) == (ROOT / 'build/plugin/Jellyfin.Plugin.Playdate.dll').read_bytes()
        try:
            docker('network', 'create', name)
            network = True
            docker('run', '-d', '--name', name + '-web', '--network', name,
                   '--network-alias', 'repository', '-v', str(work / 'repository') + ':/usr/share/nginx/html:ro',
                   'nginx:stable-alpine')
            containers.append(name + '-web')
            for directory in ('config', 'cache'):
                (work / directory).mkdir()
            docker('run', '-d', '--name', name + '-jellyfin', '--network', name,
                   '--user', f'{os.getuid()}:{os.getgid()}', '-p', '127.0.0.1::8096',
                   '-v', str(work / 'config') + ':/config', '-v', str(work / 'cache') + ':/cache',
                   'jellyfin/jellyfin:10.11.11')
            containers.append(name + '-jellyfin')
            address = docker('port', name + '-jellyfin', '8096/tcp')
            assert address.startswith('127.0.0.1:')
            base = 'http://' + address
            token = ''

            def api(path, data=None):
                request = urllib.request.Request(base + path,
                    data=None if data is None else json.dumps(data).encode(),
                    headers={'Content-Type': 'application/json',
                             'X-Emby-Authorization': AUTH + (', Token="' + token + '"' if token else '')})
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None

            def ready():
                deadline = time.monotonic() + 60
                while True:
                    try:
                        info = api('/System/Info/Public')
                        # The early setup listener can respond before the real API is ready.
                        if 'StartupWizardCompleted' in info:
                            return info
                    except (OSError, urllib.error.URLError):
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError('Disposable Jellyfin API did not become ready')
                    time.sleep(.5)

            assert ready()['StartupWizardCompleted'] is False
            password = secrets.token_hex(20)
            api('/Startup/Configuration', {'ServerName': 'Repository test', 'UICulture': 'en-US',
                                          'MetadataCountryCode': 'US', 'PreferredMetadataLanguage': 'en'})
            api('/Startup/User')
            api('/Startup/User', {'Name': 'repository-test', 'Password': password})
            api('/Startup/Complete', {})
            token = api('/Users/AuthenticateByName', {'Username': 'repository-test', 'Pw': password})['AccessToken']
            api('/Repositories', [{'Name': 'Playdate test', 'Url': 'http://repository/manifest.json', 'Enabled': True}])
            catalog = api('/Packages')
            matches = [item for item in catalog if uuid.UUID(item['guid']) == uuid.UUID(manifest['guid'])]
            assert matches, 'Playdate missing from catalog: ' + json.dumps(catalog)
            entry = matches[0]
            assert entry['versions'][0]['version'] == version['version']
            print('PASS: Jellyfin catalog reads the manifest and compatible version.', flush=True)
            api('/Packages/Installed/Playdate?version=' + version['version'], {})
            installed = work / 'config/plugins' / ('Playdate_' + version['version'])
            assert (installed / 'Jellyfin.Plugin.Playdate.dll').read_bytes() == (ROOT / 'build/plugin/Jellyfin.Plugin.Playdate.dll').read_bytes()
            assert (installed / 'meta.json').exists()
            print('PASS: Jellyfin downloads, verifies, and installs the repository ZIP.', flush=True)
            docker('restart', name + '-jellyfin')
            # Docker may allocate a new host port for an ephemeral mapping on restart.
            address = docker('port', name + '-jellyfin', '8096/tcp')
            assert address.startswith('127.0.0.1:')
            base = 'http://' + address
            ready()
            status = api('/Playdate/api/status')
            assert status['backend'] == 'plugin'
            plugins = api('/Plugins')
            plugin = next(item for item in plugins if uuid.UUID(item['Id']) == uuid.UUID(manifest['guid']))
            assert plugin['Status'] == 'Active', plugin['Status']
            print('PASS: Installed plugin is active after restart and its API responds.', flush=True)
            result = {'ok': True, 'jellyfin': '10.11.11', 'version': version['version'],
                      'catalog': True, 'download_and_install': True, 'active_after_restart': True,
                      'plugin_api': True, 'zip_md5': version['checksum']}
            (ROOT / 'build/plugin-repository-result.json').write_text(json.dumps(result, indent=2) + '\n')
        finally:
            for container in reversed(containers):
                docker('rm', '-f', container)
            if network:
                docker('network', 'rm', name)


if __name__ == '__main__':
    main()
