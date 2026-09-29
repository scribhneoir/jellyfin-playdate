# Playback investigation results

The current Jellyfin plugin and native poster/streaming validation are documented
in [PLUGIN-RESULTS.md](PLUGIN-RESULTS.md). The sections below preserve the original
format investigation and its subsequent user verification.

**Updated status: the user verified all twelve beeps clearly aligned with the
flashes in the Simulator using the normal audio output. The user then confirmed
picture and audio from a real Jellyfin video in the new client.** The earlier
automated-capture blocker below is retained as investigation history. It is not
evidence that native playback is silent. The user explicitly skipped hardware
testing and authorized the Jellyfin client implementation.

The listening check is human verification, not an instrumented lip-sync
measurement. Physical hardware and long-duration playback remain untested.

Test date: 2026-09-29. Host: x86_64 NixOS. The tested native runtime is Playdate
Simulator 3.1.1 from the pinned Nix development environment. SDK 3.1.2 headers
were inspected separately; an attempted 3.1.2 runtime comparison did not finish
startup in the allotted 35 seconds and provides no playback validation. No
physical Playdate was available.

## What was implemented

- A Linux encoder using FFmpeg and Python's standard library. It emits native
  packets incrementally, supports keyframes/XOR deltas and interleaved MP3, and
  requires no proprietary encoder or SDK on the server.
- A Docker diagnostic endpoint that converts a configured local clip on demand.
  It also supports controlled pauses, bandwidth limits, and mid-packet cuts.
- A native Lua probe with file/HTTP inputs, hardware configuration, pause/exit
  controls, error handling, and timing/buffer logs.
- A generated flash/beep fixture, a strict packet inspector, and media tests.

There is no Jellyfin authentication, library UI, item selection, progress API,
seek/resume protocol, or production conversion queue in this milestone.

## Packet and video evidence

The generated 12-second diagnostic encodes to **311,868 bytes**, containing
180 video frames (12 keyframes, 168 deltas) and 461 MP3 frames. Its effective
combined bandwidth is approximately **26 kB/s (208 kbit/s)**. Compressed audio
duration is 12.04245 seconds, including encoder delay/padding.

The native decoder displayed all 180 frames from a local file and over HTTP.
The captured native final frame matched the independently reconstructed bitmap
at frame 179 **byte for byte across all 12,000 bitmap bytes**. The original
generated screenshot is [included here](evidence/native-final.png); the check
result is in [pixel-check.json](evidence/pixel-check.json).

The Docker server emitted its first native packet **0.190 seconds** after the
request began, finishing conversion at **12.064 seconds**. Thus playback does
not require a completed `.pds` download or a known final Content-Length.
See [server timings](evidence/docker-service.log).

## Native playback observations

Times below are relative to each probe's startup; they include connection and
permission/initialization overhead and are not server encoding times. Frame
indices are zero based. These are single diagnostic runs, not benchmarks.

| Scenario | First frame | Last observed frame | Maximum buffered frames | Outcome |
| --- | ---: | ---: | ---: | --- |
| Local file | 0.029 s | 179 at 12.011 s | 127 | All 180 frames |
| Docker HTTP, paced input | 3.442 s | 179 at 15.417 s | 4 | All 180 frames; 311,868 bytes |
| Two-second injected stall | 3.937 s | 169 at 19.978 s | 92 | Resumed after a substantial pause; timer ended before final frames |
| 16,000 byte/s delivery | 8.437 s | 97 at 22.927 s | 74 | Stalled; insufficient throughput |
| Disconnect at byte 100,003 | 3.938 s | 58 at 7.842 s | 4 | Stopped advancing; connection closed; exited without crash |
| HTTP 404 | — | — | 0 | Logged HTTP 404 and released the stream |

Raw logs and a machine-readable summary are in [evidence/](evidence/).
The slow/stalled runs do **not** demonstrate satisfactory recovery or maintained
sync. A finish callback did not reliably report underruns for this stream, even
with `setStopOnUnderrun(true)`; the probe must not rely on it as its only failure
signal. A truncated transport is visible as a connection close and halted frame
progress, without a reliable native corruption error. There is no automatic
reconnect or resume implementation.

The normal HTTP run held at most four video frames, while the local-file run
reached 127 buffered frames. The probe configures a 65,536-byte HTTP read buffer.
The encoder itself holds a previous bitmap and current packets rather than a
movie-sized buffer. These observations support progressive, bounded buffering
for this short test; they do not establish a long-duration native memory bound.

Observed Lua heap was approximately 142–152 KiB in HTTP playback. Native audio,
video, and HTTP allocations are excluded. The Simulator process peaked around
125,000 KiB RSS, including its desktop UI/runtime. Neither value is a physical
Playdate RAM measurement. Hardware memory headroom remains unverified.

## Initial automated audio-capture blocker (superseded by user verification)

Demultiplexed generated MP3 frames decode into non-silent PCM with FFmpeg in both
the host and Docker tests. That validates the encoder's audio payload, but not
the native output path or A/V synchronization.

Native output was recorded from a dedicated, unmuted PulseAudio/PipeWire null
sink, never from the user's desktop output. Normal probe runs produced silence
in both FFmpeg and direct PulseAudio captures despite video advancing and the
fileplayer reporting playback. An explicit sound channel, channel/fileplayer
volume 1, and selecting active outputs did not resolve it. During one run with
an optional independent synth control tone, an FFmpeg capture contained nine
regularly spaced pulses, fewer than the twelve expected diagnostic pulses.
That observation did not reproduce in a subsequent direct PulseAudio capture
with the same control tone.
This is insufficient evidence to assign the problem to the encoder, native
API usage, Simulator initialization, or the host audio stack.

Consequently **no instrumented absolute lip-sync or A/V drift result is claimed**. The
summarizer explicitly refuses a drift calculation when flash/beep counts differ.
The optional `control_tone=1` probe argument exists to help reproduce this audio
initialization discrepancy; it is not enabled by default or treated as a fix.

Also, setting the native fileplayer volume to zero caused an earlier local-file
run to stall at frame 149. Silent automated tests should keep volume 1 and route
the process to an isolated sink.

## Validation and next gate

The six automated tests passed in the pinned host environment and in the final
Docker image (Debian FFmpeg 5.1.9). They cover exact frame reconstruction,
malformed/truncated packets, MP3 frame padding/profile, real FFmpeg conversion
and audio decoding, emission before conversion completes, and preserving an
existing output when conversion fails. The native probe compiles successfully.
Logs: [host checks](evidence/checks.log), [Docker checks](evidence/docker-tests.log).

The user subsequently authorized building the Jellyfin UI and authenticated
bridge after verifying the Simulator playback. The following remain useful
follow-up investigations, rather than gates blocking that work:

1. Reproduce native audio on a physical device or a known-good Simulator/audio
   setup. Establish whether stream construction requires additional sound
   initialization. Record all twelve diagnostic pulses reliably.
2. Measure flash/beep alignment against a common clock and repeat after a stall.
3. Run a longer clip on hardware and measure native memory, sustained Wi-Fi
   throughput, interruption recovery, and pause behavior.
4. Confirm the undocumented API's support/compatibility boundary with Panic or
   its native-stream consumers; expand the packet profile only with evidence.

No alternate video format was substituted to bypass these gates. SDK binaries,
public reference footage, and PeeDeeTV implementation code are not shipped.
