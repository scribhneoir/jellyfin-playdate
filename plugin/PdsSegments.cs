using System.Threading.Channels;

namespace Jellyfin.Plugin.Playdate;

public sealed record PdsPart(byte[] Data, bool Final);

// Each response has a known length; concatenating responses gives the original
// PDS stream. Keep whole packets and bound both segment size and queued memory.
public sealed class PdsSegments : Stream
{
    private readonly int framesPerSegment;
    private readonly int maxBytes;
    private readonly Channel<PdsPart> queue = Channel.CreateBounded<PdsPart>(2);
    private readonly MemoryStream buffer = new();
    private int frames;
    private byte[]? pending;
    public ChannelReader<PdsPart> Reader => queue.Reader;

    public PdsSegments(int framesPerSegment = PdsEncoder.Fps, int maxBytes = 65536)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(framesPerSegment, 1);
        ArgumentOutOfRangeException.ThrowIfLessThan(maxBytes, 16384);
        this.framesPerSegment = framesPerSegment;
        this.maxBytes = maxBytes;
    }

    private async Task Emit(CancellationToken token)
    {
        if (buffer.Length == 0) return;
        var bytes = buffer.ToArray();
        buffer.SetLength(0);
        frames = 0;
        if (pending is not null) await queue.Writer.WriteAsync(new PdsPart(pending, false), token);
        pending = bytes;
    }

    public override async ValueTask WriteAsync(ReadOnlyMemory<byte> packet, CancellationToken token = default)
    {
        if (packet.Length > maxBytes) throw new IOException("PDS packet exceeds segment limit");
        if (buffer.Length + packet.Length > maxBytes) await Emit(token);
        buffer.Write(packet.Span);
        if (packet.Span[1] is 0xc1 or 0xc2 && ++frames == framesPerSegment) await Emit(token);
    }

    public async Task Finish(CancellationToken token)
    {
        await Emit(token);
        if (pending is not null) await queue.Writer.WriteAsync(new PdsPart(pending, true), token);
        pending = null;
    }
    public void Complete(Exception? error = null) => queue.Writer.TryComplete(error);
    public void Discard() { while (queue.Reader.TryRead(out _)) { } }
    public override Task FlushAsync(CancellationToken token) => Task.CompletedTask;
    public override void Flush() { }
    public override bool CanRead => false;
    public override bool CanSeek => false;
    public override bool CanWrite => true;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
    public override int Read(byte[] bytes, int offset, int count) => throw new NotSupportedException();
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] bytes, int offset, int count) => throw new NotSupportedException();
    protected override void Dispose(bool disposing) { if (disposing) { pending = null; buffer.Dispose(); } base.Dispose(disposing); }
}
