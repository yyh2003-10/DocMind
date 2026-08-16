using System.Windows;
using System.Windows.Input;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind
{
    public partial class MainWindow : Window
    {
        public MainWindow(NotificationService notificationService)
        {
            InitializeComponent();

            // 订阅通知服务 → Toast 层显示
            notificationService.NotificationAdded += notification =>
            {
                Dispatcher.Invoke(() => ToastLayer.Show(notification));
            };

            // 拖动窗口
            BrandPanel.MouseDown += (s, e) =>
            {
                if (e.ChangedButton == MouseButton.Left)
                    DragMove();
            };
        }

        protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
        {
            // 关窗前统一取消进行中的后台任务（导入轮询、重建索引轮询），
            // 避免窗口关闭后孤儿任务继续占用资源。
            if (DataContext is MainViewModel vm)
            {
                vm.CancelInFlightOperations();
            }
            base.OnClosing(e);
        }

        private void MinimizeButton_Click(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState.Minimized;
        }

        private void MaximizeButton_Click(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState == WindowState.Maximized
                ? WindowState.Normal
                : WindowState.Maximized;
        }

        private void CloseButton_Click(object sender, RoutedEventArgs e)
        {
            Application.Current.Shutdown();
        }
    }
}