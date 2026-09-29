using System.Buffers.Binary;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Entities;

namespace Jellyfin.Plugin.Playdate;

public sealed class PdiImages(IMediaEncoder encoder)
{
    public const int Width = 96, Height = 144;
    private readonly SemaphoreSlim slot = new(1);
    private readonly Dictionary<string, byte[]> cache = new();

    public static ItemImageInfo? Poster(BaseItem item) => item.GetImageInfo(ImageType.Primary, 0)
        ?? item.GetParents().Select(parent => parent.GetImageInfo(ImageType.Primary, 0)).FirstOrDefault(image => image is not null);

    public static string Tag(ItemImageInfo? image)
    {
        if (image is null || !image.IsLocalFile || !File.Exists(image.Path)) return "";
        var file = new FileInfo(image.Path);
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            $"pdi-v1:{file.FullName}:{file.Length}:{file.LastWriteTimeUtc.Ticks}")))[..24].ToLowerInvariant();
    }

    // Uncompressed opaque PDI, validated against pdc output from our own test PNGs.
    public static byte[] Pack(byte[] bitmap)
    {
        if (bitmap.Length != Width / 8 * Height) throw new InvalidDataException("Invalid poster bitmap size");
        var result = new byte[32 + bitmap.Length];
        Encoding.ASCII.GetBytes("Playdate IMG").CopyTo(result, 0);
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(16), Width);
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(18), Height);
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(20), Width / 8);
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(30), 4);
        bitmap.CopyTo(result, 32);
        return result;
    }

    public async Task<byte[]> Get(ItemImageInfo? image, CancellationToken request)
    {
        var tag = Tag(image);
        if (tag == "") throw new PdsException(404, "No poster is available");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(request);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        await slot.WaitAsync(timeout.Token);
        try
        {
            if (cache.TryGetValue(tag, out var found)) return found;
            var info = new ProcessStartInfo(encoder.EncoderPath) { UseShellExecute = false,
                RedirectStandardOutput = true, RedirectStandardError = true, CreateNoWindow = true };
            string[] args = ["-nostdin", "-v", "error", "-threads", "1", "-i", image!.Path,
                "-threads", "1", "-filter_threads", "1", "-vf",
                $"scale={Width}:{Height}:force_original_aspect_ratio=decrease,pad={Width}:{Height}:(ow-iw)/2:(oh-ih)/2:color=white,format=gray",
                "-sws_dither", "bayer", "-pix_fmt", "monob", "-frames:v", "1", "-f", "rawvideo", "pipe:1"];
            foreach (var arg in args) info.ArgumentList.Add(arg);
            using var process = Process.Start(info) ?? throw new PdsException(500, "Cannot prepare poster");
            var drain = process.StandardError.BaseStream.CopyToAsync(Stream.Null);
            try
            {
                var bitmap = new byte[Width / 8 * Height];
                await process.StandardOutput.BaseStream.ReadExactlyAsync(bitmap, timeout.Token);
                await process.WaitForExitAsync(timeout.Token);
                if (process.ExitCode != 0) throw new IOException("Image conversion failed");
                var data = Pack(bitmap);
                if (cache.Count >= 256) cache.Remove(cache.Keys.First());
                cache[tag] = data;
                return data;
            }
            finally
            {
                try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }
                await process.WaitForExitAsync(CancellationToken.None);
                await drain;
            }
        }
        catch (OperationCanceledException) when (!request.IsCancellationRequested)
        { throw new PdsException(504, "Poster conversion timed out"); }
        catch (IOException) { throw new PdsException(422, "Cannot decode this poster"); }
        finally { slot.Release(); }
    }
}
