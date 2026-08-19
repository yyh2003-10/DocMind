"""测试 RAG 对话中的附件文档与图片读取 (Tool Parsing & Ingestion)。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from doc2mind.core.config import Settings
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.rag import _parse_attachments, rag_answer, rag_answer_stream


class DummyLLM(LLMClient):
    """测试用虚拟 LLM。"""

    def __init__(self, reply: str = "这是关于附件的分析回答。"):
        self.reply = reply
        self.last_messages = []

    @property
    def provider(self) -> str:
        return "dummy"

    @property
    def model_name(self) -> str:
        return "dummy-model"

    def _do_chat(self, messages, temperature=None, max_tokens=None) -> str:
        self.last_messages = messages
        return self.reply

    def _do_stream_chat(self, messages, temperature=None, max_tokens=None):
        self.last_messages = messages
        yield "这是关于附件的"
        yield "分析回答。"


class TestRagAttachments(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_parse_attachments_text_and_markdown(self):
        """测试解析纯文本和 Markdown 附件。"""
        txt_file = self.tmp_path / "note.txt"
        txt_file.write_text("项目核心指标：Q1 活跃用户增长 30%，系统延迟降低 50ms。", encoding="utf-8")

        md_file = self.tmp_path / "arch.md"
        md_file.write_text("# 架构设计\n采用微服务与本地双引擎向量库设计。", encoding="utf-8")

        ctx, sources = _parse_attachments([str(txt_file), str(md_file)])
        self.assertIn("note.txt", ctx)
        self.assertIn("arch.md", ctx)
        self.assertIn("Q1 活跃用户增长 30%", ctx)
        self.assertIn("采用微服务与本地双引擎向量库设计", ctx)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0].source, "note.txt")
        self.assertEqual(sources[0].source_type, "attachment")
        self.assertEqual(sources[1].source, "arch.md")

    def test_rag_answer_with_attachments(self):
        """测试在没有知识库切片命中的情况下，通过附件成功进行问答。"""
        code_file = self.tmp_path / "main.py"
        code_file.write_text("def compute_revenue(price, count):\n    return price * count * 0.9\n", encoding="utf-8")

        llm = DummyLLM("compute_revenue 函数计算了打 9 折后的总收入。")
        settings = Settings(db_path=self.tmp_path / "test.db")

        ans = rag_answer(
            query="请解释 compute_revenue 函数的逻辑",
            settings=settings,
            llm_client=llm,
            attachments=[str(code_file)],
            store=MagicMock(),
            embedder=MagicMock(),
        )

        self.assertIn("compute_revenue", ans.answer)
        self.assertEqual(len(ans.sources), 1)
        self.assertEqual(ans.sources[0].source, "main.py")
        self.assertEqual(ans.sources[0].source_type, "attachment")
        user_msg = [m for m in llm.last_messages if m["role"] == "user"][-1]
        self.assertIn("def compute_revenue", user_msg["content"])

    def test_rag_answer_stream_with_attachments(self):
        """测试流式对话携带附件。"""
        doc_file = self.tmp_path / "report.md"
        doc_file.write_text("## 风险预警\n高并发下连接池可能耗尽。", encoding="utf-8")

        llm = DummyLLM()
        settings = Settings(db_path=self.tmp_path / "test.db")

        events = list(rag_answer_stream(
            query="有什么风险？",
            settings=settings,
            llm_client=llm,
            attachments=[str(doc_file)],
            store=MagicMock(),
            embedder=MagicMock(),
        ))

        tokens = []
        done_payload = None
        for ev in events:
            data = json.loads(ev)
            if "token" in data:
                tokens.append(data["token"])
            if data.get("done"):
                done_payload = data

        self.assertEqual("".join(tokens), "这是关于附件的分析回答。")
        self.assertIsNotNone(done_payload)
        self.assertEqual(len(done_payload["sources"]), 1)
        self.assertEqual(done_payload["sources"][0]["source"], "report.md")


if __name__ == "__main__":
    unittest.main()
