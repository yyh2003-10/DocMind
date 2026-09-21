"""FC-07：搜索空库 / 无命中 分流（后端 message + 前端文案规则）。"""

from __future__ import annotations

from pathlib import Path


def classify_empty_hint(message: str | None) -> tuple[bool, str]:
    """与 SearchViewModel 相同的分流规则。"""
    hint = (message or "").strip()
    if not hint:
        return False, "没有匹配的结果。\n建议：尝试更换关键词，或调低「最低相似度」；\n也可以到【导入】页确认文档已加入知识库。"
    is_empty_lib = (
        "知识库为空" in hint
        or "没有任何文档" in hint
        or ("集合" in hint and "文档" in hint)
    )
    if is_empty_lib:
        return True, hint + "\n\n下一步：点击「去导入」添加文件，完成后再回来搜索。"
    return False, hint + "\n\n建议：更换关键词，或调低「最低相似度」后重试。"


def test_backend_search_message_present():
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "知识库为空" in http
    assert "没有任何文档" in http


def test_frontend_consumes_message():
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\SearchViewModel.cs").read_text(encoding="utf-8")
    assert "_isLibraryEmpty" in vm
    assert "ShowGoImportAction" in vm
    assert "GoToImportRequested" in vm
    assert "知识库为空" in vm
    main = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\MainViewModel.cs").read_text(encoding="utf-8")
    assert "GoToImportRequested" in main
    xaml = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Views\SearchView.xaml").read_text(encoding="utf-8")
    assert "GoToImportCommand" in xaml
    assert "ShowGoImportAction" in xaml


def test_classify_rules():
    empty, text = classify_empty_hint("知识库为空：请先在【导入】页添加文档")
    assert empty and "去导入" in text
    empty, text = classify_empty_hint("集合「foo」中没有任何文档，请确认集合名是否正确")
    assert empty
    empty, text = classify_empty_hint(None)
    assert not empty and "没有匹配" in text
    empty, text = classify_empty_hint("重排降级提示")
    assert not empty and "更换关键词" in text
