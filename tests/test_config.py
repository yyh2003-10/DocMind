"""配置管理单元测试。"""

from __future__ import annotations

import os

import pytest

import doc2mind.core.config as _config_mod
from doc2mind.core.config import Settings, get_settings, set_settings

# 捕获真实实现：下方 autouse fixture 会把 load_config_file 替换为 lambda，
# 测试「旧 config.toml 键映射」时需要调用原始实现而非 stub
_ORIGINAL_LOAD_CONFIG = _config_mod.load_config_file


@pytest.fixture(autouse=True)
def _no_user_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离本机用户 config.toml：from_env 的「默认值」断言不被真实配置污染。"""
    monkeypatch.setattr("doc2mind.core.config.load_config_file", lambda: {})


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
        assert s.llm_max_tokens == 8192
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
            assert s.llm_max_tokens == 8192
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

    def test_from_env_embed_dim_aligned_with_catalog(self) -> None:
        """catalog 已收录模型：embed_dim 自动对齐真实维度，无需加载模型探测。"""
        os.environ["DOC2MIND_EMBED_MODEL"] = "jinaai/jina-embeddings-v2-base-zh"
        try:
            s = Settings.from_env()
            assert s.embed_dim == 768
        finally:
            del os.environ["DOC2MIND_EMBED_MODEL"]

    def test_from_env_explicit_embed_dim_wins(self) -> None:
        """显式配置的 embed_dim（toml/环境变量）优先，catalog 不覆盖。"""
        os.environ["DOC2MIND_EMBED_MODEL"] = "jinaai/jina-embeddings-v2-base-zh"
        os.environ["DOC2MIND_EMBED_DIM"] = "999"
        try:
            s = Settings.from_env()
            assert s.embed_dim == 999
        finally:
            del os.environ["DOC2MIND_EMBED_MODEL"]
            del os.environ["DOC2MIND_EMBED_DIM"]

    def test_from_env_custom_model_keeps_preset_dim(self) -> None:
        """catalog 未收录的自定义模型：保持预设维度（由 reindex probe 兜底）。"""
        os.environ["DOC2MIND_EMBED_MODEL"] = "my-org/custom-embed-model"
        try:
            s = Settings.from_env()
            assert s.embed_dim == 512
        finally:
            del os.environ["DOC2MIND_EMBED_MODEL"]

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

    def test_history_messages_default(self) -> None:
        """AUD-011：字段更名后默认值与语义（消息条数而非轮数）。"""
        s = Settings()
        assert s.rag_max_history_messages == 20
        assert not hasattr(s, "rag_max_history_turns")

    def test_legacy_history_turns_env_mapped(self) -> None:
        """旧环境变量 DOC2MIND_RAG_MAX_HISTORY_TURNS 兼容读取（AUD-011）。"""
        os.environ["DOC2MIND_RAG_MAX_HISTORY_TURNS"] = "6"
        try:
            s = Settings.from_env()
            assert s.rag_max_history_messages == 6
        finally:
            del os.environ["DOC2MIND_RAG_MAX_HISTORY_TURNS"]

    def test_new_history_messages_env_wins_over_legacy(self) -> None:
        """新旧环境变量同时存在时新名优先（AUD-011）。"""
        os.environ["DOC2MIND_RAG_MAX_HISTORY_TURNS"] = "6"
        os.environ["DOC2MIND_RAG_MAX_HISTORY_MESSAGES"] = "8"
        try:
            s = Settings.from_env()
            assert s.rag_max_history_messages == 8
        finally:
            del os.environ["DOC2MIND_RAG_MAX_HISTORY_TURNS"]
            del os.environ["DOC2MIND_RAG_MAX_HISTORY_MESSAGES"]

    def test_legacy_history_turns_toml_key_mapped(self, tmp_path, monkeypatch) -> None:
        """旧 config.toml 键 rag_max_history_turns → rag_max_history_messages（AUD-011）。"""
        import pathlib

        cfg = pathlib.Path(tmp_path) / "config.toml"
        cfg.write_text('[doc2mind]\nrag_max_history_turns = 5\n', encoding="utf-8")
        monkeypatch.setattr("doc2mind.core.config.config_file_path", lambda: cfg)
        # 恢复真实的 load_config_file（autouse fixture 的 stub 在此被覆盖）
        monkeypatch.setattr(_config_mod, "load_config_file", _ORIGINAL_LOAD_CONFIG)
        loaded = _config_mod.load_config_file()
        assert loaded["rag_max_history_messages"] == 5

    def test_watch_paths_from_env(self) -> None:
        os.environ["DOC2MIND_WATCH_PATHS"] = "C:/docs, D:/notes/kb , /var/data"
        os.environ["DOC2MIND_WATCH_DEBOUNCE_SECONDS"] = "3.5"
        try:
            s = Settings.from_env()
            assert s.watch_paths == ["C:/docs", "D:/notes/kb", "/var/data"]
            assert s.watch_debounce_seconds == 3.5
        finally:
            del os.environ["DOC2MIND_WATCH_PATHS"]
            del os.environ["DOC2MIND_WATCH_DEBOUNCE_SECONDS"]

