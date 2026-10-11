using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using DocMind.Services;
using DocMind.ViewModels;

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

            // 数值参数校验状态同步到 VM：Validation.ErrorEvent 是冒泡路由事件，
            // 在根上统一接收所有子 Binding 的校验错误（红框出现/消失都会触发），
            // 用于禁用/恢复「保存」按钮并作为保存前最后防线。
            AddHandler(Validation.ErrorEvent, new RoutedEventHandler(OnValidationError));
        }

        private void OnValidationError(object sender, RoutedEventArgs e)
        {
            if (DataContext is not SettingsViewModel vm) return;

            // e.OriginalSource 是报错控件；Validation.GetHasError 反映该控件当前是否仍处于错误态
            if (e.OriginalSource is DependencyObject d)
            {
                var anyError = HasAnyValidationError(d);
                if (anyError)
                {
                    vm.HasValidationErrors = true;
                }
                else if (vm.HasValidationErrors)
                {
                    // 该控件已修复；只有当整页再无其它错误时才解除保存封锁
                    vm.HasValidationErrors = HasAnyValidationErrorInTree(this);
                }
            }
        }

        private static bool HasAnyValidationError(DependencyObject source)
        {
            return source is UIElement ui && Validation.GetHasError(ui);
        }

        private static bool HasAnyValidationErrorInTree(DependencyObject root)
        {
            // 迭代遍历，避免深层视觉树/模板展开时递归爆栈
            var stack = new Stack<DependencyObject>();
            stack.Push(root);
            while (stack.Count > 0)
            {
                var node = stack.Pop();
                if (node is UIElement ui && Validation.GetHasError(ui)) return true;
                var n = System.Windows.Media.VisualTreeHelper.GetChildrenCount(node);
                for (var i = 0; i < n; i++)
                {
                    stack.Push(System.Windows.Media.VisualTreeHelper.GetChild(node, i));
                }
            }
            return false;
        }
    }
}
