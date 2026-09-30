using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Jellyfin.Data;
using Jellyfin.Data.Enums;
using Jellyfin.Database.Implementations.Enums;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Dto;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Session;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.Library;
using MediaBrowser.Model.MediaInfo;
using MediaBrowser.Model.Querying;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Filters;
using UserEntity = Jellyfin.Database.Implementations.Entities.User;

namespace Jellyfin.Plugin.Playdate;

[ApiController]
[Authorize]
[Route("Playdate/api")]
[RequestSizeLimit(4096)]
public sealed class PlaydateController(IUserManager users, ILibraryManager library, IUserViewManager views,
    IDtoService dtos, IMediaSourceManager sources, ISessionManager sessions, PlaybackService playback,
    IServerApplicationHost host, PdiImages images) : Controller
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly DtoOptions Dto = new(false) { EnableImages = true, ImageTypeLimit = 1,
        ImageTypes = [ImageType.Primary], EnableUserData = true,
        Fields = [ItemFields.Overview] };
    private UserEntity CurrentUser = null!;
    private string Owner = "";
    // Claim names are Jellyfin 10.11's authenticated identity; never accept a user ID in requests.
    private string Claim(string name) => User.FindFirst("Jellyfin-" + name)?.Value ?? "";
    private ContentResult Reply(object value, int status = 200) => new() {
        Content = JsonSerializer.Serialize(value, JsonOptions), ContentType = "application/json", StatusCode = status };

    public override async Task OnActionExecutionAsync(ActionExecutingContext context, ActionExecutionDelegate next)
    {
        Response.Headers.CacheControl = "no-store";
        if (!Guid.TryParse(Claim("UserId"), out var userId) ||
            users.GetUserById(userId) is not { } user || user.HasPermission(PermissionKind.IsDisabled))
        {
            context.Result = Reply(new { error = "Sign in with a Jellyfin user account" }, 403);
            return;
        }
        CurrentUser = user;
        Owner = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Claim("Token"))));
        var result = await next();
        if (result.Exception is PdsException error && !Response.HasStarted)
        {
            result.ExceptionHandled = true;
            result.Result = Reply(new { error = error.Message }, error.Status);
        }
    }

    private BaseItem Item(Guid id) => library.GetItemById<BaseItem>(id, CurrentUser)
        ?? throw new PdsException(404, "Jellyfin item was not found");

    private static string Trim(string? value, int max) => (value ?? "")[..Math.Min(value?.Length ?? 0, max)];
    private object Brief(BaseItem item)
    {
        BaseItemDto dto = dtos.GetBaseItemDto(item, Dto, CurrentUser);
        var duration = (dto.RunTimeTicks ?? 0) / (double)TimeSpan.TicksPerSecond;
        return new { id = dto.Id.ToString("N"), name = Trim(dto.Name, 180), type = dto.Type.ToString(),
            folder = dto.IsFolder ?? false, duration,
            resume = Math.Min(duration, (dto.UserData?.PlaybackPositionTicks ?? 0) / (double)TimeSpan.TicksPerSecond),
            played = dto.UserData?.Played ?? false, year = dto.ProductionYear,
            series = Trim(dto.SeriesName, 120), episode = dto.IndexNumber, season = dto.ParentIndexNumber,
            overview = Trim(dto.Overview, 800), poster = PdiImages.Tag(PdiImages.Poster(item)) };
    }

    [HttpGet("status")]
    public IActionResult Status() => Reply(new { server = host.FriendlyName, version = host.ApplicationVersionString,
        user = CurrentUser.Username, backend = "plugin", pluginVersion = "0.4.0", fps = PdsEncoder.Fps });

    [HttpGet("libraries")]
    public IActionResult Libraries()
    {
        var folders = views.GetUserViews(new UserViewQuery { User = CurrentUser, IncludeExternalContent = false });
        var items = folders.Where(folder =>
        {
            var type = dtos.GetBaseItemDto(folder, Dto, CurrentUser).CollectionType;
            return type is not (CollectionType.music or CollectionType.books or CollectionType.photos or CollectionType.livetv);
        }).Select(Brief).ToArray();
        return Reply(new { items, total = items.Length, start = 0 });
    }

    [HttpGet("items")]
    public IActionResult Items([FromQuery] Guid? parent = null, [FromQuery] int start = 0,
        [FromQuery] string search = "", [FromQuery] string view = "")
    {
        if (start < 0 || start > 1_000_000 || search.Length > 120 || (view != "" && view != "resume"))
            throw new PdsException(400, "Invalid library query");
        var folder = parent.HasValue ? Item(parent.Value) as Folder : library.GetUserRootFolder();
        if (folder is null) throw new PdsException(400, "Select a library or folder");
        var recursive = search.Length > 0 || view == "resume";
        var query = new InternalItemsQuery(CurrentUser) { ParentId = parent ?? Guid.Empty,
            StartIndex = start, Limit = 20, Recursive = recursive, SearchTerm = search,
            IsResumable = view == "resume" ? true : null, IsVirtualItem = false,
            EnableTotalRecordCount = true, DtoOptions = Dto,
            IncludeItemTypes = recursive ? [BaseItemKind.Movie, BaseItemKind.Episode, BaseItemKind.Video] :
                [BaseItemKind.Movie, BaseItemKind.Episode, BaseItemKind.Video, BaseItemKind.Series,
                 BaseItemKind.Season, BaseItemKind.BoxSet, BaseItemKind.Folder, BaseItemKind.CollectionFolder],
            OrderBy = view == "resume" ? [(ItemSortBy.DatePlayed, SortOrder.Descending)] :
                [(ItemSortBy.ParentIndexNumber, SortOrder.Ascending), (ItemSortBy.IndexNumber, SortOrder.Ascending),
                 (ItemSortBy.SortName, SortOrder.Ascending)] };
        var result = folder.GetItems(query);
        return Reply(new { items = result.Items.Select(Brief).ToArray(), total = result.TotalRecordCount, start });
    }

    [HttpGet("items/{id:guid}")]
    public IActionResult Details(Guid id) => Reply(Brief(Item(id)));

    [HttpGet("items/{id:guid}/poster.pdi")]
    public async Task<IActionResult> Poster(Guid id)
    {
        var data = await images.Get(PdiImages.Poster(Item(id)), HttpContext.RequestAborted);
        return File(data, "application/octet-stream");
    }

    private void CheckPlayback()
    {
        if (!CurrentUser.HasPermission(PermissionKind.EnableMediaPlayback) ||
            !CurrentUser.HasPermission(PermissionKind.EnableVideoPlaybackTranscoding) ||
            !CurrentUser.HasPermission(PermissionKind.EnableAudioPlaybackTranscoding))
            throw new PdsException(403, "This account needs video playback and audio/video transcoding permission");
    }

    public sealed record PlayRequest(Guid Id, double Position = 0, int SegmentSeconds = 1);
    public sealed record ProgressRequest(double Position, bool Paused = false, bool Started = false);

    [HttpPost("play")]
    public async Task<IActionResult> Play([FromBody] PlayRequest request)
    {
        CheckPlayback();
        if (request.SegmentSeconds is not (1 or 8)) throw new PdsException(400, "Choose one-second or eight-second segments");
        var item = Item(request.Id);
        if (item is not Video || item.GetPlayAccess(CurrentUser) != PlayAccess.Full)
            throw new PdsException(400, "Select a playable video");
        var media = await sources.GetPlaybackMediaSources(item, CurrentUser, true, false, HttpContext.RequestAborted);
        var source = media.FirstOrDefault(s => s.Protocol == MediaProtocol.File && !s.RequiresOpening &&
            !s.IsInfiniteStream && s.VideoType == VideoType.VideoFile && s.RunTimeTicks > 0 &&
            s.MediaStreams.Any(stream => stream.Type == MediaStreamType.Video) && System.IO.File.Exists(s.Path));
        if (source is null) throw new PdsException(422, "This version supports finite video files stored on the Jellyfin server");
        var duration = source.RunTimeTicks!.Value / (double)TimeSpan.TicksPerSecond;
        PlaybackService.ValidatePosition(request.Position, Math.Max(0, duration - .1));
        var audio = source.MediaStreams.FirstOrDefault(s => s.Type == MediaStreamType.Audio && s.Index == source.DefaultAudioStreamIndex)
            ?? source.MediaStreams.FirstOrDefault(s => s.Type == MediaStreamType.Audio);
        var session = await sessions.GetSessionByAuthenticationToken(Claim("Token"), Claim("DeviceId"),
            HttpContext.Connection.RemoteIpAddress?.ToString() ?? "");
        var job = playback.Add(new PlaybackJob(CurrentUser.Id, Owner, session.Id, item.Id, source.Id,
            source.Path, audio?.Index, duration, request.Position, request.SegmentSeconds));
        return Reply(job.Public(), 201);
    }

    private PlaybackJob Job(string id) => playback.Get(id, CurrentUser.Id, Owner);

    [HttpGet("session/{id}")]
    public IActionResult Session(string id) => Reply(Job(id).Public());

    [HttpPost("session/{id}/progress")]
    public async Task<IActionResult> Progress(string id, [FromBody] ProgressRequest request)
    {
        var job = Job(id);
        await playback.Report(job, request.Position, false, request.Paused);
        return Reply(job.Public());
    }

    [HttpPost("session/{id}/stop")]
    public async Task<IActionResult> Stop(string id, [FromBody] ProgressRequest request)
    {
        var job = Job(id);
        // Short videos and an early pause may finish before the next segment GET.
        if (request.Started && !job.ReportedStart) await playback.Report(job, request.Position, false);
        await playback.Report(job, request.Position, true);
        return Reply(job.Public());
    }

    [HttpGet("stream/{id}.pds")]
    public async Task Stream(string id)
    {
        CheckPlayback();
        var job = Job(id);
        _ = Item(job.ItemId); // Recheck access if permissions changed after creating the session.
        await playback.Stream(job, Response.Body, async () =>
        {
            Response.ContentType = "application/octet-stream";
            Response.Headers["X-Accel-Buffering"] = "no";
            await Response.StartAsync(HttpContext.RequestAborted);
        }, HttpContext.RequestAborted);
    }

    [HttpGet("stream/{id}/parts/{part:int}")]
    public async Task<IActionResult> Part(string id, int part, [FromQuery] double? position = null)
    {
        CheckPlayback();
        var job = Job(id);
        _ = Item(job.ItemId);
        Response.Headers.CacheControl = "no-store, no-transform";
        var data = await playback.Part(job, part, HttpContext.RequestAborted, position);
        if (data is null) return NoContent();
        Response.Headers["X-Pds-Final"] = data.Final ? "1" : "0";
        if (job.ProgressWarning.Length > 0) Response.Headers["X-Pds-Progress-Warning"] = job.ProgressWarning;
        return File(data.Data, "application/octet-stream");
    }
}
