# Plugin validation — 2026-09-29

Jellyfin **10.11.11**, .NET 9, Jellyfin FFmpeg 7.1.4, and Playdate Simulator
**3.1.1** on x86_64 NixOS. The live server's public endpoint also reports
10.11.11. The initial automated tests below used an isolated local server
with original generated media and a disposable account. Subsequent production
and hardware observations are recorded separately below.

## Results

- The native client passed directly against Jellyfin and through Nginx:
  PDI download, all 13,824 decoded poster pixels, cached reload without HTTP,
  nonblank decoded video, pause/resume, seek, stop, and complete playback.
  Both runs consumed **303,072 PDS bytes** and reached **frame 179 of 180**,
  reporting the final decoded position **11.9333 seconds** for the 12-second
  fixture. No progress-save warnings occurred.
- The existing native bridge test passed with the updated client: playback,
  held-frame pause, resume, disappearing controls, seek, and stop reporting.
- The 14 existing Python encoder/bridge tests passed.
- All nine plugin integration tests passed: authentication, user/library and
  transcoding permissions, session ownership, pagination, silent video, PDI
  encoding, progressive conversion, seeking/progress, segment integrity,
  cancellation, and abandoned-session expiry. The segmented four-second seek
  stream matched the continuous stream byte for byte: **100,975 bytes**,
  **60 video frames**, and **155 MP3 frames**. Stopping saved **9.5 seconds**;
  an unconsumed session expired without altering watch progress.
- The plugin configuration helper authenticated with the generated test
  account, verified the plugin endpoint, and wrote its private Lua connection
  with mode 0600. Production credentials and the previous bridge configuration
  were left intact.
- Both ZIP packages passed archive-integrity and content checks. The plugin ZIP
  contains only its DLL and Jellyfin manifest. The separate client package uses
  the empty public connection template and contains no native C module.
- Repository installation passed in a fresh disposable Jellyfin 10.11.11 server:
  catalog discovery, ZIP download and checksum verification, installation, and
  an active plugin with a working API after restart. The repository archive
  contains only the DLL at its root; Jellyfin creates the installed metadata.
  Repackaging the same DLL produces identical ZIP bytes.

Evidence: [direct native run](evidence/plugin-native-direct.json),
[Nginx native run](evidence/plugin-native-proxy.json),
[bridge regression](evidence/plugin-bridge-regression.json), and
[14 Python checks](evidence/plugin-unit-tests.log). Plugin evidence:
[nine integration checks](evidence/plugin-integration.log) and
[measured stream results](evidence/plugin-integration.json).
Repository evidence: [catalog installation and restart](evidence/plugin-repository.json).

## Limits

These checks establish local conversion, native decoding, controls, and ordinary
reverse-proxy compatibility. The plugin has since been installed on the production
server; authentication, library browsing, and movie/show/season posters were
verified there. Hardware playback now has a reproduced failure, described below.
Long-running Wi-Fi, device memory/throughput, and instrumented A/V drift remain
unverified. Native PDS
playback uses an undocumented SDK API; future firmware compatibility is not
guaranteed. The earlier user listening checks were for the original probe and
bridge, not this plugin build.

## Subsequent hardware investigation

On Playdate OS 3.1.2, the 0.3.1 client with diagnostic counters consumed the first
two segments (20,860 and 20,892 bytes), then issued the request for segment index
2. That request produced no headers or additional decoder bytes before the
30-second watchdog fired. The decoder reached frame 28, exhausted its buffered
frames, and reported an audio underrun. This narrows the failure to obtaining
the next segment; it does not establish whether connection reuse, concurrent
progress requests, or another transport issue is responsible.

The [device capture](evidence/device-segment-stall.json) includes no server token,
media identifier, or session identifier. Simulator success does not validate
this device behavior. Disabling separate progress POSTs allowed continuous
playback on hardware, but the user reported buffering between one-second clips.
The continuous chunked endpoint then failed on both hardware and SDK 3.1.2's
Simulator. The Simulator's byte counters included HTTP chunk framing.

## Version 0.4.0 change and checks

The client now requests eight-second, length-delimited segments, buffers three
seconds initially, and sends decoded progress with the next segment request.
The server encodes ahead within bounded buffers. Both client and plugin must be
upgraded. The client is compiled with SDK 3.1.2.

The 14 encoder/bridge tests and 11 plugin integration tests passed. Integration
checks cover eight-second segmentation, byte identity, progress carried with
segments, early-stop progress, and invalid requests leaving progress unchanged.
The first eight-second segment contained 201,678 bytes and arrived in 0.11 seconds
on the isolated local fixture. These timings do not predict device Wi-Fi latency.

Evidence: [14 unit checks](evidence/client-0.4-unit.log),
[11 plugin checks](evidence/plugin-0.4-integration.log), and
[measurements](evidence/plugin-0.4-measurements.json).

Final native playback and hardware validation were deferred at the user's request
to ship the candidate promptly. The 0.4.0 hardware fix is not yet confirmed.

## Version 0.4.0 hardware result and 0.4.1 candidate

Hardware playback reached frame 238 (15.87 seconds), then stalled. Segment zero
was fully consumed (192,658 bytes). Segment one advertised another 153,509 bytes,
but the cumulative native byte counter stopped at 345,686 rather than 346,167.
There were no bytes available and no overlapping control request. The client
never requested segment two because it was still waiting for those 481 bytes.
This identifies the blocked client condition; it does not establish why the
native connection stopped delivering/counting bytes.

[Device evidence](evidence/device-0.4-stall.json) records the request/header events
and final watchdog failure without media IDs or credentials.

Client 0.4.1 closes the connection after each fully consumed segment and requests
`Connection: close`, resetting HTTP/TCP state before the next segment. It keeps
eight-second segments, the same native player, and progress in segment requests.
Plugin 0.4.0 remains compatible. This is a candidate change: client compilation
and package checks were performed, but its hardware playback is not yet verified.

See [installation and API details](PLUGIN.md).
