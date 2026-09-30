using System.Collections.Concurrent;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Controller.Session;
using MediaBrowser.Model.Session;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Playdate;

public sealed class PdsException(int status, string message) : Exception(message)
{
    public int Status { get; } = status;
}

public sealed class PlaybackJob(Guid userId, string owner, string jellyfinSession, Guid itemId,
    string sourceId, string source, int? audioIndex, double duration, double start, int segmentSeconds = 1)
{
    public string Id { get; } = Guid.NewGuid().ToString("N");
    public Guid UserId { get; } = userId;
    public string Owner { get; } = owner;
    public string JellyfinSession { get; } = jellyfinSession;
    public Guid ItemId { get; } = itemId;
    public string SourceId { get; } = sourceId;
    public string Source { get; } = source;
    public int? AudioIndex { get; } = audioIndex;
    public double Duration { get; } = duration;
    public double Start { get; } = start;
    public int SegmentSeconds { get; } = segmentSeconds;
    public int MaxSegmentBytes => SegmentSeconds == 1 ? 65536 : 2 * 1024 * 1024;
    public double Position { get; set; } = start;
    public CancellationTokenSource Cancel { get; } = new();
    public SemaphoreSlim ReportLock { get; } = new(1);
    public DateTime Created { get; } = DateTime.UtcNow;
    public DateTime LastSeen { get; set; } = DateTime.UtcNow;
    public string State { get; set; } = "waiting";
    public string Error { get; set; } = "";
    public string ProgressWarning { get; set; } = "";
    public bool ReportedStart { get; set; }
    public bool ReportedStop { get; set; }
    public PdsSegments? Segments { get; set; }
    public Task? Producer { get; set; }
    public SemaphoreSlim PartLock { get; } = new(1);
    public int NextPart { get; set; }
    public long Bytes;
    public object Public() => new { id = Id, path = $"/api/stream/{Id}.pds", fps = PdsEncoder.Fps,
        parts = $"/api/stream/{Id}/parts",
        segmentSeconds = SegmentSeconds, maxSegmentBytes = MaxSegmentBytes, progressInParts = true,
        start = Start, duration = Duration, state = State, bytes = Interlocked.Read(ref Bytes),
        error = Error, progressWarning = ProgressWarning };
}

public sealed class PlaybackService(IMediaEncoder encoder, ISessionManager sessions,
    ILogger<PlaybackService> logger) : BackgroundService
{
    private readonly ConcurrentDictionary<string, PlaybackJob> jobs = new();
    private readonly SemaphoreSlim encoderSlot = new(1);
    private readonly object gate = new();

    public static void ValidatePosition(double value, double maximum)
    {
        if (!double.IsFinite(value) || value < 0 || value > maximum)
            throw new PdsException(400, "Playback position is outside the video");
    }

    public PlaybackJob Add(PlaybackJob job)
    {
        lock (gate)
        {
            if (jobs.Values.Any(j => j.State is "waiting" or "streaming" && !j.Cancel.IsCancellationRequested))
                throw new PdsException(409, "Another video is playing; stop it before starting a new one");
            foreach (var old in jobs.Values.Where(j => (j.ReportedStop || !j.ReportedStart) &&
                j.State is not ("waiting" or "streaming")).OrderBy(j => j.Created).ToArray())
            {
                if (jobs.Count < 32) break;
                jobs.TryRemove(old.Id, out _);
            }
            if (jobs.Count >= 64) throw new PdsException(429, "Too many pending sessions; try again shortly");
            jobs[job.Id] = job;
            return job;
        }
    }

    public PlaybackJob Get(string id, Guid userId, string owner)
    {
        if (!jobs.TryGetValue(id, out var job) || job.UserId != userId || job.Owner != owner)
            throw new PdsException(404, "Playback session has expired");
        return job;
    }

    public async Task Report(PlaybackJob job, double position, bool stop, bool paused = false)
    {
        ValidatePosition(position, job.Duration);
        await job.ReportLock.WaitAsync();
        try
        {
            if (job.ReportedStop) return;
            job.Position = position;
            job.LastSeen = DateTime.UtcNow;
            if (stop)
            {
                job.Cancel.Cancel();
                job.Segments?.Discard();
                job.State = "stopped";
            }
            // Merely opening/encoding a stream must not mark the title watched.
            if (!job.ReportedStart && stop) { job.ReportedStop = true; return; }
            var ticks = (long)Math.Round(position * TimeSpan.TicksPerSecond);
            if (!job.ReportedStart)
            {
                await sessions.OnPlaybackStart(new PlaybackStartInfo {
                    ItemId = job.ItemId, SessionId = job.JellyfinSession, MediaSourceId = job.SourceId,
                    PlaySessionId = job.Id, PositionTicks = ticks, AudioStreamIndex = job.AudioIndex,
                    PlayMethod = PlayMethod.Transcode, CanSeek = true, VolumeLevel = 100 });
                job.ReportedStart = true;
            }
            if (stop)
            {
                await sessions.OnPlaybackStopped(new PlaybackStopInfo {
                    ItemId = job.ItemId, SessionId = job.JellyfinSession, MediaSourceId = job.SourceId,
                    PlaySessionId = job.Id, PositionTicks = ticks });
                job.ReportedStop = true;
            }
            else
            {
                await sessions.OnPlaybackProgress(new PlaybackProgressInfo {
                    ItemId = job.ItemId, SessionId = job.JellyfinSession, MediaSourceId = job.SourceId,
                    PlaySessionId = job.Id, PositionTicks = ticks, AudioStreamIndex = job.AudioIndex,
                    PlayMethod = PlayMethod.Transcode, CanSeek = true, IsPaused = paused, VolumeLevel = 100 });
            }
            job.ProgressWarning = "";
        }
        catch (Exception error)
        {
            job.ProgressWarning = "Jellyfin could not save playback progress";
            logger.LogWarning("Playdate progress report failed ({ErrorType})", error.GetType().Name);
        }
        finally { job.ReportLock.Release(); }
    }

    private async Task ProduceParts(PlaybackJob job, PdsSegments output)
    {
        try
        {
            await Stream(job, output, () => Task.CompletedTask, CancellationToken.None);
            if (job.State == "complete") await output.Finish(job.Cancel.Token);
            output.Complete();
        }
        catch (Exception error) { output.Complete(error); }
        finally { output.Dispose(); }
    }

    public async Task<PdsPart?> Part(PlaybackJob job, int part, CancellationToken request, double? position = null)
    {
        if (!await job.PartLock.WaitAsync(0, request)) throw new PdsException(409, "A segment request is already active");
        using var cancel = CancellationTokenSource.CreateLinkedTokenSource(request, job.Cancel.Token);
        try
        {
            if (part < 0 || part != job.NextPart || job.Cancel.IsCancellationRequested)
                throw new PdsException(409, "Request the next video segment or start a new session");
            // Progress belongs to a decoded frame, not to bytes encoded/downloaded.
            // Carrying it with the next segment avoids concurrent device requests.
            if (position.HasValue) await Report(job, position.Value, false);
            else job.LastSeen = DateTime.UtcNow;
            if (job.Segments is null)
            {
                if (job.State != "waiting") throw new PdsException(409, "This session already has a stream");
                job.Segments = new PdsSegments(PdsEncoder.Fps * job.SegmentSeconds, job.MaxSegmentBytes);
                job.Producer = ProduceParts(job, job.Segments);
            }
            while (await job.Segments.Reader.WaitToReadAsync(cancel.Token))
            {
                if (job.Segments.Reader.TryRead(out var bytes)) { job.NextPart++; return bytes; }
            }
            if (job.State == "error") throw new PdsException(422, job.Error);
            if (job.State != "complete") throw new PdsException(409, "Playback session was stopped");
            return null;
        }
        catch (OperationCanceledException)
        {
            job.Cancel.Cancel();
            throw new PdsException(409, "Playback session was stopped");
        }
        catch (System.Threading.Channels.ChannelClosedException)
        { throw new PdsException(422, "Video conversion could not finish"); }
        finally { job.PartLock.Release(); }
    }

    public async Task Stream(PlaybackJob job, Stream output, Func<Task> startResponse, CancellationToken request)
    {
        if (job.State != "waiting" || job.Cancel.IsCancellationRequested)
            throw new PdsException(409, "Start a new playback session to resume this video");
        using var cancel = CancellationTokenSource.CreateLinkedTokenSource(request, job.Cancel.Token);
        try
        {
            if (!await encoderSlot.WaitAsync(TimeSpan.FromSeconds(5), cancel.Token))
                throw new PdsException(409, "The previous conversion is still stopping; try again shortly");
        }
        catch (OperationCanceledException) when (!request.IsCancellationRequested)
        { throw new PdsException(409, "Playback session was stopped"); }
        try
        {
            lock (gate)
            {
                if (job.State != "waiting" || job.Cancel.IsCancellationRequested)
                    throw new PdsException(409, "Start a new playback session to resume this video");
                job.State = "streaming";
            }
            await startResponse();
            await PdsEncoder.Encode(encoder.EncoderPath, job.Source, job.AudioIndex, job.Start,
                job.Duration - job.Start, output, count => Interlocked.Add(ref job.Bytes, count), cancel.Token);
            if (!cancel.IsCancellationRequested) job.State = "complete";
        }
        catch (PdsException) { throw; }
        catch (OperationCanceledException)
        {
            if (job.State != "stopped" && job.State != "expired") job.State = "disconnected";
            job.Cancel.Cancel();
        }
        catch (Exception error)
        {
            job.State = "error";
            job.Error = "Video conversion failed; check this file plays in Jellyfin";
            job.Cancel.Cancel();
            logger.LogWarning("Playdate conversion failed ({ErrorType})", error.GetType().Name);
        }
        finally { encoderSlot.Release(); }
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        try
        {
            while (await timer.WaitForNextTickAsync(stoppingToken))
            {
                foreach (var job in jobs.Values)
                {
                    var age = DateTime.UtcNow - job.LastSeen;
                    if ((!job.ReportedStart && age.TotalSeconds > 60) ||
                        (job.ReportedStart && !job.ReportedStop && age.TotalSeconds > 45))
                    {
                        await Report(job, job.Position, stop: true);
                        job.State = "expired";
                    }
                    if (age.TotalHours > 1 && job.State is not ("waiting" or "streaming"))
                        jobs.TryRemove(job.Id, out _);
                }
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
        finally
        {
            foreach (var job in jobs.Values) job.Cancel.Cancel();
            await Task.WhenAll(jobs.Values.Where(j => j.Producer is not null).Select(j => j.Producer!));
            foreach (var job in jobs.Values.Where(j => j.ReportedStart && !j.ReportedStop))
                await Report(job, job.Position, stop: true);
        }
    }
}
