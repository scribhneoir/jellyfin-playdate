# Jellyfin for Playdate

A native Playdate client with a **Jellyfin plugin** that converts videos to
`.pds` as they stream and serves native `.pdi` posters. The plugin runs inside
Jellyfin and uses its FFmpeg; no companion service is needed in plugin mode.
The original Python/Docker bridge remains available as an alternative.

## Plugin setup (Jellyfin 10.11.11)

See [plugin installation, architecture, and testing](docs/PLUGIN.md).
Recorded results are in [plugin validation](docs/PLUGIN-RESULTS.md).

In Jellyfin, open **Dashboard → Plugins → Repositories → Add** and add:

```text
https://raw.githubusercontent.com/scribhneoir/jellyfin-playdate/main/manifest.json
```

Install **Playdate** from the Catalog, then restart Jellyfin. The
[v0.3.0 release](https://github.com/scribhneoir/jellyfin-playdate/releases/tag/v0.3.0)
also includes an unconfigured Playdate client ZIP. See
[connection setup](docs/PLUGIN.md#connect-the-client) to connect it to your account.

For manual installation or a source build:

1. Build with `make plugin-package`, or use `build/Jellyfin.Plugin.Playdate-0.3.0.zip`.
2. Stop Jellyfin, extract the ZIP into its data directory's `plugins` folder,
   then start Jellyfin. The result is `plugins/Playdate/Jellyfin.Plugin.Playdate.dll`.
3. Set `JELLYFIN_URL`, `JELLYFIN_USERNAME`, and `JELLYFIN_PASSWORD` in your private
   `.env`, then run `python3 tools/configure_plugin.py`.
4. With the Playdate SDK configured, run `make client-package`
   (or `nix develop path:. --command make client-package` on this NixOS setup).

Plugin configuration saves a **Jellyfin user access token** in the private app,
never the password. Keep that configured app private. Revoked tokens require
running configuration again. The plugin enforces the account's library and
transcoding permissions.

The user verified all twelve diagnostic beeps lined up with the flashes in the
Simulator, then confirmed picture and audio from a real Jellyfin video.
Physical-device testing was skipped at the user's request. The native streaming
API is undocumented; this is an early personal-use client, not an official
Jellyfin or Panic application.

## What works

- Browse libraries, series, seasons, and episodes, with paginated lists.
- Search and continue watching using your Jellyfin account.
- Stream 400×240 one-bit video at 15 fps, with mono MP3 audio.
- Pause/resume, seek with the crank or D-pad, and save playback position.
- Keep encoding and buffering incremental; stop FFmpeg when playback stops.
- Download and cache 96×144 native `.pdi` posters on title details (plugin mode).
- Renew a Jellyfin login when its token expires (bridge mode).

The bridge supports one configured account and one conversion at a time. It
reads originals through Jellyfin's authenticated HTTP stream; shared media
folders or a Jellyfin plugin are not required. Video without an audio track gets
a silent track so the native playback clock still advances.

## Alternative: bridge setup

Requirements: Docker Compose supporting raw env files (2.30+), Python 3.11+,
Make, and a separately installed [Playdate SDK](https://play.date/dev/).
Local encoder tests also require FFmpeg with `libmp3lame`.

```sh
python3 tools/configure.py \
  --jellyfin-url https://your-jellyfin-server \
  --bridge-url http://127.0.0.1:8000
```

Edit `.env` and set `JELLYFIN_USERNAME` and `JELLYFIN_PASSWORD`. Values are raw:
**do not add quotes**; spaces, `#`, `$`, and `=` in passwords are kept literally.
Alternatively set `JELLYFIN_TOKEN` and its `JELLYFIN_USER_ID` instead of a password.
Use the account whose libraries and watch history you want on the Playdate.

`configure.py` generates a random bridge key, writes `.env` with mode 0600, and
creates `client/local_config.lua`. Both files are ignored by Git and excluded
from the Docker build. Re-running configuration preserves existing credentials
and the access key.

```sh
make up
export PLAYDATE_SDK_PATH=/path/to/PlaydateSDK
make client-package
"$PLAYDATE_SDK_PATH/bin/PlaydateSimulator" build/Jellyfin.pdx
```

On NixOS/x86_64 Linux:

```sh
nix develop path:. --command make client-package
nix develop path:. --command PlaydateSimulator build/Jellyfin.pdx
```

The pinned Nix environment supplies SDK 3.1.1. Docker includes Python and FFmpeg,
without SDK binaries. The packaged app is `build/Jellyfin.zip`.

`make up` runs `docker compose --env-file /dev/null up --build -d bridge`.
The explicit empty Compose interpolation file prevents password characters
from being treated as shell-style substitutions; the service loads `.env` using
`format: raw`. After changing credentials, run `make up` again to recreate the
container with the new environment.

## Controls

| Screen | Controls |
| --- | --- |
| Library or episode list | Up/Down or crank: select; A: open; B: back |
| Paged list | Left/Right: previous/next page |
| Video details | Select Resume or Play from beginning, then A |
| Playing | A: pause; B: return to details |
| Paused | A: resume |
| Seeking | Left/Right: 10 seconds; crank: 60 seconds per turn |
| Connection | System menu → Connection |

Seeking begins after the crank/D-pad settles, or immediately with A. Pause and
seek release the current stream; resuming starts a new conversion at the decoded
playback position. This avoids filling the device's memory during a long pause.
Expect a short buffering interval when resuming or seeking.

The client reports the decoded frame position every ten seconds and when you
stop. It does not infer watched progress from bytes downloaded. Abrupt exits can
resume up to about ten seconds earlier. If the bridge cannot save progress, the
client displays a warning. A disconnected client is eventually removed from
Jellyfin's active playback sessions using its last reported position.

## Connect the bridge over your LAN later

For a physical Playdate, configure the **bridge computer's LAN address**, not
Jellyfin's public address:

```sh
python3 tools/configure.py --bridge-url http://192.168.1.10:8000
BRIDGE_BIND_ADDRESS=0.0.0.0 make up
make client-package
```

Upload `build/Jellyfin.pdx` using the SDK's normal workflow, allow the Playdate's
network prompt, and join the same LAN. The bridge defaults to localhost until
`BRIDGE_BIND_ADDRESS` is supplied to `make up`. It is intended for a trusted LAN;
use an HTTPS reverse proxy if exposing it beyond that. TLS certificates are
verified for the upstream Jellyfin connection.

Your compiled app contains the bridge access key. Keep your private build and
`client/local_config.lua` private. It contains no Jellyfin password or user token.
To revoke a build, replace `BRIDGE_TOKEN` in `.env`, run `make configure`, recreate
the bridge with `make up`, and rebuild. If the device has saved a different
connection, choose **Use bundled connection** in its Connection screen.

## Diagnostics and tests

```sh
nix develop path:. --command make test
curl http://127.0.0.1:8000/health
# Do not dump container environment variables; they contain credentials.
docker compose --env-file /dev/null logs --tail 30 bridge
make down
```

Tests exercise real FFmpeg conversion through an isolated HTTP Jellyfin fixture,
progressive native framing, source byte ranges, seeking, cancellation, login
renewal, authentication boundaries, silent audio, and progress reporting.
The fixture contains original generated media and never changes your library.
`/health` checks the bridge process; an authenticated `/api/status` plus a library
request checks Jellyfin access.

The app writes `client-events.log` in its Playdate data directory. It logs screen
and playback states, without credentials or media URLs. Build and local test
artifacts go in `build/`.

The original format probe is preserved:

```sh
make fixture probe
"$PLAYDATE_SDK_PATH/bin/PlaydateSimulator" build/PDSProbe.pdx manual=1 seconds=0
# Separate diagnostic service, localhost port 8001:
docker compose --env-file /dev/null --profile probe up -d pds-probe
```

That service converts only the generated clip and has no authentication. Its
container listens on host port 8001, separately from the Jellyfin bridge on 8000.
Use the probe arguments `host=127.0.0.1 port=8001` for its HTTP stream.

## Current limits

Physical Playdate performance and long-running Wi-Fi playback remain untested.
This version selects Jellyfin's default audio track; it has no subtitle overlay,
track picker, live TV, offline downloads, or automatic next episode. Posters are
available in plugin mode. The plugin reads finite video files on the Jellyfin
server; the bridge reads through Jellyfin's static video endpoint. Audio and
video use independent FFmpeg processes. Encoding is CPU based, so movies with
very high decoding cost may need a faster server or a lower-complexity source.

See [the client implementation notes](docs/CLIENT.md),
[the native packet profile](docs/FORMAT.md), and
[the investigation history](docs/RESULTS.md). Undocumented native API behavior
may vary across SDK and firmware releases.
