import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from pds import FRAME_BYTES, decode_video, encode_video, mp3_size, packets
from inspect_pds import inspect


class PacketTests(unittest.TestCase):
    def test_key_and_xor_frames_reconstruct_pixels(self):
        # Use nonuniform, poorly compressible pixels so a sparse XOR wins.
        import random
        rng = random.Random(1)
        first = rng.randbytes(FRAME_BYTES)
        second = bytes([first[0] ^ 128]) + first[1:]
        wire = encode_video(first, None, True) + encode_video(second, first, False)
        decoded, previous = [], None
        kinds = []
        for kind, body in packets(io.BytesIO(wire)):
            kinds.append(kind)
            previous = decode_video(kind, body, previous)
            decoded.append(previous)
        self.assertEqual(kinds, [0xC1, 0xC2])
        self.assertEqual(decoded, [first, second])

    def test_invalid_or_incomplete_input_is_rejected(self):
        frame = encode_video(bytes(FRAME_BYTES), None, True)
        for cut in (1, 3, len(frame) - 1):
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                list(packets(io.BytesIO(frame[:cut])))
        with self.assertRaises(ValueError):
            decode_video(0xC1, zlib.compress(bytes(FRAME_BYTES + 1)), None)
        with self.assertRaises(ValueError):
            decode_video(0xC2, zlib.compress(bytes(FRAME_BYTES)), None)
        with self.assertRaises(ValueError):
            decode_video(0xC1, b'not zlib', None)
        with self.assertRaises(ValueError):
            list(packets(io.BytesIO(b'ID3\0')))

    def test_mp3_frame_padding_and_profile(self):
        self.assertEqual(mp3_size(bytes.fromhex('fffb50c4')), 208)
        self.assertEqual(mp3_size(bytes.fromhex('fffb52c4')), 209)
        for header in ('fff350c4', 'fffb54c4', 'fffb5000'):
            with self.subTest(header=header), self.assertRaises(ValueError):
                mp3_size(bytes.fromhex(header))


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for media integration')
class EncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = pathlib.Path(cls.tmp.name) / 'test.mkv'
        subprocess.run([sys.executable, str(ROOT / 'tools/make_fixture.py'),
                        str(cls.source), '--seconds', '3'], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_actual_ffmpeg_conversion_and_audio_decoding(self):
        dest = pathlib.Path(self.tmp.name) / 'test.pds'
        subprocess.run([sys.executable, str(ROOT / 'tools/encode.py'),
                        str(self.source), str(dest)], check=True, capture_output=True)
        with dest.open('rb') as f:
            summary = inspect(f)
        self.assertEqual(summary['video_frames'], 45)
        self.assertAlmostEqual(summary['audio_seconds'], 3, delta=0.1)
        audio = b''.join(data for kind, data in packets(io.BytesIO(dest.read_bytes())) if kind == 'audio')
        result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'mp3', '-i', 'pipe:0',
                                 '-f', 's16le', '-ac', '1', '-ar', '44100', 'pipe:1'],
                                input=audio, capture_output=True, check=True)
        self.assertGreater(len(result.stdout), 3 * 44100 * 2)
        self.assertNotEqual(set(result.stdout), {0})

    def test_first_packet_arrives_before_conversion_finishes(self):
        with subprocess.Popen([sys.executable, str(ROOT / 'tools/encode.py'),
                               str(self.source), '-', '--realtime'], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as process:
            try:
                import selectors
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=8), 'encoder produced no data')
                self.assertEqual(process.stdout.read(2), b'\xff\xc1')
                self.assertIsNone(process.poll(), 'conversion already finished before first packet')
                process.stdout.close() # exercise broken-pipe cleanup of FFmpeg children
                process.wait(timeout=8)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_failed_conversion_preserves_existing_output(self):
        broken = pathlib.Path(self.tmp.name) / 'broken.mkv'
        broken.write_bytes(b'not a movie')
        dest = pathlib.Path(self.tmp.name) / 'existing.pds'
        dest.write_bytes(b'keep me')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/encode.py'), str(broken), str(dest)],
                                capture_output=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(dest.read_bytes(), b'keep me')
        self.assertFalse(list(pathlib.Path(self.tmp.name).glob('.pds-*')))

    def test_silent_source_gets_a_finite_native_audio_clock(self):
        from encode import encode
        output = io.BytesIO()
        encode(self.source, output, duration=2, has_audio=False)
        summary = inspect(io.BytesIO(output.getvalue()))
        self.assertEqual(summary['video_frames'], 30)
        self.assertAlmostEqual(summary['audio_seconds'], 2, delta=.1)
        audio = b''.join(data for kind, data in packets(io.BytesIO(output.getvalue())) if kind == 'audio')
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'mp3', '-i', 'pipe:0',
            '-f', 's16le', 'pipe:1'], input=audio, capture_output=True, check=True).stdout
        self.assertTrue(pcm)
        self.assertEqual(set(pcm), {0})


if __name__ == '__main__':
    unittest.main()
