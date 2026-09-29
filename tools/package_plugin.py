#!/usr/bin/env python3
"""Package the plugin for manual installation and, optionally, a hosted repository."""
import argparse
import datetime
import hashlib
import json
import pathlib
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def write_zip(path, entries):
    # Fixed ZIP metadata keeps checksums stable when repackaging the same DLL.
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as package:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.zip.sha256').write_text(checksum + '  ' + path.name + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-url', help='HTTP(S) directory where the repository ZIP will be hosted')
    parser.add_argument('--output-dir', type=pathlib.Path, default=ROOT / 'build')
    args = parser.parse_args()
    if args.repository_url:
        url = urllib.parse.urlsplit(args.repository_url)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username
                or url.password or url.query or url.fragment
                or any(c.isspace() for c in args.repository_url)):
            parser.error('--repository-url must be an HTTP(S) directory URL without credentials, query, or fragment')

    project = ET.parse(ROOT / 'plugin/Jellyfin.Plugin.Playdate.csproj')
    version = project.findtext('./PropertyGroup/Version')
    target = project.find("./ItemGroup/PackageReference[@Include='Jellyfin.Controller']").get('Version')
    assembly = ROOT / 'build/plugin/Jellyfin.Plugin.Playdate.dll'
    binary = assembly.read_bytes()
    package_info = {
        'guid': '9a601fa6-3a03-49d3-a64e-2be2c824c3ad',
        'name': 'Playdate',
        'description': 'Native PDS video streaming and PDI posters for Playdate.',
        'overview': 'Stream your library directly to Playdate.',
        'owner': 'scribhneoir', 'category': 'General',
    }
    metadata = dict(package_info, version=version, targetAbi=target + '.0',
                    status='Active', autoUpdate=False, assemblies=[assembly.name])
    metadata_bytes = (json.dumps(metadata, indent=2) + '\n').encode()
    (assembly.parent / 'meta.json').write_bytes(metadata_bytes)
    filename = 'Jellyfin.Plugin.Playdate-' + version.removesuffix('.0') + '.zip'
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manual = args.output_dir / filename
    write_zip(manual, {'Playdate/' + assembly.name: binary, 'Playdate/meta.json': metadata_bytes})
    print('Manual installation ZIP: ' + str(manual))

    if args.repository_url:
        directory = args.output_dir / 'repository'
        directory.mkdir(exist_ok=True)
        archive = directory / filename
        # Jellyfin creates Playdate_VERSION and meta.json itself during installation.
        write_zip(archive, {assembly.name: binary})
        release = {
            'version': version, 'targetAbi': target + '.0',
            'sourceUrl': args.repository_url.rstrip('/') + '/' + filename,
            # Jellyfin 10.11's repository installer requires MD5, not SHA-256.
            'checksum': hashlib.md5(archive.read_bytes(), usedforsecurity=False).hexdigest(),
            'timestamp': datetime.datetime.fromtimestamp(assembly.stat().st_mtime,
                datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'changelog': 'Native PDS streaming, playback progress, and cached PDI posters.',
        }
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps([dict(package_info, versions=[release])], indent=2) + '\n')
        print('Repository files: ' + str(directory))
        print('Host manifest.json and the repository ZIP; add the manifest URL in Jellyfin.')


if __name__ == '__main__':
    main()
