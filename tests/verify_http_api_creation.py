"""验证 HTTP API POST /v1/creative/export 远程端点真实使用性。"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from doc2mind.server.http import create_app


def test_real_http_export():
    app = create_app()
    client = TestClient(app)

    # 发起 PPTX 导出请求（主题：modern_purple）
    req_body = {
        "content": """
:::artifact type="pptx" title="AI 前沿创新汇报" theme="modern_purple"
---
# AI 智能创作平台
## 基于大模型的企业级交付
---
<!-- layout: metrics -->
# 性能核心突破
- 100% : 离线可用率
- 0MB : 显存依赖
:::
""",
        "format": "pptx",
        "theme": "modern_purple",
        "title": "AI 前沿创新汇报",
    }

    resp = client.post("/v1/creative/export", json=req_body)
    assert resp.status_code == 200, f"API 返回错误: {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    assert data["format"] == "pptx"
    assert data["file_size_bytes"] > 0
    assert len(data["file_path"]) > 0

    print("✓ HTTP API POST /v1/creative/export 端点调用成功！")
    print(f"  - 导出格式: {data['format']}")
    print(f"  - 文件名: {data['file_name']}")
    print(f"  - 真实物理路径: {data['file_path']}")
    print(f"  - 文件大小: {data['file_size_bytes']:,} bytes")

if __name__ == "__main__":
    test_real_http_export()
