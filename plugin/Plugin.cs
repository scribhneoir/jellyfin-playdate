using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;
using Microsoft.Extensions.DependencyInjection;

namespace Jellyfin.Plugin.Playdate;

public sealed class Plugin(IApplicationPaths paths, IXmlSerializer serializer)
    : BasePlugin<BasePluginConfiguration>(paths, serializer)
{
    public override string Name => "Playdate";
    public override string Description => "Stream your Jellyfin videos to Playdate in native PDS format.";
    public override Guid Id => Guid.Parse("9a601fa6-3a03-49d3-a64e-2be2c824c3ad");
}

public sealed class ServiceRegistrator : IPluginServiceRegistrator
{
    public void RegisterServices(IServiceCollection services, IServerApplicationHost applicationHost)
    {
        services.AddSingleton<PlaybackService>();
        services.AddSingleton<PdiImages>();
        services.AddHostedService(provider => provider.GetRequiredService<PlaybackService>());
    }
}
