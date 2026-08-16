using DocMind.Services;

namespace DocMind.Tests;

/// <summary>SecretProtector（DPAPI 加密）单元测试。</summary>
public class SecretProtectorTests
{
    [Fact]
    public void Protect_Then_Unprotect_RoundTrips()
    {
        var plain = "sk-test-12345";
        var cipher = SecretProtector.Protect(plain);

        Assert.NotEqual(plain, cipher);
        Assert.True(SecretProtector.IsProtected(cipher));
        Assert.Equal(plain, SecretProtector.Unprotect(cipher));
    }

    [Fact]
    public void Protect_IsIdempotent_OnCiphertext()
    {
        var cipher = SecretProtector.Protect("sk-abc");
        // 已加密的值再次 Protect 不二次加密
        Assert.Equal(cipher, SecretProtector.Protect(cipher));
    }

    [Fact]
    public void Protect_EmptyValue_StaysEmpty()
    {
        Assert.Null(SecretProtector.Protect(null));
        Assert.Equal("", SecretProtector.Protect(""));
    }

    [Fact]
    public void Unprotect_Plaintext_PassesThrough()
    {
        // 旧版明文兼容：原样返回，等待下次保存时迁移加密
        Assert.Equal("legacy-plain-key", SecretProtector.Unprotect("legacy-plain-key"));
        Assert.False(SecretProtector.IsProtected("legacy-plain-key"));
    }

    [Fact]
    public void Unprotect_EmptyOrNull_PassesThrough()
    {
        // null 保持 null（未配置语义），空串保持空串
        Assert.Null(SecretProtector.Unprotect(null));
        Assert.Equal("", SecretProtector.Unprotect(""));
    }

    [Fact]
    public void Unprotect_CorruptedCiphertext_ReturnsEmpty()
    {
        // 非法 base64 / 损坏密文：按未配置处理而非抛异常
        Assert.Equal("", SecretProtector.Unprotect("dpapi:v1:!!!not-base64!!!"));
    }

    [Fact]
    public void Ciphertext_DoesNotContainPlaintext()
    {
        var cipher = SecretProtector.Protect("sk-very-secret-value");
        Assert.DoesNotContain("sk-very-secret-value", cipher);
    }
}
