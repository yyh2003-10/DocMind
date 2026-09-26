using System.Net;
using System.Text;
using DocMind.Models;
using DocMind.Services;
using Microsoft.Extensions.Logging.Abstractions;

namespace DocMind.Tests;

public class Doc2kbApiServiceTests
{
    private class MockHttpMessageHandler : HttpMessageHandler
    {
        public HttpResponseMessage Response { get; set; } = new(HttpStatusCode.OK);

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(Response);
        }
    }

    [Fact]
    public async Task SendAsync_FastApiErrorFormat_ExtractsMessageAndCode()
    {
        var json = "{\"detail\":{\"code\":\"LLM_ERROR\",\"message\":\"知识卡片蒸馏失败: API Key 无效\",\"detail\":null}}";
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.InternalServerError)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("LLM_ERROR", ex.Code);
        Assert.Equal("知识卡片蒸馏失败: API Key 无效", ex.Message);
    }

    [Fact]
    public async Task SendAsync_FastApiStringDetail_ExtractsMessage()
    {
        var json = "{\"detail\":\"未找到指定实体\"}";
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.NotFound)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("NOTFOUND", ex.Code);
        Assert.Equal("未找到指定实体", ex.Message);
    }

    [Fact]
    public async Task SendAsync_ArrayRootBody_FallsBackWithoutThrowing()
    {
        // 根为 JSON 数组：此前 TryGetProperty 会抛 InvalidOperationException 逃出 catch，
        // 修复后应走通用状态码异常（回归测试）
        var json = "[\"detail\",\"error\",42]";
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.InternalServerError)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("INTERNALSERVERERROR", ex.Code);
        Assert.Contains("Request failed with status code 500", ex.Message);
    }

    [Fact]
    public async Task SendAsync_FastApi422ValidationArray_ExtractsMessages()
    {
        var json = "{\"detail\":[{\"loc\":[\"body\",\"query\"],\"msg\":\"field required\",\"type\":\"missing\"}]}";
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.UnprocessableEntity)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("VALIDATION_ERROR", ex.Code);
        Assert.Contains("field required", ex.Message);
    }

    [Fact]
    public async Task SendAsync_TopLevelErrorString_ExtractsMessage()
    {
        var json = "{\"error\":\"unauthorized\"}";
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.Unauthorized)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("UNAUTHORIZED", ex.Code);
        Assert.Equal("unauthorized", ex.Message);
    }

    [Fact]
    public async Task SendAsync_Plain500Html_FallsBackToStatusCodeMessage()
    {
        var handler = new MockHttpMessageHandler
        {
            Response = new HttpResponseMessage(HttpStatusCode.InternalServerError)
            {
                Content = new StringContent("<html><body>500 Internal Server Error</body></html>", Encoding.UTF8, "text/html")
            }
        };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        var service = new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.DistillEntityKnowledgeAsync(new EntityDistillRequest
            {
                EntityId = "e1",
                EntityName = "Test"
            }));

        Assert.Equal("INTERNALSERVERERROR", ex.Code);
        Assert.Contains("Request failed with status code 500", ex.Message);
    }
}

public class Doc2kbApiServiceSseParserTests
{
    private static HttpResponseMessage SseResponse(string sseBody)
    {
        var content = new StreamContent(new MemoryStream(Encoding.UTF8.GetBytes(sseBody)));
        content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("text/event-stream");
        return new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
    }

    private static (Doc2kbApiService Service, MockHandler Handler) CreateService(string sseBody)
    {
        var handler = new MockHandler { Response = SseResponse(sseBody) };
        var client = new HttpClient(handler) { BaseAddress = new Uri("http://127.0.0.1:8765/") };
        return (new Doc2kbApiService(client, NullLogger<Doc2kbApiService>.Instance), handler);
    }

    // ChatStreamAsync 需要可流式读取的 Response；MockHandler 与上面 MockHttpMessageHandler
    // 等价，这里独立声明避免改动既有测试类的嵌套类型可见性
    private class MockHandler : HttpMessageHandler
    {
        public HttpResponseMessage Response { get; set; } = new(HttpStatusCode.OK);

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(Response);
        }
    }

    private static ChatStreamResult Run(ChatRequest req, Doc2kbApiService service,
        List<string>? tokens = null, List<string>? thinking = null, List<string>? statuses = null)
        => service.ChatStreamAsync(
            req,
            t => tokens?.Add(t),
            _ => { },
            s => statuses?.Add(s),
            th => thinking?.Add(th)).GetAwaiter().GetResult();

    [Fact]
    public void ChatStream_ParsesDoneFrame_PartialWarningAndModelSpec()
    {
        var sse = string.Join("\n",
            "data: {\"type\":\"status\",\"message\":\"正在检索知识库...\"}",
            "",
            "data: {\"type\":\"thinking\",\"text\":\"推理链片段\"}",
            "",
            "data: {\"token\":\"部分\"}",
            "data: {\"token\":\"回答\"}",
            "",
            "data: {\"done\":true,\"chat_id\":\"chat-abc\",\"model\":\"m1\",\"provider\":\"openai\",\"model_spec\":{\"display_name\":\"DeepSeek Chat\",\"context_window\":65536,\"max_output_tokens\":8192,\"is_reasoning_model\":false,\"summary_text\":\"上下文 64K · 最大输出 8K\"},\"total_chunks\":2,\"elapsed_ms\":1234,\"partial\":true,\"warning\":\"回答因网络连接中断，当前内容仅供参考，请重新发送问题获取完整回答。\",\"sources\":[{\"index\":1,\"source\":\"a.pdf\",\"chunk_id\":7,\"format\":\"pdf\",\"page\":3,\"heading\":\"架构\",\"score\":0.9,\"source_type\":\"local\",\"snippet\":\"原文\"}]}",
            "data: [DONE]",
            "");
        var (service, _) = CreateService(sse);
        var tokens = new List<string>();
        var thinking = new List<string>();
        var statuses = new List<string>();

        var result = Run(new ChatRequest { Query = "q" }, service, tokens, thinking, statuses);

        Assert.Equal("chat-abc", result.ChatId);
        Assert.Equal("m1", result.Model);
        Assert.Equal("openai", result.Provider);
        Assert.Equal(2, result.TotalChunks);
        Assert.Equal(1234, result.ElapsedMs);
        Assert.True(result.Partial);
        Assert.Contains("网络连接中断", result.Warning);
        // model_spec 全字段解析（此前被 ParseDoneFrame 丢弃）
        Assert.Equal("DeepSeek Chat", result.ModelDisplayName);
        Assert.Equal(65536, result.ContextWindow);
        Assert.Equal(8192, result.MaxOutputTokens);
        Assert.False(result.IsReasoningModel);
        Assert.Equal("上下文 64K · 最大输出 8K", result.ModelSpecSummary);
        Assert.Single(result.Sources);
        Assert.Equal(7, result.Sources[0].ChunkId);
        Assert.Equal(3, result.Sources[0].Page);
        // 回调分发
        Assert.Equal(new[] { "部分", "回答" }, tokens);
        Assert.Equal(new[] { "推理链片段" }, thinking);
        Assert.Equal(new[] { "正在检索知识库..." }, statuses);
    }

    [Fact]
    public void ChatStream_SkipsHeartbeatComments_AndDoneWithoutModelSpec()
    {
        // 心跳注释帧/空行被跳过；done 帧缺省 model_spec 与 partial 时字段安全回退
        var sse = ": heartbeat\n\ndata: {\"token\":\"你好\"}\n\n" +
                  ": heartbeat\n\n" +
                  "data: {\"done\":true,\"chat_id\":\"c1\",\"model\":\"m\",\"provider\":\"p\",\"total_chunks\":0,\"elapsed_ms\":5,\"partial\":false,\"sources\":[]}\n\n" +
                  "data: [DONE]\n\n";
        var (service, _) = CreateService(sse);
        var tokens = new List<string>();

        var result = Run(new ChatRequest { Query = "q" }, service, tokens);

        Assert.Equal("你好", Assert.Single(tokens));
        Assert.False(result.Partial);
        Assert.Null(result.Warning);
        Assert.Null(result.ModelDisplayName);
        Assert.Null(result.ContextWindow);
        Assert.Null(result.IsReasoningModel);
        Assert.Empty(result.Sources);
    }

    [Fact]
    public async Task ChatStream_ErrorFrame_ThrowsRagError()
    {
        var sse = "data: {\"error\":\"LLM 配置错误: API Key 无效\"}\n\ndata: [DONE]\n\n";
        var (service, _) = CreateService(sse);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.ChatStreamAsync(new ChatRequest { Query = "q" }, _ => { }, _ => { }));

        Assert.Equal("RAG_ERROR", ex.Code);
        Assert.Contains("API Key 无效", ex.Message);
    }

    [Fact]
    public async Task ChatStream_MalformedFrame_ThrowsParseError()
    {
        var sse = "data: {broken json\n\n";
        var (service, _) = CreateService(sse);

        var ex = await Assert.ThrowsAsync<ApiException>(() =>
            service.ChatStreamAsync(new ChatRequest { Query = "q" }, _ => { }, _ => { }));

        Assert.Equal("PARSE_ERROR", ex.Code);
    }

    [Fact]
    public void ChatStream_MissingDoneFrame_ReturnsEmptyResult()
    {
        // 无终帧 → 空 ChatStreamResult（多轮 chat_id 丢失的下限保护）
        var sse = "data: {\"token\":\"片段\"}\n\ndata: [DONE]\n\n";
        var (service, _) = CreateService(sse);

        var result = Run(new ChatRequest { Query = "q" }, service);

        Assert.Equal(string.Empty, result.ChatId);
        Assert.Equal(0, result.TotalChunks);
        Assert.Empty(result.Sources);
    }

    [Fact]
    public void ChatStream_ToleratesDuplicatedDataPrefix()
    {
        // 回归防护：后端 done 帧曾拼出 "data: data: {...}" 双前缀，
        // 端上只剥一次前缀时 payload 仍以 'd' 开头，JsonDocument.Parse 直接抛
        // PARSE_ERROR，整轮回答判定失败（HTML 气泡随之空白）。
        var sse = "data: data: {\"token\":\"双前缀也认\"}\n\n"
                  + "data: data: {\"done\":true,\"chat_id\":\"c-dup\",\"model\":\"m\",\"provider\":\"p\","
                  + "\"total_chunks\":1,\"elapsed_ms\":9,\"partial\":false,\"sources\":[]}\n\n"
                  + "data: data: [DONE]\n\n";
        var (service, _) = CreateService(sse);
        var tokens = new List<string>();

        var result = Run(new ChatRequest { Query = "q" }, service, tokens);

        Assert.Equal("双前缀也认", Assert.Single(tokens));
        Assert.Equal("c-dup", result.ChatId);
        Assert.Equal(1, result.TotalChunks);
    }
}
