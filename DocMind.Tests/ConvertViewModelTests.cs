using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using CommunityToolkit.Mvvm.Input;

namespace DocMind.Tests;

/// <summary>
/// ConvertViewModel 基础状态：IsBusy 在转换前后正确翻转。
/// </summary>
public class ConvertViewModelTests
{
    private static ConvertViewModel CreateVm(FakeDoc2kbApiService fake)
        => new(fake, new NotificationService());

    [Fact]
    public async Task ConvertAsync_SetsIsBusyTrueDuringAndFalseAfter()
    {
        var fake = new FakeDoc2kbApiService();
        var busyChanges = new List<bool>();

        // 模拟转换耗时
        fake.OnConvert = async (_, _) =>
        {
            await Task.Delay(50);
            return new ConvertResult
            {
                Input = "test.md",
                OutputFormat = "html",
                Content = "<p>converted</p>",
            };
        };

        var vm = CreateVm(fake);
        vm.InputPath = @"C:\test.md";
        vm.PropertyChanged += (_, e) =>
        {
            if (e.PropertyName == nameof(ConvertViewModel.IsBusy))
                busyChanges.Add(vm.IsBusy);
        };

        await vm.ConvertCommand.ExecuteAsync(null);

        // IsBusy 应翻转 true → false
        Assert.Contains(true, busyChanges);
        Assert.False(vm.IsBusy);
        Assert.Contains("完成", vm.StatusMessage);
    }

    [Fact]
    public void ConvertCommand_Disabled_WhenBusyOrNoInput()
    {
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);

        Assert.False(vm.ConvertCommand.CanExecute(null));

        vm.InputPath = @"C:\test.md";
        Assert.True(vm.ConvertCommand.CanExecute(null));
    }

    [Fact]
    public void CanImportToKnowledgeBase_Notifies_WhenLastResultOrOutputPathChanges()
    {
        // 回归守卫（原 UX P0-D）：LastResult/OutputPath 变化时必须发出
        // CanImportToKnowledgeBase 通知，否则转换页「一键导入知识库」按钮
        // 的 Visibility 绑定永远停在初始 Collapsed，跨页链路 UI 断裂。
        var fake = new FakeDoc2kbApiService();
        var vm = CreateVm(fake);
        var notified = new List<string>();
        vm.PropertyChanged += (_, e) => notified.Add(e.PropertyName);

        var file = Path.Combine(Path.GetTempPath(), $"convert-import-{Guid.NewGuid():N}.md");
        File.WriteAllText(file, "converted");
        try
        {
            vm.OutputPath = file;
            vm.LastResult = new ConvertResult { Input = "test.md", OutputFormat = "md", Content = "<p>ok</p>" };

            Assert.Contains(nameof(ConvertViewModel.CanImportToKnowledgeBase), notified);
            Assert.True(vm.CanImportToKnowledgeBase);
            Assert.True(vm.ImportToKnowledgeBaseCommand.CanExecute(null));

            notified.Clear();
            vm.OutputPath = string.Empty;
            Assert.Contains(nameof(ConvertViewModel.CanImportToKnowledgeBase), notified);
            Assert.False(vm.CanImportToKnowledgeBase);
            Assert.False(vm.ImportToKnowledgeBaseCommand.CanExecute(null));
        }
        finally
        {
            File.Delete(file);
        }
    }
}