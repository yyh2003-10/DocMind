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

    def test_llm_defaults(self) -> None:
        """LLM/RAG 对话配置默认值：未配置 LLM、RAG 阈值关闭。"""
        s = Settings()
        assert s.llm_provider == "none"
        assert s.llm_api_key is None
        assert s.llm_base_url is None
        assert s.llm_model == ""
        assert s.llm_temperature == 0.7
        assert s.llm_max_tokens == 2048
        assert s.rag_top_k == 5
        assert s.rag_min_score == 0.0

    def test_llm_from_env(self) -> None:
        """DOC2MIND_LLM_* 环境变量覆盖 LLM 配置。"""
        env = {
            "DOC2MIND_LLM_PROVIDER": "openai",
            "DOC2MIND_LLM_API_KEY": "sk-env-test",
            "DOC2MIND_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "DOC2MIND_LLM_MODEL": "deepseek-chat",
            "DOC2MIND_LLM_TEMPERATURE": "0.2",
            "DOC2MIND_LLM_MAX_TOKENS": "512",
            "DOC2MIND_RAG_TOP_K": "7",
            "DOC2MIND_RAG_MIN_SCORE": "0.3",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            s = Settings.from_env()
            assert s.llm_provider == "openai"
            assert s.llm_api_key == "sk-env-test"
            assert s.llm_base_url == "https://api.deepseek.com/v1"
            assert s.llm_model == "deepseek-chat"
            assert s.llm_temperature == 0.2
            assert s.llm_max_tokens == 512
            assert s.rag_top_k == 7
            assert s.rag_min_score == 0.3
        finally:
            for k in env:
                del os.environ[k]

    def test_llm_from_env_invalid_values_fall_back(self) -> None:
        """非法 float/int 环境变量被静默忽略，保持默认。"""
        env = {
            "DOC2MIND_LLM_TEMPERATURE": "not-a-float",
            "DOC2MIND_LLM_MAX_TOKENS": "abc",
            "DOC2MIND_RAG_TOP_K": "xyz",
        }
        for k, v in env.items():
            os.environ[k] = v
        try:
            s = Settings.from_env()
            assert s.llm_temperature == 0.7
            assert s.llm_max_tokens == 2048
            assert s.rag_top_k == 5
        finally:
            for k in env:
                del os.environ[k]


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
        import pathlib
        import tempfile

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
