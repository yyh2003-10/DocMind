namespace DocMind;

public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
}
