#!/usr/bin/env python3
"""Encode a local video to experimental native PDS, incrementally.

Requires FFmpeg with libmp3lame. No SDK binaries are used or redistributed.
"""
import argparse
import contextlib
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

from pds import FRAME_BYTES, SAMPLE_RATE, SAMPLES_PER_MP3_FRAME, encode_video, mp3_size, read_exact


def encode(source, output, fps=15, realtime=False, *, start=0, duration=None,
           audio_index=None, has_audio=True, cancel=None):
    if fps not in (5, 10, 15, 30) or start < 0 or (duration is not None and duration <= 0):
        raise ValueError('invalid frame rate or playback interval')
    if not has_audio and duration is None:
        raise ValueError('a duration is required to synthesize silent audio')
    base = ["ffmpeg", "-nostdin", "-v", "error"]
    if realtime:
        base += ["-re"]
    if str(source).startswith(('http://', 'https://')):
        base += ['-rw_timeout', '15000000']
    if start:
        base += ['-ss', str(start)]
    base += ["-i", str(source)]
    interval = ['-t', str(duration)] if duration is not None else []
    video = base + [
        "-map", "0:v:0", "-an", "-vf",
        f"fps={fps}:start_time=0,scale=400:240:force_original_aspect_ratio=decrease,"
        "pad=400:240:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=gray",
        "-sws_dither", "bayer", "-pix_fmt", "monob", *interval, "-f", "rawvideo", "pipe:1",
    ]
    audio_base = base if has_audio else ['ffmpeg', '-nostdin', '-v', 'error',
        '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono']
    audio_map = '0:a:0' if audio_index is None or not has_audio else f'0:{int(audio_index)}'
    audio = audio_base + [
        "-map", audio_map, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        '-af', 'aresample=async=1:first_pts=0' + (',apad' if duration is not None else ''),
        "-c:a", "libmp3lame", "-b:a", "64k", "-reservoir", "0",
        "-write_xing", "0", "-id3v2_version", "0", "-write_id3v1", "0",
        *interval, "-f", "mp3", "pipe:1",
    ]
    with contextlib.ExitStack() as stack:
        processes = []
        errors = []
        watch_done = threading.Event()
        watcher = None
        try:
            for command in (video, audio):
                error = stack.enter_context(tempfile.TemporaryFile())
                processes.append(subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error))
                errors.append(error)
            if cancel is not None:
                def watch_cancel():
                    while not watch_done.wait(0.05):
                        if cancel.is_set():
                            for process in processes:
                                if process.poll() is None:
                                    try:
                                        process.terminate()
                                    except ProcessLookupError:
                                        pass
                            return
                watcher = threading.Thread(target=watch_cancel, daemon=True)
                watcher.start()
            vp, ap = processes
            previous = None
            frame_count = audio_count = 0
            video_done = audio_done = False
            while not (video_done and audio_done):
                if cancel is not None and cancel.is_set():
                    raise ConnectionAbortedError('playback cancelled')
                # Integer sample clocks keep mux ordering independent of rounding.
                want_video = not video_done and (
                    audio_done or frame_count * SAMPLE_RATE <= audio_count * SAMPLES_PER_MP3_FRAME * fps
                )
                if want_video:
                    frame = read_exact(vp.stdout, FRAME_BYTES, allow_eof=True)
                    if frame is None:
                        video_done = True
                        continue
                    packet = encode_video(frame, previous, frame_count % fps == 0)
                    previous = frame
                    frame_count += 1
                else:
                    header = read_exact(ap.stdout, 4, allow_eof=True)
                    if header is None:
                        audio_done = True
                        continue
                    packet = header + read_exact(ap.stdout, mp3_size(header) - 4)
                    audio_count += 1
                output.write(packet)
                output.flush()
            for process, error in zip(processes, errors):
                if process.wait() != 0:
                    error.seek(0)
                    raise RuntimeError(error.read().decode(errors="replace").strip())
            if not frame_count or not audio_count:
                raise ValueError("the experimental profile requires both video and audio")
            return {"video_frames": frame_count, "audio_frames": audio_count, "fps": fps}
        finally:
            watch_done.set()
            if watcher:
                watcher.join()
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=pathlib.Path)
    parser.add_argument("output", help=".pds filename, or - for stdout")
    parser.add_argument("--fps", type=int, choices=(5, 10, 15, 30), default=15)
    parser.add_argument("--realtime", action="store_true", help="pace source reads to test progressive delivery")
    args = parser.parse_args()
    try:
        if not args.input.is_file():
            parser.error("input must be an existing local video file")
        if args.output != "-" and pathlib.Path(args.output).resolve() == args.input.resolve():
            parser.error("output must differ from input")
        if args.output == "-":
            result = encode(args.input, sys.stdout.buffer, args.fps, args.realtime)
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=pathlib.Path(args.output).parent,
                        prefix='.pds-', delete=False) as output:
                    temporary = pathlib.Path(output.name)
                    result = encode(args.input, output, args.fps, args.realtime)
                os.replace(temporary, args.output)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        print(result, file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"encode: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
