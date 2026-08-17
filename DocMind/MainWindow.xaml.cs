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

            // 标题栏支持拖拽与双击切换最大化
            TitleBarBorder.MouseDown += (s, e) =>
            {
                if (e.ChangedButton == MouseButton.Left)
                {
                    if (e.ClickCount == 2)
                    {
                        WindowState = WindowState == WindowState.Maximized
                            ? WindowState.Normal
                            : WindowState.Maximized;
                    }
                    else
                    {
                        DragMove();
                    }
                }
            };

            // 窗口状态变化时自适应圆角与边框，防止最大化时屏幕边缘裁剪
            StateChanged += (s, e) =>
            {
                if (WindowState == WindowState.Maximized)
                {
                    WindowBorder.CornerRadius = new CornerRadius(0);
                    WindowBorder.Margin = new Thickness(6);
                }
                else
                {
                    WindowBorder.CornerRadius = new CornerRadius(10);
                    WindowBorder.Margin = new Thickness(0);
                }
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