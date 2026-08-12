using DocMind.Models;
using DocMind.Services;
using DocMind.ViewModels;
using CommunityToolkit.Mvvm.Input;

namespace WpfApp1.Tests;

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
}