using System.Security.Cryptography;
using System.Text;

namespace DocMind.Services;

/// <summary>DPAPI 敏感字符串保护（Windows CurrentUser 范围）。
///
/// 密文格式 <c>dpapi:v1:Base64</c>，只能在加密时的同一 Windows 用户账户下解密，
/// 配置文件被拷贝到其他机器/用户时无法还原（解密失败按空值处理，用户重新输入）。
/// 兼容旧明文：输入无前缀时视为明文原样返回，下次保存自动迁移为密文。</summary>
public static class SecretProtector
{
    private const string Prefix = "dpapi:v1:";

    // 附加熵：与"同机器任意进程都能调 DPAPI"相比提高一道门槛（非安全边界）
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("DocMind.LlmApiKey.v1");

    /// <summary>加密明文；空值与已加密值原样返回（幂等）。</summary>
    public static string? Protect(string? plain)
    {
        if (string.IsNullOrEmpty(plain) || IsProtected(plain))
        {
            return plain;
        }
        var encrypted = ProtectedData.Protect(
            Encoding.UTF8.GetBytes(plain), Entropy, DataProtectionScope.CurrentUser);
        return Prefix + Convert.ToBase64String(encrypted);
    }

    /// <summary>解密；null 保持 null（与未配置语义一致），旧明文原样返回（迁移时机在保存），解密失败返回空串。</summary>
    public static string? Unprotect(string? value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return value;
        }
        if (!IsProtected(value))
        {
            return value;
        }
        try
        {
            var bytes = Convert.FromBase64String(value[Prefix.Length..]);
            return Encoding.UTF8.GetString(
                ProtectedData.Unprotect(bytes, Entropy, DataProtectionScope.CurrentUser));
        }
        catch (CryptographicException)
        {
            // 换 Windows 用户/文件损坏：无法还原，按未配置处理
            return string.Empty;
        }
        catch (FormatException)
        {
            return string.Empty;
        }
    }

    /// <summary>判断值是否已是 DPAPI 密文格式。</summary>
    public static bool IsProtected(string? value)
        => value?.StartsWith(Prefix, StringComparison.Ordinal) == true;
}
