using DocMind.Services;

namespace DocMind.Tests;

/// <summary>
/// M0-3 崩溃自愈策略：退避序列、连续失败上限、维护模式豁免（纯策略单测）。
/// 进程级行为（kill python → ≤30s 自动恢复 → job 续接）由人工走查覆盖：
/// 1) 启动应用后任务管理器 kill python 进程；2) 观察状态灯 Offline→Starting→Online；
/// 3) debug.log 出现「自动重启 x/3」与「自动重启成功」；4) 对话页 Agent 任务进度非僵尸。
/// </summary>
public class BackendProcessServiceTests
{
    [Fact]
    public void AutoRestartDelay_FollowsBackoffSequence()
    {
        Assert.Equal(0, BackendProcessService.AutoRestartDelayMs(1));      // 第 1 次立即
        Assert.Equal(5_000, BackendProcessService.AutoRestartDelayMs(2));  // 第 2 次 5s 退避
        Assert.Equal(15_000, BackendProcessService.AutoRestartDelayMs(3)); // 第 3 次 15s 退避
        Assert.Equal(15_000, BackendProcessService.AutoRestartDelayMs(9)); // 上限封顶
    }

    [Theory]
    [InlineData(0, true)]
    [InlineData(1, true)]
    [InlineData(2, true)]
    [InlineData(3, false)]
    [InlineData(4, false)]
    public void ShouldAutoRestart_StopsAfterMaxConsecutiveFailures(int attempt, bool expected)
        => Assert.Equal(expected, BackendProcessService.ShouldAutoRestart(attempt, isMaintenance: false));

    [Fact]
    public void ShouldAutoRestart_NeverDuringMaintenance()
    {
        // 维护模式（插件安装）期间后端被主动停止，绝不能触发自动重启
        // 去和 pip 抢运行库文件锁
        for (var attempt = 0; attempt < 10; attempt++)
        {
            Assert.False(BackendProcessService.ShouldAutoRestart(attempt, isMaintenance: true));
        }
    }
}
