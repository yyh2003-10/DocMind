using System;

namespace DocMind.Services;

public class BackendConnectionException : Exception
{
    public BackendConnectionException(string message, Exception? innerException = null)
        : base(message, innerException)
    {
    }
}
