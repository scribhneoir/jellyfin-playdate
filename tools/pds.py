"""Experimental native PDS packet framing, inferred from a public reference.

This deliberately supports only the profile emitted by encode.py. It is not a
complete specification of every packet understood by Playdate firmware.
"""
import struct
import zlib

WIDTH, HEIGHT = 400, 240
FRAME_BYTES = WIDTH * HEIGHT // 8
SAMPLE_RATE = 44100
SAMPLES_PER_MP3_FRAME = 1152
BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


def read_exact(source, size, *, allow_eof=False):
    data = bytearray()
    while len(data) < size:
        part = source.read(size - len(data))
        if not part:
            if allow_eof and not data:
                return None
            raise ValueError(f"truncated packet: wanted {size} bytes, got {len(data)}")
        data.extend(part)
    return bytes(data)


def mp3_size(header):
    word = int.from_bytes(header, "big")
    version, layer = (word >> 19) & 3, (word >> 17) & 3
    bitrate, rate = (word >> 12) & 15, (word >> 10) & 3
    if word >> 21 != 0x7FF or version != 3 or layer != 1 or rate != 0:
        raise ValueError("expected MPEG-1 Layer III audio at 44100 Hz")
    if bitrate not in range(1, 15) or (word >> 6) & 3 != 3:
        raise ValueError("expected non-free-format mono MP3")
    return 144000 * BITRATES[bitrate] // SAMPLE_RATE + ((word >> 9) & 1)


def packets(source):
    while (header := read_exact(source, 4, allow_eof=True)) is not None:
        if header[:2] in (b"\xff\xc1", b"\xff\xc2"):
            size = int.from_bytes(header[2:], "big")
            if size == 0:
                raise ValueError("empty compressed video packet")
            yield header[1], read_exact(source, size)
        else:
            size = mp3_size(header)
            yield "audio", header + read_exact(source, size - 4)


def decode_video(kind, payload, previous):
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(payload, FRAME_BYTES + 1)
    except zlib.error as error:
        raise ValueError(f"invalid zlib video payload: {error}") from error
    if len(raw) != FRAME_BYTES or not decoder.eof or decoder.unused_data:
        raise ValueError("video packet must contain exactly one 400x240 bitmap")
    if kind == 0xC1:
        return raw
    if kind != 0xC2 or previous is None:
        raise ValueError("delta frame without preceding keyframe")
    return bytes(a ^ b for a, b in zip(raw, previous))


def encode_video(frame, previous, keyframe):
    if len(frame) != FRAME_BYTES:
        raise ValueError("frame must be a 400x240 packed bitmap")
    full = zlib.compress(frame)
    kind, data = 0xC1, full
    if previous is not None and not keyframe:
        delta = zlib.compress(bytes(a ^ b for a, b in zip(frame, previous)))
        if len(delta) < len(full):
            kind, data = 0xC2, delta
    return struct.pack(">BBH", 0xFF, kind, len(data)) + data
