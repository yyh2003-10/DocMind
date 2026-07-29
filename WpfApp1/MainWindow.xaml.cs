using System.Windows;
using DocMind.Services;

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
        }
    }
}
