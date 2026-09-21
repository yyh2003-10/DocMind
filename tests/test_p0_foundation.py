"""P0 产品地基：分块对齐、导入健康、库状态、使用档案。"""

from pathlib import Path

from doc2mind.core.config import (
    USAGE_PROFILES,
    Settings,
    apply_usage_profile,
    profile_persona_hint,
)
from doc2mind.core.library_status import get_library_status
from doc2mind.core.pipeline import (
    IngestResult,
    IngestSummary,
    _aggregate_health,
    _chunk_health,
    _estimate_chunk_tokens,
)


class TestChunkEmbedAlign:
    def test_default_chunk_fits_embed_window(self):
        s = Settings()
        assert s.chunk_max_tokens <= s.embed_max_length
        assert s.chunk_max_chars <= s.chunk_max_tokens * 3

    def test_estimate_tokens_cjk(self):
        assert _estimate_chunk_tokens("你好世界") == 4
        assert _estimate_chunk_tokens("") == 0


class TestChunkHealth:
    def test_long_chunk_counted(self):
        s = Settings(embed_max_length=10, chunk_max_tokens=10)

        class _Ch:
            content = "一" * 50  # 50 tokens >> 10

        long_n, warnings, suggest = _chunk_health([_Ch()], s)
        assert long_n == 1
        assert any("超出嵌入窗口" in w for w in warnings)

    def test_suggest_query_from_first_line(self):
        s = Settings()

        class _Ch:
            content = "# 对接方案\n正文……"

        _, _, suggest = _chunk_health([_Ch()], s)
        assert suggest == "对接方案"

    def test_config_mismatch_warns(self):
        s = Settings(chunk_max_tokens=2000, embed_max_length=512)

        class _Ch:
            content = "短"

        _, warnings, _ = _chunk_health([_Ch()], s)
        assert any("chunk_max_tokens" in w for w in warnings)


class TestAggregateHealth:
    def test_sum_and_suggest(self):
        summary = IngestSummary(results=[
            IngestResult(
                source="a.md", collection="c", format="md", size_bytes=1,
                chunk_count=2, elapsed_ms=1, status="ingested",
                long_chunk_count=1, health_warnings=("警告A",),
                suggest_query="问A",
            ),
            IngestResult(
                source="b.md", collection="c", format="md", size_bytes=1,
                chunk_count=1, elapsed_ms=1, status="skipped",
            ),
        ])
        _aggregate_health(summary)
        assert summary.long_chunk_count == 1
        assert summary.health_warnings == ("警告A",)
        assert summary.suggest_query == "问A"


class TestUsageProfiles:
    def test_all_profiles_defined(self):
        for name in ("docs", "notes", "agent", "library"):
            assert name in USAGE_PROFILES

    def test_apply_notes_profile(self):
        s = Settings(usage_profile="docs", rag_top_k=10)
        out = apply_usage_profile(s, "notes")
        assert out.usage_profile == "notes"
        assert out.rag_top_k == 4
        assert out.rag_mode == "strict"
        # 原对象不被原地改
        assert s.rag_top_k == 10

    def test_unknown_profile_noop(self):
        s = Settings()
        out = apply_usage_profile(s, "nope")
        assert out is s

    def test_notes_and_agent_hints_disable_actions(self):
        assert "不要输出" in profile_persona_hint("notes") or "禁止" in profile_persona_hint("notes")
        assert "ACTIONS" in profile_persona_hint("notes") or "行动" in profile_persona_hint("notes")
        assert "未找到" in profile_persona_hint("agent") or "工具" in profile_persona_hint("agent")


class TestLibraryStatus:
    def _store(self, dim=512):
        class _S:
            embedding_dim = dim

            def list_documents(self, limit=10000):
                class _D:
                    source = "a.md"
                    chunk_count = 3
                    summary = None
                    title = None

                return [_D()]

        return _S()

    def test_ok_when_aligned(self):
        s = Settings(embed_dim=512, chunk_max_tokens=480, embed_max_length=512)
        st = get_library_status(self._store(512), s)
        assert st["status"] == "ok"
        assert st["aligned"] is True
        assert st["total_documents"] == 1

    def test_dim_mismatch_needs_reindex(self):
        s = Settings(embed_dim=768)
        st = get_library_status(self._store(512), s)
        assert st["status"] == "reindex_needed"
        assert any(i["code"] == "dim_mismatch" for i in st["issues"])

    def test_empty_library(self):
        class _S:
            embedding_dim = 512

            def list_documents(self, limit=10000):
                return []

        st = get_library_status(_S(), Settings())
        assert st["status"] == "empty"
