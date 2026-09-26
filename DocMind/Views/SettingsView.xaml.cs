using System.Diagnostics;
using System.Windows.Controls;
using DocMind.Services;

namespace DocMind.Views
{
    public partial class SettingsView : UserControl
    {
        public SettingsView()
        {
            var loadTimer = Stopwatch.StartNew();
            InitializeComponent();
            DebugLog.Info($"SettingsView XAML 初始化完成: {loadTimer.ElapsedMilliseconds}ms", "Nav");
            Loaded += (_, _) => DebugLog.Debug("SettingsView 已进入可视树", "Nav");
        }
    }
}
