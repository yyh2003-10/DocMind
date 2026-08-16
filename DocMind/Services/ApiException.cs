using System;

namespace DocMind.Services;

public class ApiException : Exception
{
    public string Code { get; }
    public string? Detail { get; }

    public ApiException(string code, string message, string? detail = null, Exception? innerException = null)
        : base(message, innerException)
    {
        Code = code;
        Detail = detail;
    }

    public ApiException(Models.ApiError error)
        : this(error.Code, error.Message, error.Detail)
    {
    }
}
