# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

如果你发现安全漏洞，请**不要**公开提交 Issue，而是通过以下方式报告：

1. **GitHub Security Advisories**（推荐）：
   - 打开 [Security](https://github.com/yyh2003-10/DocMind/security) 页面
   - 点击 "Report a vulnerability"
2. **邮件**：将漏洞详情发送至项目维护者（见 [CONTRIBUTING.md](CONTRIBUTING.md)）

我们会在 48 小时内确认收到，并在 7 天内提供初步响应。

## Scope

本项目的安全相关范围：
- **本地文件读写**：解析 PDF/DOCX/XLSX/PPTX/HTML/Markdown/图片/代码时的路径遍历与文件读取权限
- **LLM 集成**：通过 OpenAI/Anthropic/Gemini/Ollama 进行 RAG 对话时的 API Key 存储（本地 DPAPI 加密）
- **MCP 工具接口**：作为 MCP Server 接入 AI 编辑器时的权限边界
- **网络请求**：内置 WebSearchService 联网检索时的请求来源

不属于安全范畴的问题请走正常 Issue 流程。
