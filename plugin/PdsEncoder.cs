using System.Buffers.Binary;
using System.Diagnostics;
using System.Globalization;
using System.IO.Compression;

namespace Jellyfin.Plugin.Playdate;

// Independent port of tools/pds.py and tools/encode.py; no Playdate SDK dependency.
public static class PdsEncoder
{
    public const int Fps = 15;
    public const int FrameBytes = 400 * 240 / 8;

    public static byte[] VideoPacket(byte[] frame, byte[]? previous, bool keyframe)
    {
        if (frame.Length != FrameBytes) throw new InvalidDataException("Invalid bitmap size");
        static byte[] Compress(byte[] data)
        {
            using var output = new MemoryStream();
            using (var zlib = new ZLibStream(output, CompressionLevel.Optimal, leaveOpen: true)) zlib.Write(data);
            return output.ToArray();
        }
        byte kind = 0xc1;
        var payload = Compress(frame);
        if (!keyframe && previous is not null)
        {
            var delta = new byte[FrameBytes];
            for (var i = 0; i < delta.Length; i++) delta[i] = (byte)(frame[i] ^ previous[i]);
            var compressed = Compress(delta);
            if (compressed.Length < payload.Length) { kind = 0xc2; payload = compressed; }
        }
        var packet = new byte[payload.Length + 4];
        packet[0] = 0xff;
        packet[1] = kind;
        BinaryPrimitives.WriteUInt16BigEndian(packet.AsSpan(2), checked((ushort)payload.Length));
        payload.CopyTo(packet, 4);
        return packet;
    }

    private static async Task<byte[]?> ReadPacket(Stream input, int size, bool eof, CancellationToken token)
    {
        var buffer = new byte[size];
        var read = 0;
        while (read < size)
        {
            var count = await input.ReadAsync(buffer.AsMemory(read), token);
            if (count == 0)
            {
                if (read == 0 && eof) return null;
                throw new InvalidDataException("Truncated encoder output");
            }
            read += count;
        }
        return buffer;
    }

    private static int AudioSize(byte[] header)
    {
        var word = BinaryPrimitives.ReadUInt32BigEndian(header);
        // This encoder's profile is MPEG-1 Layer III, 44.1 kHz, mono, 64 kbps.
        if (word >> 21 != 0x7ff || ((word >> 19) & 3) != 3 || ((word >> 17) & 3) != 1 ||
            ((word >> 12) & 15) != 5 || ((word >> 10) & 3) != 0 || ((word >> 6) & 3) != 3)
            throw new InvalidDataException("Unexpected audio profile");
        return 144000 * 64 / 44100 + (int)((word >> 9) & 1);
    }

    public static async Task Encode(string ffmpeg, string source, int? audioIndex, double start,
        double duration, Stream output, Action<int> countBytes, CancellationToken token)
    {
        var interval = duration.ToString("R", CultureInfo.InvariantCulture);
        // The bounded segment queue and response backpressure limit read-ahead.
        // Encoding ahead lets the device refill its buffer between HTTP requests.
        string[] input = ["-nostdin", "-v", "error", "-threads", "2", "-ss",
            start.ToString("R", CultureInfo.InvariantCulture), "-i", source];
        string[] video = [..input, "-map", "0:v:0", "-an", "-threads", "2", "-filter_threads", "1", "-vf",
            $"fps={Fps}:start_time=0,scale=400:240:force_original_aspect_ratio=decrease,pad=400:240:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=gray",
            "-sws_dither", "bayer", "-pix_fmt", "monob", "-t", interval, "-f", "rawvideo", "pipe:1"];
        string[] audioInput = audioIndex.HasValue ? input :
            ["-nostdin", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"];
        string[] audio = [..audioInput, "-map", audioIndex.HasValue ? $"0:{audioIndex.Value}" : "0:a:0",
            "-vn", "-threads", "2", "-ac", "1", "-ar", "44100", "-af", "aresample=async=1:first_pts=0,apad",
            "-c:a", "libmp3lame", "-b:a", "64k", "-reservoir", "0", "-write_xing", "0",
            "-id3v2_version", "0", "-write_id3v1", "0", "-t", interval, "-f", "mp3", "pipe:1"];
        var processes = new List<Process>();
        var drains = new List<Task>();
        try
        {
            foreach (var args in new[] { video, audio })
            {
                var info = new ProcessStartInfo(ffmpeg) { UseShellExecute = false,
                    RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
                foreach (var arg in args) info.ArgumentList.Add(arg);
                var process = Process.Start(info) ?? throw new IOException("Cannot start FFmpeg");
                processes.Add(process);
                // Drain without accumulating or logging media paths from FFmpeg errors.
                drains.Add(process.StandardError.BaseStream.CopyToAsync(Stream.Null));
            }
            byte[]? previous = null;
            long frames = 0, audioFrames = 0;
            var videoDone = false;
            var audioDone = false;
            while (!videoDone || !audioDone)
            {
                token.ThrowIfCancellationRequested();
                byte[] packet;
                if (!videoDone && (audioDone || frames * 44100 <= audioFrames * 1152 * Fps))
                {
                    var frame = await ReadPacket(processes[0].StandardOutput.BaseStream, FrameBytes, true, token);
                    if (frame is null) { videoDone = true; continue; }
                    packet = VideoPacket(frame, previous, frames % Fps == 0);
                    previous = frame;
                    frames++;
                }
                else
                {
                    var header = await ReadPacket(processes[1].StandardOutput.BaseStream, 4, true, token);
                    if (header is null) { audioDone = true; continue; }
                    packet = [..header, ..(await ReadPacket(processes[1].StandardOutput.BaseStream, AudioSize(header) - 4, false, token))!];
                    audioFrames++;
                }
                await output.WriteAsync(packet, token);
                await output.FlushAsync(token);
                countBytes(packet.Length);
            }
            foreach (var process in processes)
            {
                await process.WaitForExitAsync(token);
                if (process.ExitCode != 0) throw new IOException("FFmpeg conversion failed");
            }
            if (frames == 0 || audioFrames == 0) throw new InvalidDataException("Missing video or audio");
        }
        finally
        {
            foreach (var process in processes)
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }
                await process.WaitForExitAsync(CancellationToken.None);
                process.Dispose();
            }
            await Task.WhenAll(drains);
        }
    }
}
