"""配置管理单元测试。"""

from __future__ import annotations

import os

from doc2mind.core.config import Settings, get_settings, set_settings


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.embed_model == "BAAI/bge-small-zh-v1.5"
        assert s.embed_dim == 512
        assert s.chunk_max_tokens == 1500
        assert s.chunk_min_chars == 50
        assert s.chunk_overlap_chars == 200
        assert s.search_top_k == 10
        assert s.rrf_k == 60
        assert s.server_port == 8765
        assert s.server_host == "127.0.0.1"

    def test_from_env(self) -> None:
        os.environ["DOC2MIND_EMBED_MODEL"] = "test-model"
        os.environ["DOC2MIND_SERVER_PORT"] = "9999"
        os.environ["DOC2MIND_CHUNK_MAX_TOKENS"] = "2000"

        try:
            s = Settings.from_env()
            assert s.embed_model == "test-model"
            assert s.server_port == 9999
            assert s.chunk_max_tokens == 2000
            # 未设环境变量的保持默认
            assert s.chunk_min_chars == 50
        finally:
            del os.environ["DOC2MIND_EMBED_MODEL"]
            del os.environ["DOC2MIND_SERVER_PORT"]
            del os.environ["DOC2MIND_CHUNK_MAX_TOKENS"]

    def test_from_env_invalid_int(self) -> None:
        """无效整型环境变量应被静默忽略。"""
        os.environ["DOC2MIND_SERVER_PORT"] = "not-a-number"
        try:
            s = Settings.from_env()
            assert s.server_port == 8765  # 保持默认
        finally:
            del os.environ["DOC2MIND_SERVER_PORT"]

    def test_ensure_dirs(self) -> None:
        import tempfile
        import pathlib

        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(db_path=pathlib.Path(tmp) / "doc2mind" / "test.db")
            s.ensure_dirs()
            assert pathlib.Path(tmp + "/doc2mind").is_dir()

    def test_get_settings_singleton(self) -> None:
        old = get_settings()
        assert old is get_settings()  # 同一实例

    def test_set_settings(self) -> None:
        s = Settings(server_port=1234)
        set_settings(s)
        assert get_settings().server_port == 1234
        # 恢复
        set_settings(Settings())
