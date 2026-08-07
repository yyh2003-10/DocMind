using System.Text.Json;
using System.Text.Json.Serialization;

namespace DocMind.Services;

/// <summary>snake_case 命名策略：序列化时把 CamelCase 字段名转为 snake_case，
/// 与后端 pydantic DTO 字段对齐。反序列化时由 JsonSerializerOptions 的
/// PropertyNameCaseInsensitive=true 兜底，不依赖本策略。</summary>
internal sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
{
    /// <summary>把 CamelCase 转 snake_case：InsertPath → input_path，TopK → top_k。</summary>
    public override string ConvertName(string name)
    {
        if (string.IsNullOrEmpty(name))
        {
            return name;
        }

        var sb = new System.Text.StringBuilder(name.Length + 4);
        for (var i = 0; i < name.Length; i++)
        {
            var c = name[i];
            if (i > 0 && char.IsUpper(c))
            {
                sb.Append('_');
            }
            sb.Append(char.ToLowerInvariant(c));
        }
        return sb.ToString();
    }
}
