"""入口：`python -m doc2mind` 与 `doc2mind` 命令等价。"""

from __future__ import annotations

from doc2mind.cli import app


def main() -> None:
    """CLI 主入口。"""
    app()


if __name__ == "__main__":
    main()
