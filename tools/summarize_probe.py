#!/usr/bin/env python3
"""Summarize probe logs and optional isolated-sink audio; no absolute A/V claim."""
import argparse
import array
import csv
import json
import pathlib
import sys
import wave


def summarize(path, audio_path=None):
    with path.open() as source:
        rows = list(csv.DictReader(source, delimiter='\t'))
    frames = [row for row in rows if row['event'] in ('first_frame', 'frame')]
    flashes = [float(row['seconds']) for row in rows if row['event'] == 'flash']
    result = {
        'events_file': path.name,
        'frame_events': len(frames),
        'first_frame_seconds': float(frames[0]['seconds']) if frames else None,
        'last_frame_seconds': float(frames[-1]['seconds']) if frames else None,
        'last_frame_index': int(frames[-1]['frame']) if frames else None,
        'maximum_buffered_frames': max((int(row['buffered']) for row in rows), default=0),
        'bytes_read': max((int(row['bytes']) for row in rows), default=0),
        'maximum_lua_kib': max((float(row['lua_kib']) for row in rows), default=0),
        'flashes_seconds': flashes,
        'transport_events': [dict(row) for row in rows if row['event'] in
                             ('error', 'underrun', 'connection_closed', 'audio_started')],
    }
    if audio_path:
        with wave.open(str(audio_path), 'rb') as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError('expected mono 16-bit PCM WAV')
            rate = source.getframerate()
            samples = array.array('h', source.readframes(source.getnframes()))
        if sys.byteorder != 'little':
            samples.byteswap()
        window = rate // 100  # 10 ms resolution; require a substantial test tone.
        beeps, active = [], False
        for start in range(0, len(samples), window):
            sounding = max(map(abs, samples[start:start+window]), default=0) > 500
            if sounding and not active:
                beeps.append(start / rate)
            active = sounding
        result['beeps_seconds'] = beeps
        if len(beeps) == len(flashes) and len(beeps) > 1:
            # Capture and probe clocks have an unknown constant offset. Remove
            # their initial offset, retaining drift/jitter across the clip.
            drift = [(b-beeps[0]) - (f-flashes[0]) for b, f in zip(beeps, flashes)]
            result['relative_av_drift_seconds'] = drift
            result['maximum_absolute_relative_drift_seconds'] = max(map(abs, drift))
        else:
            result['audio_note'] = 'pulse counts differ; do not infer synchronization'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=pathlib.Path)
    parser.add_argument('--audio', type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.events, args.audio), indent=2))
