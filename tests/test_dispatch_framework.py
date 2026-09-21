"""派工框架完整性：README + ISSUE 文件齐套。"""

from __future__ import annotations

from pathlib import Path

DISPATCH = Path(r"E:\DocMindY-worktrees\agent-p0\docs\agents\dispatch")


def test_dispatch_readme_and_issues():
    readme = DISPATCH / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    for tid in [f"T{i}" for i in range(0, 18)]:
        assert tid in text, tid
    issues_dir = DISPATCH / "issues"
    files = sorted(issues_dir.glob("ISSUE-*.md"))
    assert len(files) >= 18, files
    for f in files:
        body = f.read_text(encoding="utf-8")
        assert "验收标准" in body, f.name
        assert "优先级" in body, f.name
