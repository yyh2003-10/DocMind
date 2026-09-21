# ISSUE-06 稳定联网：搜索 API 插件化

| 字段 | 内容 |
|---|---|
| ID | T6 |
| 优先级 | **P1** |
| 角色 | 后端 |
| 依赖 | T3（引用门控更稳后再扩源） |
| 状态 | ready |

## 背景

内置多引擎抓取慢且易被门槛滤光；用户期望「像插件一样」稳定检索网页。

## 目标

新增可配置 **Search Provider 插件**（如 Tavily/博查/SerpAPI 类），与现有内置抓取并存或可切换。

## 非目标

- 不默认打开付费 API。  
- 不在本任务做 MCP Client（见 ISSUE-09）。

## 验收标准

- [ ] 配置：`search_provider = builtin | <api_name>` + api_key（key 不回显）  
- [ ] API 模式：返回结构化 title/url/snippet，进入现有 web 资料块与引用链路  
- [ ] 超时/失败降级：builtin 或明确报错，不拖死生成  
- [ ] 单测：mock HTTP；密钥脱敏  
- [ ] 文档：如何申请/配置  
- [ ] 商用：无 key 时不崩溃，提示去设置  

## 实现触点

- `core/search/**`、`config.py`、`server/http.py` /v1/config  
- 设置页字段（可与 T7 部分重叠）  

## 产出

- provider 接口说明 + 配置示例 + 测试结果  

