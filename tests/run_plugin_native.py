#!/usr/bin/env python3
"""Run real plugin playback and downloaded PDI images in the Playdate Simulator."""
import argparse
import json
import os
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulator', default='PlaydateSimulator')
    parser.add_argument('--sdk', default=os.environ.get('PLAYDATE_SDK_PATH'))
    parser.add_argument('--data', required=True, type=pathlib.Path)
    parser.add_argument('--proxy', action='store_true', help='test through the isolated Nginx on port 8098')
    args = parser.parse_args()
    source = ROOT/'build/plugin-native-source'
    source.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT/'build/jellyfin-test/connection.json').read_text())
    assert config['url'] == 'http://127.0.0.1:8097'
    if args.proxy:
        config['url'] = 'http://127.0.0.1:8098'
    shutil.copy(ROOT/'tests/native_plugin/main.lua', source/'main.lua')
    for name in ('net.lua', 'posters.lua', 'player.lua', 'ui.lua'):
        shutil.copy(ROOT/'client'/name, source/name)
    shutil.copytree(ROOT/'client/fonts', source/'fonts', dirs_exist_ok=True)
    (source/'config.lua').write_text('CLIENT_CONFIG = {backend="plugin", url='+json.dumps(config['url'])+
                                   ', token='+json.dumps(config['token'])+'}\n')
    (source/'config.lua').chmod(0o600)
    (source/'pdxinfo').write_text('name=Jellyfin plugin test\nbundleID=com.scribhneoir.jf-plugin-smoke\nversion=0.3.0\n')
    dest = ROOT/'build/PluginSmoke.pdx'
    subprocess.run([str(pathlib.Path(args.sdk)/'bin/pdc'), '-sdkpath', args.sdk, str(source), str(dest)], check=True)
    result_path = args.data/'result.json'
    result_path.unlink(missing_ok=True)
    with (ROOT/'build/plugin-native-simulator.log').open('w') as output:
        subprocess.run([args.simulator, str(dest)], stdout=output, stderr=subprocess.STDOUT, timeout=105, check=True)
    result = json.loads(result_path.read_text())
    (ROOT/'build/plugin-native-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
