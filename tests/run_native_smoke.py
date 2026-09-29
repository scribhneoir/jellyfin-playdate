#!/usr/bin/env python3
"""Opt-in SDK test; uses generated media and an isolated Jellyfin HTTP fixture.

Run from the project root inside the SDK environment, passing the Simulator's
actual data/result.json path. The ordinary unit suite does not launch a GUI.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

from test_bridge import BridgeTests, ROOT, TOKEN


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulator', default='PlaydateSimulator')
    parser.add_argument('--sdk', default=os.environ.get('PLAYDATE_SDK_PATH'))
    parser.add_argument('--result', required=True, type=pathlib.Path)
    parser.add_argument('--screenshot', type=pathlib.Path, help='optional Simulator image export')
    args = parser.parse_args()
    if not args.sdk:
        parser.error('set PLAYDATE_SDK_PATH or pass --sdk')
    BridgeTests.setUpClass()
    fixture = BridgeTests()
    fixture.setUp()
    try:
        source = ROOT/'build/native-test-source'
        source.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT/'tests/native/main.lua', source/'main.lua')
        for name in ('net.lua', 'player.lua', 'ui.lua'):
            shutil.copy(ROOT/'client'/name, source/name)
        shutil.copytree(ROOT/'client/fonts', source/'fonts', dirs_exist_ok=True)
        (source/'config.lua').write_text('CLIENT_CONFIG = { url = '+json.dumps(fixture.base)+', token = '+json.dumps(TOKEN)+' }\n')
        (source/'pdxinfo').write_text('name=Jellyfin playback test\nbundleID=com.scribhneoir.jf-native-smoke\nversion=0.2.0\n')
        dest = ROOT/'build/NativeSmoke.pdx'
        subprocess.run([str(pathlib.Path(args.sdk)/'bin/pdc'), '-sdkpath', args.sdk, str(source), str(dest)], check=True)
        args.result.unlink(missing_ok=True)
        with (ROOT/'build/native-smoke-simulator.log').open('w') as output:
            subprocess.run([args.simulator, str(dest), *([str(args.screenshot)] if args.screenshot else [])],
                           stdout=output, stderr=subprocess.STDOUT, timeout=105, check=True)
        result = json.loads(args.result.read_text())
        (ROOT/'build/native-smoke-result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
        if not result['ok']:
            return 1
        stopped = [data for path, data in fixture.upstream.reports if path.endswith('/Stopped')]
        assert len(stopped) == 3, f'Expected pause, seek, and exit reports, got {len(stopped)}'
        assert all(0 < report['PositionTicks'] < 30_000_000 for report in stopped)
    finally:
        fixture.tearDown()
        BridgeTests.tearDownClass()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
