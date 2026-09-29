#!/usr/bin/env python3
"""Make an original, lossless test clip with one flash and beep per second."""
import argparse
import pathlib
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--seconds', type=int, default=12)
args = parser.parse_args()
if args.seconds < 1:
    parser.error('seconds must be positive')
args.output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
    "testsrc2=size=400x240:rate=15,"
    "drawbox=x=0:y=0:w=80:h=80:color=black:t=fill,"
    "drawbox=x=0:y=0:w=80:h=80:color=white:t=fill:enable='lt(mod(t,1),0.1)'",
    '-f', 'lavfi', '-i',
    r'aevalsrc=if(lt(mod(t\,1)\,0.1)\,0.3*sin(2*PI*1000*t)\,0):s=44100',
    '-t', str(args.seconds), '-c:v', 'ffv1', '-c:a', 'pcm_s16le', '-y', str(args.output),
], check=True)
