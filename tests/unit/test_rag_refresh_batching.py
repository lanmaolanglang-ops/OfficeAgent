"""P2-12：RAG 刷新任务分批处理 + 词法分数地板统一配置回归。

修复前：
- ``refresh_knowledge_base`` 一次性物化全库记录并单次 embed 全部内容
  （任务层无界），大知识库下内存与单次请求规模都不可控；
- 词法命中分数地板以 ``0.35`` 字面量散落 5 处，无统一配置源。

Embedding 请求层的分批职责归 P1-18 的 APIEmbedder（batch_size），本层
不重复实现；本文件锁定的是**任务层**的分批与阈值收敛。
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import office_agent.database.repository as repository_module
import office_agent.database.session as session_module
import office_agent.knowledge_base.embeddings as embeddings_module
from office_agent.task_queue.tasks.rag_tasks import (
    DEFAULT_LEXICAL_SCORE_FLOOR,
    refresh_knowledge_base,
    search_knowledge,
)


def _make_items(count: int, dimension: int = 3):
    return [
        SimpleNamespace(
            id=f"k{i:04d}", content=f"content-{i}", embedding=None,
            embedding_model=None, metadata_json=None,
        )
        for i in range(count)
    ]


class _FakeEmbedder:
    def __init__(self, calls, fail_on_call: int | None = None, dimension=3):
        self.model_id = "semantic-test"
        self.version = "v1"
        self.dimension = dimension
        self._calls = calls
        self._fail_on_call = fail_on_call
        self._call_index = 0

    def embed(self, texts):
        self._call_index += 1
        self._calls.append(list(texts))
        if self._fail_on_call is not None and self._call_index >= self._fail_on_call:
            raise RuntimeError("embedding provider exploded")
        return [[0.1] * self.dimension for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * (self.dimension - 1)


class _FakeKnowledgeRepo:
    """基于共享 backing 列表的 find(offset, limit, order_by) 分页桩。"""

    store: list = []

    def __init__(self, session):
        pass

    def find(self, offset=0, limit=100, order_by=None, descending=False,
             **filters):
        assert order_by == "id", "刷新分页必须按 id 稳定序"
        ordered = sorted(_FakeKnowledgeRepo.store, key=lambda item: item.id)
        return ordered[offset:offset + limit]

    def get_all(self, offset=0, limit=100):
        ordered = sorted(_FakeKnowledgeRepo.store, key=lambda item: item.id)
        return ordered[offset:offset + limit]


@pytest.fixture
def rag_env(monkeypatch):
    """替身注入：session_scope / KnowledgeRepository / embedder 工厂。"""
    store = _make_items(0)
    calls = []
    monkeypatch.setattr(_FakeKnowledgeRepo, "store", store, raising=False)

    @contextmanager
    def fake_session_scope():
        yield SimpleNamespace()

    monkeypatch.setattr(session_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(repository_module, "KnowledgeRepository",
                        _FakeKnowledgeRepo)
    return store, calls


def _install_embedder(monkeypatch, calls, **kwargs):
    embedder = _FakeEmbedder(calls, **kwargs)
    monkeypatch.setattr(embeddings_module, "create_semantic_embedder",
                        lambda: embedder)
    return embedder


class TestRefreshBatching:
    def test_multi_batch_full_coverage_in_order(self, rag_env, monkeypatch):
        store, calls = rag_env
        store.extend(_make_items(25))
        monkeypatch.setenv("RAG_REFRESH_BATCH_SIZE", "10")
        _install_embedder(monkeypatch, calls)

        result = refresh_knowledge_base()

        assert result["status"] == "success"
        assert result["total"] == 25 and result["refreshed"] == 25
        assert [len(batch) for batch in calls] == [10, 10, 5], \
            "任务层必须按 batch size 分批调用 embed"
        for batch in calls:
            assert batch == sorted(batch, key=lambda t: t)
        for index, item in enumerate(store):
            assert item.embedding_model == "semantic-test"
            assert json.loads(item.embedding) == [0.1, 0.1, 0.1]
            assert json.loads(item.metadata_json)["embedding_model"] == \
                "semantic-test"

    def test_single_batch_when_below_size(self, rag_env, monkeypatch):
        store, calls = rag_env
        store.extend(_make_items(7))
        monkeypatch.setenv("RAG_REFRESH_BATCH_SIZE", "200")
        _install_embedder(monkeypatch, calls)

        result = refresh_knowledge_base()

        assert result["status"] == "success"
        assert [len(batch) for batch in calls] == [7]

    def test_batch_failure_reported_not_masked(self, rag_env, monkeypatch):
        """某一批失败必须如实 failed（已完成批次保留），不得假报成功。"""
        store, calls = rag_env
        store.extend(_make_items(25))
        monkeypatch.setenv("RAG_REFRESH_BATCH_SIZE", "10")
        _install_embedder(monkeypatch, calls, fail_on_call=2)

        result = refresh_knowledge_base()

        assert result["status"] == "failed"
        assert "embedding provider exploded" in result["error"]
        assert result["refreshed"] == 10, "已完成批次计数应如实保留"

    def test_invalid_batch_size_env_falls_back(self, rag_env, monkeypatch):
        store, calls = rag_env
        store.extend(_make_items(3))
        monkeypatch.setenv("RAG_REFRESH_BATCH_SIZE", "not-a-number")
        _install_embedder(monkeypatch, calls)

        result = refresh_knowledge_base()

        assert result["status"] == "success"
        assert [len(batch) for batch in calls] == [3]


class TestLexicalScoreFloor:
    def test_default_constant_value(self):
        assert DEFAULT_LEXICAL_SCORE_FLOOR == 0.35

    def _legacy_search(self, monkeypatch, *, content="needle here",
                       floor=None):
        """单条旧 hashing 记录 + 词法命中的最小检索场景。"""
        item = SimpleNamespace(
            id="k0001", title="t", content=content, embedding="[]",
            embedding_model=None, metadata_json=None, source=None,
            category=None,
        )

        @contextmanager
        def fake_session_scope():
            yield SimpleNamespace()

        class _SearchRepo:
            def __init__(self, session):
                pass

            def get_all(self, offset=0, limit=100):
                return [item] if offset == 0 else []

        monkeypatch.setattr(session_module, "session_scope",
                            fake_session_scope)
        monkeypatch.setattr(repository_module, "KnowledgeRepository",
                            _SearchRepo)
        monkeypatch.setattr(embeddings_module, "create_semantic_embedder",
                            lambda: _FakeEmbedder([]))
        kwargs = {}
        if floor is not None:
            kwargs["lexical_score_floor"] = floor
        return search_knowledge("needle", top_k=5, **kwargs)

    def test_default_floor_applied(self, monkeypatch):
        result = self._legacy_search(monkeypatch)
        assert result["status"] == "success"
        assert result["results"][0]["score"] == pytest.approx(0.35)
        assert result["legacy_count"] == 1

    def test_explicit_floor_overrides(self, monkeypatch):
        result = self._legacy_search(monkeypatch, floor=0.7)
        assert result["results"][0]["score"] == pytest.approx(0.7)

    def test_floor_clamped_to_unit_interval(self, monkeypatch):
        result = self._legacy_search(monkeypatch, floor=5.0)
        assert result["results"][0]["score"] == pytest.approx(1.0)

    def test_non_lexical_legacy_stays_zero(self, monkeypatch):
        """无词法命中的旧记录不享受地板分（保持 0.0，既不拔高也不丢名次）。"""
        result = self._legacy_search(monkeypatch, content="nothing matches")
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert result["results"][0]["score"] == pytest.approx(0.0)
