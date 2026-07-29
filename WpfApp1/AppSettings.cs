using System.IO;
using System.Text.Json;

namespace DocMind;

public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
    public string Theme { get; set; } = "Light";

    /// <summary>持久化当前设置到 appsettings.json。</summary>
    public void Save()
    {
        var path = System.IO.Path.Combine(
            AppContext.BaseDirectory, "appsettings.json");
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        });
        File.WriteAllText(path, json);
    }
}
