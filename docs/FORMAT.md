# Experimental native PDS profile

This is an empirical description of the packets emitted by this encoder. It is
not an official specification or a claim that every native stream is supported.
Here `.pds` means the requested native video stream; the SDK also uses that
extension for unrelated string-table assets.

## Evidence and provenance

Investigated on 2026-09-29:

- [Official SDK downloads](https://play.date/dev/): Linux SDK 3.1.2 headers expose
  `playdate_videostream` in `C_API/pd_api/pd_api_gfx.h`, including file/HTTP/TCP
  inputs, audio/video players, buffer counts, and bytes read. The structure sits
  in a section marked 3.0. Its presence does not establish a stable public API.
  The [3.1.2 Lua manual](https://sdk.play.date/3.1.2/) does not describe it.
- [PeeDeeTV](https://peedee.tv/) is a public native-stream consumer. Its public
  app download was inspected for API-name strings; no implementation code or
  assets were copied into this project. The public
  [PDZ container description](https://github.com/cranksters/playdate-reverse-engineering/blob/main/formats/pdz.md)
  was used to read those strings.
- A five-second, bounded download from the public
  [channel 1 stream](https://api.peedee.tv/stream/1) exposed packet boundaries.
  That prefix was incomplete and is not shipped. The endpoint ignored a Range
  request. Its response advertised a conflicting audio rate, so actual packet
  headers, independent decoding, and native playback were used as evidence.
- Independently generated fixtures from `tools/make_fixture.py`, encoded by our
  Python/FFmpeg implementation, were passed into SDK 3.1.1's native player.
  See [RESULTS.md](RESULTS.md) for precisely what was validated.

Downloaded Linux SDK 3.1.2 archive SHA-256:
`7e3d912611e82f79007afd2a3a9e68a19c8bfc7025b5f761805f21368470d793`.
Public PeeDeeTV app ZIP SHA-256:
`39a8c074292387b04a5ebbedadcb23ba83c399010eb808ee4e6fc5a6a3dee823`.
These identify the investigated artifacts; they are not redistributed.

No usable public Linux encoder for this native stream was found in the surveyed
official SDK, forum encoder discussions, or public repositories. Older `.pdv`
encoders and custom Playdate video protocols do not answer this format question.
This limited search is not evidence that no encoder exists elsewhere.

## Packet boundaries

For the tested profile, the stream starts directly with packets. No global
header, trailer, final frame-count table, or complete file is required before
playback. Video and MP3 packets are interleaved in presentation order.

| Packet | Bytes | Meaning |
| --- | --- | --- |
| Video keyframe | `FF C1`, 16-bit big-endian length, payload | zlib-compressed full bitmap |
| Video delta | `FF C2`, 16-bit big-endian length, payload | zlib-compressed bitmap XOR previous reconstructed bitmap |
| Audio | Standard MPEG-1 Layer III header and remaining frame | Complete MP3 frame; no additional outer header |

Each inflated bitmap has exactly 12,000 bytes: 400×240 pixels, 50 bytes per row,
top to bottom. Bits run most significant first, with 1 for white and 0 for black.
The encoder emits a keyframe at least once per second, otherwise choosing a
delta only when its compressed payload is smaller than a keyframe's.

The audio profile is mono MPEG-1 Layer III, 44,100 samples/second, 64 kbit/s,
1,152 samples per frame. `FF FB 50 C4` starts an unpadded 208-byte frame;
`FF FB 52 C4` starts a padded 209-byte frame. Frame length follows the standard
MPEG-1 Layer III calculation, including its padding bit. The encoder disables
the MP3 bit reservoir, ID3 tags, and Xing headers. Encoder delay/padding remain;
there is no gapless-playback metadata in our profile.

To choose the next packet at video rate `fps`, emit video frame `v` before audio
frame `a` when `v * 44100 <= a * 1152 * fps`. There are no explicit video
timestamps in this narrow profile. A requested frame rate changes this ordering;
it is not written into a made-up container header. Native timing behavior outside
the tested profile remains unverified.

## Native API observations

In SDK 3.1.1 Lua, `playdate.graphics.videostream.new(source)` accepted a filename
string or an HTTP connection. A file handle was rejected. The stream provides
`update()`, `getVideoPlayer()`, `getFilePlayer()`, `getBufferedFrameCount()`, and
`getBytesRead()`. The video player draws to its selected context while the audio
fileplayer supplies playback progression. Call `stream:update()` repeatedly and
start the audio after an initial buffer has arrived.

The native video player's `getFrameRate()` returned zero for this stream, and
the audio player's `getOffset()` did not provide a useful progressing clock.
Neither is used as evidence of synchronization. Setting the audio volume to
zero changed buffer consumption and caused a local-file test to stall; tests
therefore use volume 1 and an isolated audio output sink.

Other packet types, metadata, seeking, transport reconnection, alternate audio
profiles, native buffer sizing in Lua, and compatibility across firmware versions
are unresolved. The Python inspector deliberately rejects input outside its
profile rather than guessing. The native decoder is supplied by the separately
installed SDK; our malformed-input tests exercise the Python parser, not a
security audit of the native decoder.

## Native PDI poster profile

The plugin emits opaque, uncompressed 96×144 images. The format was checked
against the [community PDI description](https://github.com/cranksters/playdate-reverse-engineering/blob/main/formats/pdi.md)
and independently validated byte for byte against SDK `pdc -u` output from an
original generated PNG. No SDK is used by the server's image encoder.

| Offset | Bytes | Value |
| --- | --- | --- |
| 0 | 12 | ASCII `Playdate IMG` |
| 12 | 4 | Global flags: 0 |
| 16 | 2 each | Width 96, height 144, stride 12 |
| 22 | 2 each | Left, right, top, bottom clipping: all 0 |
| 30 | 2 | Cell flags: 4 (opaque) |
| 32 | 1,728 | Top-to-bottom bitmap, MSB first, 1 = white |

Header numbers are little endian. Total file size is 1,760 bytes. The client
checks this exact header and size before passing the downloaded image to the
native decoder. The Simulator test checks all 13,824 decoded pixels and verifies
that a second load uses the on-device cache.

## PDS over HTTP in plugin mode

Native player tests with SDKs 3.1.1 and 3.1.2 failed to decode HTTP chunk framing.
The plugin therefore exposes numbered responses with `Content-Length`, carrying
whole PDS packets. This changes transport boundaries, not PDS bytes or the codec
profile. Client 0.4.0 negotiates eight-second segments capped at 2 MiB; legacy
clients retain one-second segments capped at 65,536 bytes. The last response
has `X-Pds-Final: 1`.
The native player and audio clock persist across segment requests. The client
uses native consumed-byte counts to decide when to request another segment,
closes the connection between responses (client 0.4.1), and defers reuse for
50 ms to avoid callback races. Progress is included in the next segment request.
The ordinary continuous `.pds` endpoint remains available for HTTP clients
that handle chunked encoding. See [PLUGIN.md](PLUGIN.md) for the API and tests.

## Distribution boundary

Only original source and generated diagnostic media belong in this project.
The Docker image contains Python, FFmpeg, and our encoder; it contains no SDK,
PeeDeeTV app, or reference program footage. SDK use remains subject to the terms
included with the separately installed SDK. Shipping a future commercial client
would require confirming the native API's support status and applicable terms.
