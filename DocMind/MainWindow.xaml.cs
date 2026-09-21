using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using DocMind.Services;
using DocMind.ViewModels;

namespace DocMind
{
    public partial class MainWindow : Window
    {
        public MainWindow(NotificationService notificationService)
        {
            InitializeComponent();

            // 订阅通知服务 → Toast 层显示（异步投递，避免工作线程被 UI 调度阻塞）
            notificationService.NotificationAdded += notification =>
            {
                Dispatcher.InvokeAsync(() => ToastLayer.Show(notification));
            };

            // 窗口状态变化时自适应圆角（最大化贴工作区边缘，圆角无意义）
            StateChanged += (s, e) =>
            {
                if (WindowState == WindowState.Maximized)
                {
                    WindowBorder.CornerRadius = new CornerRadius(0);
                    WindowBorder.Margin = new Thickness(0);
                }
                else
                {
                    WindowBorder.CornerRadius = new CornerRadius(10);
                    WindowBorder.Margin = new Thickness(0);
                }
            };
        }

        protected override void OnSourceInitialized(EventArgs e)
        {
            base.OnSourceInitialized(e);
            // WindowStyle=None 时默认最大化会铺满整屏（含任务栏），需挂钩修正边界
            HwndSource.FromHwnd(new WindowInteropHelper(this).Handle)
                ?.AddHook(WndProc);
        }

        private static IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
        {
            if (msg != NativeMethods.WM_GETMINMAXINFO)
            {
                return IntPtr.Zero;
            }

            var mmi = Marshal.PtrToStructure<NativeMethods.MINMAXINFO>(lParam);
            var monitor = NativeMethods.MonitorFromWindow(hwnd, NativeMethods.MONITOR_DEFAULTTONEAREST);
            if (monitor == IntPtr.Zero)
            {
                return IntPtr.Zero;
            }

            var mi = new NativeMethods.MONITORINFO
            {
                cbSize = Marshal.SizeOf<NativeMethods.MONITORINFO>()
            };
            if (!NativeMethods.GetMonitorInfo(monitor, ref mi))
            {
                return IntPtr.Zero;
            }

            // 最大化位置/尺寸对齐当前显示器工作区，避免被任务栏遮挡
            mmi.ptMaxPosition.x = Math.Abs(mi.rcWork.left - mi.rcMonitor.left);
            mmi.ptMaxPosition.y = Math.Abs(mi.rcWork.top - mi.rcMonitor.top);
            mmi.ptMaxSize.x = Math.Abs(mi.rcWork.right - mi.rcWork.left);
            mmi.ptMaxSize.y = Math.Abs(mi.rcWork.bottom - mi.rcWork.top);
            Marshal.StructureToPtr(mmi, lParam, true);
            handled = true;
            return IntPtr.Zero;
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