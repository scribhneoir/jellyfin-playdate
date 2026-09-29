#!/usr/bin/env python3
"""Validate the experimental PDS profile and print packet/timing statistics."""
import argparse
import json
from pds import SAMPLE_RATE, SAMPLES_PER_MP3_FRAME, decode_video, packets


def inspect(source):
    counts = {"keyframes": 0, "delta_frames": 0, "audio_frames": 0}
    previous = None
    for kind, payload in packets(source):
        if kind == "audio":
            counts["audio_frames"] += 1
        else:
            previous = decode_video(kind, payload, previous)
            counts["keyframes" if kind == 0xC1 else "delta_frames"] += 1
    frames = counts["keyframes"] + counts["delta_frames"]
    duration = counts["audio_frames"] * SAMPLES_PER_MP3_FRAME / SAMPLE_RATE
    if not frames or not duration:
        raise ValueError("expected nonempty video and audio tracks")
    return dict(counts, video_frames=frames, audio_seconds=duration,
                inferred_fps=frames / duration, width=400, height=240)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    args = parser.parse_args()
    try:
        with open(args.file, "rb") as source:
            print(json.dumps(inspect(source), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f"inspect: {error}\n")
