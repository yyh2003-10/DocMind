# DocMind 构建总览

> 生成日期：2026-07-28
> 当前进度：阶段 1（项目骨架）完成，转入阶段 2

---

## 分工

| 角色 | 负责 | 状态 |
|---|---|---|
| **AtomCode（本会话）** | Python 后端全部 + 文档契约 | 进行中 |
| **Visual Studio Copilot** | WPF 客户端（`tasks/wpf-*.md`） | 任务已发布，待执行 |

---

## Python 后端进度（HANDOVER §14）

| 阶段 | 内容 | 状态 |
|:---:|---|:---:|
| 1 | 项目骨架 + `pyproject.toml` + 包结构 | ✅ 完成 |
| 2 | `detect.py` + 4 核心加载器 (pdf/docx/md/html) | ⏳ 下一步 |
| 3 | 分块器 (semantic + table + code) | ⏸ |
| 4 | fastembed 嵌入引擎 | ⏸ |
| 5 | sqlite-vec 存储 + BM25 检索 | ⏸ |
| 6 | CLI 全部命令 + Converter | ⏸ |
| 7 | MCP Server | ⏸ |
| 8 | FastAPI 服务 (extras) | ⏸ |

---

## WPF 任务进度（HANDOVER §13）

| # | 任务 | 文件 | 依赖 | 状态 |
|:---:|---|---|---|:---:|
| 1 | 项目重构 + MVVM 骨架 + 主窗口布局 | [`wpf-01-mvvm-skeleton.md`](wpf-01-mvvm-skeleton.md) | 无 | ⏳ 待执行 |
| 2 | HttpClient 封装 + DI 注册 | [`wpf-02-httpclient.md`](wpf-02-httpclient.md) | #1, `docs/api.md` | ⏳ 待执行 |
| 3 | 搜索页面 | [`wpf-03-search-page.md`](wpf-03-search-page.md) | #1, #2 | ⏳ 待执行 |
| 4 | 导入页面 | [`wpf-04-import-page.md`](wpf-04-import-page.md) | #1, #2 | ⏳ 待执行 |
| 5 | 格式转换页面 | [`wpf-05-convert-page.md`](wpf-05-convert-page.md) | #1, #2 | ⏳ 待执行 |
| 6 | 质量看板 | [`wpf-06-quality-dashboard.md`](wpf-06-quality-dashboard.md) | #1, #2 | ⏳ 待执行 |
| 7 | 设置页面 | [`wpf-07-settings-page.md`](wpf-07-settings-page.md) | #1 | ⏳ 待执行 |
| 8 | 系统托盘 + 打包 | [`wpf-08-tray-packaging.md`](wpf-08-tray-packaging.md) | #1-7 | ⏳ 待执行 |

**Copilot 执行顺序建议：** #1 → #2 → (#3, #4, #5, #6, #7 可并行) → #8

---

## 契约文档

| 文件 | 内容 | 服务于 |
|---|---|---|
| [`docs/api.md`](../docs/api.md) | HTTP API 契约（v1，全部端点 + 数据模型） | WPF #2-#6, Python 阶段 8 |
| [`docs/mcp.md`](../docs/mcp.md) | MCP 工具接入文档 | Python 阶段 7, 用户接入 |

---

## 关键文件

```
E:\DocMind\
├── HANDOVER.md                    # 交接报告（架构决策来源）
├── README.md                      # 项目说明 ✅
├── LICENSE                        # Apache 2.0 ✅
├── pyproject.toml                 # Python 包配置 ✅
│
├── src/doc2mind/                  # Python 包
│   ├── __init__.py                # ✅
│   ├── __main__.py                # ✅
│   ├── cli.py                     # ✅ Typer CLI 骨架（命令逻辑待阶段 6 填充）
│   ├── core/                      # 待阶段 2-5 实现
│   │   ├── config.py
│   │   ├── loader/                # detect.py + 8 个 loader
│   │   ├── chunker/               # semantic + table + code
│   │   ├── embedder/              # fastembed + api
│   │   ├── store/                 # sqlite-vec
│   │   ├── retriever/             # BM25 + 向量 RRF
│   │   └── converter/             # 格式互转
│   └── server/
│       ├── http.py                # FastAPI（阶段 8）
│       └── mcp.py                 # MCP Server（阶段 7）
│
├── WpfApp1/                       # C# WPF 客户端（VS Copilot 负责）
│   ├── DocMind.csproj             # ✅ 已配 MVVM + DI 依赖
│   ├── App.xaml / MainWindow.xaml # VS 默认骨架，待 #1 重构
│   └── ...
│
├── tasks/                         # WPF Copilot 任务文件
│   ├── wpf-01-mvvm-skeleton.md
│   ├── wpf-02-httpclient.md
│   ├── wpf-03-search-page.md
│   ├── wpf-04-import-page.md
│   ├── wpf-05-convert-page.md
│   ├── wpf-06-quality-dashboard.md
│   ├── wpf-07-settings-page.md
│   └── wpf-08-tray-packaging.md
│
└── docs/
    ├── api.md                     # ✅ HTTP API 契约
    └── mcp.md                     # 待阶段 7 补充
```
