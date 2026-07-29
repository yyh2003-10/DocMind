using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace DocMind.ViewModels;

public class ViewModelBase : INotifyPropertyChanged
{
    private string _title = string.Empty;
    private bool _isActive;

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Title
    {
        get => _title;
        set => SetProperty(ref _title, value);
    }

    public bool IsActive
    {
        get => _isActive;
        set => SetProperty(ref _isActive, value);
    }

    protected bool SetProperty<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }
}
