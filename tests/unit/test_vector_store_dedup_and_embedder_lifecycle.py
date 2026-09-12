"""P1-18（C）/ P1-19：向量库按 id 去重语义完整性 + 知识库加载不得强绑 TF-IDF。

P1-18C 修复前行为：``VectorStore.add_chunks`` 的**非增量**分支直接 append，
同一 id 重复写入会产生重复 chunk（``count`` 虚高、检索出现重复结果）。

P1-19 修复前行为：``OfficeKnowledgeBase._auto_load`` 无条件
``self.store.rebuild_index()``，而 ``rebuild_index`` 内部调用
``self._tfidf()``——对非 ``TfidfEmbedder`` 直接 ``raise RuntimeError``，
于是"用户配置了远端/稠密 embedding"的知识库**加载即失败**。
"""

from __future__ import annotations

import json

import pytest

from office_agent.knowledge_base.embeddings import BaseEmbedder, TfidfEmbedder
from office_agent.knowledge_base.knowledge_base import OfficeKnowledgeBase
from office_agent.knowledge_base.models import KnowledgeChunk
from office_agent.knowledge_base.vector_store import VectorStore


class _DenseEmbedder(BaseEmbedder):
    """确定性稠密 embedding（非 TF-IDF），用于验证非增量路径。"""

    def __init__(self, dimension: int = 3):
        self._dimension = dimension
        self.batch_calls = 0

    def embed(self, texts):
        self.batch_calls += 1
        vectors = []
        for text in texts:
            vec = [0.0] * self._dimension
            vec[0] = float(len(text))
            vectors.append(vec)
        return vectors

    def embed_query(self, text):
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return "dense-test-v1"

    @property
    def semantic(self) -> bool:
        return True


LONG_TEXT = "这是一段足够长的知识库文本内容，用于切片与检索测试。"


class TestNonIncrementalDedup:
    def test_same_id_is_not_written_twice(self):
        store = VectorStore(_DenseEmbedder())
        store.add_chunks([KnowledgeChunk(id="c1", content="第一版内容", document_id="d1")])
        store.add_chunks([KnowledgeChunk(id="c1", content="第二版内容已更新", document_id="d1")])

        assert store.count == 1
        assert [sc.chunk.id for sc in store._chunks] == ["c1"]
        assert store._chunk_by_id["c1"].chunk.content == "第二版内容已更新"

    def test_duplicate_ids_inside_one_call_are_collapsed(self):
        store = VectorStore(_DenseEmbedder())
        store.add_chunks([
            KnowledgeChunk(id="c1", content="内容甲"),
            KnowledgeChunk(id="c1", content="内容乙"),
        ])
        assert store.count == 1
        assert store._chunk_by_id["c1"].chunk.content == "内容乙"

    def test_order_and_id_mapping_stay_consistent(self):
        store = VectorStore(_DenseEmbedder())
        store.add_chunks([
            KnowledgeChunk(id="c1", content="内容甲"),
            KnowledgeChunk(id="c2", content="内容乙"),
            KnowledgeChunk(id="c3", content="内容丙"),
        ])
        store.add_chunks([KnowledgeChunk(id="c2", content="内容乙更新")])

        ids = [sc.chunk.id for sc in store._chunks]
        assert sorted(ids) == ["c1", "c2", "c3"]
        assert len(ids) == len(set(ids))
        assert store.count == 3
        assert set(store._chunk_by_id) == {"c1", "c2", "c3"}
        # 映射必须指向列表中真正存在的那一个对象
        assert [store._chunk_by_id[i] for i in ids] == store._chunks

    def test_stale_keyword_cache_is_dropped_on_overwrite(self):
        store = VectorStore(_DenseEmbedder())
        store.add_chunks([KnowledgeChunk(id="c1", content="锅炉节能改造方案")])
        assert "锅炉" in store._cache_chunk_keywords(
            store._chunk_by_id["c1"].chunk
        )
        store.add_chunks([KnowledgeChunk(id="c1", content="库存盘点流程说明")])
        cached = store._keyword_cache["c1"]
        assert "锅炉" not in cached
        assert "库存" in cached

    def test_dedup_still_holds_when_backend_batches_requests(self):
        from office_agent.knowledge_base.embeddings import APIEmbedder
        import urllib.request

        calls = []

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            inputs = list(payload["input"])
            calls.append(inputs)
            return _Response({
                "data": [{"embedding": [float(len(t)), 0.0]} for t in inputs],
            })

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        try:
            store = VectorStore(APIEmbedder(
                api_key="sk-test", base_url="https://example.com/v1",
                model="m", dimension=2, batch_size=2,
            ))
            store.add_chunks([
                KnowledgeChunk(id="c1", content="内容甲"),
                KnowledgeChunk(id="c2", content="内容乙"),
            ])
            store.add_chunks([KnowledgeChunk(id="c1", content="内容甲更新")])
        finally:
            monkeypatch.undo()

        assert store.count == 2
        assert store._chunk_by_id["c1"].chunk.content == "内容甲更新"
        assert len(calls) == 2  # 批次没有被去重逻辑打乱


class TestIncrementalDedupNotRegressed:
    def test_tfidf_same_id_overwrite(self):
        store = VectorStore(TfidfEmbedder())
        store.add_chunks([KnowledgeChunk(id="c1", content="第一版的知识库内容")])
        store.add_chunks([KnowledgeChunk(id="c1", content="第二版的知识库内容")])

        assert store.count == 1
        assert store._chunk_by_id["c1"].chunk.content == "第二版的知识库内容"

    def test_tfidf_search_uses_latest_content(self):
        store = VectorStore(TfidfEmbedder())
        store.add_chunks([KnowledgeChunk(id="c1", content="锅炉节能与燃料消耗优化")])
        assert store.search("锅炉") != []

        store.add_chunks([KnowledgeChunk(id="c1", content="库存盘点与仓储流程规范")])
        assert store.search("锅炉") == []
        assert store.search("库存盘点") != []


class TestKnowledgeBaseEmbedderLifecycle:
    def test_tfidf_backend_loads_and_rebuilds(self, tmp_path):
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        kb.add_text(LONG_TEXT, title="规范", doc_type="word_spec")
        assert kb.stats["total_chunks"] >= 1

        reloaded = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        assert reloaded.stats["total_chunks"] == kb.stats["total_chunks"]
        assert reloaded.store._fitted is True

    def test_non_tfidf_backend_loads_without_calling_tfidf(self, tmp_path, monkeypatch):
        calls = []
        original = VectorStore._tfidf

        def spy(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(VectorStore, "_tfidf", spy)

        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path), embedder=_DenseEmbedder())
        assert calls == [], "加载期不得进入仅适用于 TfidfEmbedder 的路径"

        kb.add_text(LONG_TEXT, title="规范", doc_type="word_spec")
        assert calls == [], "非 TF-IDF 写入路径也不应触碰 _tfidf()"

    def test_non_tfidf_persisted_index_reloads_and_searches(self, tmp_path):
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path), embedder=_DenseEmbedder())
        kb.add_text(LONG_TEXT, title="规范", doc_type="word_spec")
        total = kb.stats["total_chunks"]
        assert total >= 1

        reloaded = OfficeKnowledgeBase(
            storage_dir=str(tmp_path), embedder=_DenseEmbedder(),
        )
        assert reloaded.stats["total_chunks"] == total
        assert reloaded.store._chunk_by_id, "持久向量应随 chunk 一起恢复"
        # 持久向量可被检索（不是被悄悄替换成零向量的空壳）
        context = reloaded.search(LONG_TEXT[:6])
        assert context.total_results >= 1

    def test_selected_embedder_is_never_swapped(self, tmp_path):
        embedder = _DenseEmbedder()
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path), embedder=embedder)
        assert kb.embedder is embedder
        assert kb.store.embedder is embedder
        assert kb.store._is_incremental() is False

        kb.add_text(LONG_TEXT, title="规范")
        assert kb.embedder is embedder
        assert kb.store.embedder is embedder

    def test_empty_knowledge_base_is_controlled(self, tmp_path):
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path), embedder=_DenseEmbedder())
        assert kb.stats["total_documents"] == 0
        assert kb.stats["total_chunks"] == 0
        assert kb.search("任意查询").total_results == 0

    def test_missing_index_files_fall_back_without_crash(self, tmp_path):
        (tmp_path / "chunks.json").write_text("[]", encoding="utf-8")
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        assert kb.stats["total_chunks"] == 0

    def test_corrupt_index_files_degrade_gracefully(self, tmp_path):
        (tmp_path / "chunks.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "documents.json").write_text("also broken", encoding="utf-8")

        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        assert kb.stats["total_chunks"] == 0
        assert kb.stats["total_documents"] == 0

    def test_rebuild_failure_fails_closed_not_silently_swallowed(
            self, tmp_path, monkeypatch):
        """重建索引抛错时必须显式失败，不得伪造成"加载成功"。

        这是刻意的契约：P1-19 的根因是"路径绑错"（强绑 TF-IDF），
        而不是"异常没被吞掉"，因此不引入 catch-all 兜底。
        """
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        kb.add_text(LONG_TEXT, title="规范")

        def boom(self):
            raise RuntimeError("simulated rebuild failure")

        monkeypatch.setattr(VectorStore, "rebuild_index", boom)
        with pytest.raises(RuntimeError):
            OfficeKnowledgeBase(storage_dir=str(tmp_path))

    def test_tfidf_rebuild_after_manual_clear(self, tmp_path):
        kb = OfficeKnowledgeBase(storage_dir=str(tmp_path))
        kb.add_text(LONG_TEXT, title="规范")
        assert kb.store.count >= 1

        kb.store.clear()
        assert kb.store.count == 0
        kb.store.add_chunks([
            KnowledgeChunk(id="c9", content=LONG_TEXT, document_id="d9"),
        ])
        assert kb.store.count == 1
