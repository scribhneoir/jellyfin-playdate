# Plugin validation — 2026-09-29

Jellyfin **10.11.11**, .NET 9, Jellyfin FFmpeg 7.1.4, and Playdate Simulator
**3.1.1** on x86_64 NixOS. The live server's public endpoint also reports
10.11.11; all development and playback tests used an isolated local server
with original generated media and a disposable account.

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
reverse-proxy compatibility. They do not establish behavior through the user's
production Cloudflare configuration, which has not had the plugin installed.
Physical-device testing was skipped at the user's request. Long-running Wi-Fi,
device memory/throughput, and instrumented A/V drift remain untested. Native PDS
playback uses an undocumented SDK API; future firmware compatibility is not
guaranteed. The earlier user listening checks were for the original probe and
bridge, not this plugin build.

See [installation and API details](PLUGIN.md).
