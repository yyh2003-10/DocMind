namespace DocMind.Models;

using System.ComponentModel;
using System.Runtime.CompilerServices;

/// <summary>对应后端文档详情接口 chunks_preview 数组项：
/// {chunk_id, chunk_index, content, tokens, doc_type, page, heading, extra}。
/// 改为可变 class 以支持批注编辑(TextBox 双向绑定)。</summary>
public sealed class Chunk : INotifyPropertyChanged
{
    public int ChunkId { get; init; }
    public int ChunkIndex { get; init; }
    public string Content { get; init; } = string.Empty;
    public int Tokens { get; init; }
    public string? DocType { get; init; }
    public int? Page { get; init; }
    public string? Heading { get; init; }
    /// <summary>extra JSON 字典（来自后端 chunks_meta.extra 字段）。</summary>
    public Dictionary<string, object>? Extra { get; init; }

    /// <summary>从后端加载的批注原文（只读，用于判断是否有批注）。</summary>
    public string? SavedAnnotation => Extra?.TryGetValue("annotation", out var v) == true ? v?.ToString() : null;

    private string? _editingAnnotation;
    /// <summary>用户正在编辑的批注文本（双向绑定用）。首次访问时从 SavedAnnotation 初始化。</summary>
    public string? EditingAnnotation
    {
        get => _editingAnnotation ?? SavedAnnotation;
        set => SetField(ref _editingAnnotation, value);
    }

    /// <summary>是否有已保存的批注（控制删除按钮可见性）。</summary>
    public bool HasSavedAnnotation => !string.IsNullOrEmpty(SavedAnnotation);

    public event PropertyChangedEventHandler? PropertyChanged;

    /// <summary>外部修改 Extra 后触发,通知 UI 刷新 SavedAnnotation / HasSavedAnnotation。</summary>
    public void OnExternalChanged()
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(SavedAnnotation)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(HasSavedAnnotation)));
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(EditingAnnotation)));
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (!System.Collections.Generic.EqualityComparer<T>.Default.Equals(field, value))
        {
            field = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
            return true;
        }
        return false;
    }
}
