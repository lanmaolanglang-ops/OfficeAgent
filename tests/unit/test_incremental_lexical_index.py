"""RAG Phase 2B — incremental lexical/TF-IDF index regression tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from office_agent.knowledge_base.embeddings import TfidfEmbedder, cosine_similarity
from office_agent.knowledge_base.models import KnowledgeChunk
from office_agent.knowledge_base.vector_store import VectorStore


def _chunk(text: str, chunk_id: str = "", doc_id: str = "doc",
           doc_type: str = "general", tags=None, title: str = ""):
    return KnowledgeChunk(
        id=chunk_id,
        document_id=doc_id,
        content=text,
        metadata={
            "doc_title": title or text[:12],
            "doc_type": doc_type,
            "tags": tags or [],
        },
    )


def _titles(results):
    return [result.document_title for result in results]


def _counted_tokenizer(store):
    original = store._tokenize
    seen = []

    def counted(text):
        seen.append(text)
        return original(text)

    store._tokenize = counted
    return seen


class TestIncrementalComplexity:
    def test_add_one_does_not_retokenize_history(self):
        store = VectorStore()
        store.add_chunks([
            _chunk(f"historical document {index} alpha", chunk_id=f"old-{index}")
            for index in range(100)
        ])
        seen = _counted_tokenizer(store)

        store.add_chunks([_chunk("new document unique beta", chunk_id="new-1")])

        assert seen == ["new document unique beta"]

    def test_update_one_does_not_retokenize_other_documents(self):
        store = VectorStore()
        store.add_chunks([
            _chunk(f"historical document {index} alpha", chunk_id=f"old-{index}")
            for index in range(50)
        ])
        store.add_chunks([_chunk("old target text", chunk_id="target")])
        seen = _counted_tokenizer(store)

        store.update_chunks([_chunk("new target text", chunk_id="target")])

        assert seen == ["new target text"]

    def test_delete_does_not_retokenize_any_document(self):
        store = VectorStore()
        store.add_chunks([
            _chunk(f"historical document {index} alpha", chunk_id=f"old-{index}")
            for index in range(50)
        ])
        seen = _counted_tokenizer(store)

        store.remove_chunks(["old-1"])

        assert seen == []


class TestIncrementalLifecycle:
    def test_add_updates_document_frequency(self):
        store = VectorStore()
        store.add_chunks([
            _chunk("alpha beta", chunk_id="a"),
            _chunk("beta gamma", chunk_id="b"),
        ])

        assert store._df["alpha"] == 1
        assert store._df["beta"] == 2
        assert store._idf("beta") < store._idf("alpha")

    def test_duplicate_chunk_id_is_idempotent(self):
        store = VectorStore()
        store.add_chunks([_chunk("alpha beta", chunk_id="dup")])
        store.add_chunks([_chunk("gamma delta", chunk_id="dup")])

        assert store.count == 1
        assert store._df["alpha"] == 0
        assert store._df["beta"] == 0
        assert store._df["gamma"] == 1
        assert store._postings["gamma"] == {"dup"}
        assert "alpha" not in store._postings

    def test_remove_chunks_removes_postings_and_results(self):
        store = VectorStore()
        store.add_chunks([
            _chunk("alpha unique term", chunk_id="a"),
            _chunk("beta other term", chunk_id="b"),
        ])

        assert store.remove_chunks(["a"]) == 1
        assert "alpha" not in store._postings
        assert store.search("alpha", min_score=0) == []

    def test_update_chunks_replaces_old_text(self):
        store = VectorStore()
        store.add_chunks([_chunk("old contract wording", chunk_id="a")])

        store.update_chunks([_chunk("new contract wording", chunk_id="a")])

        assert store.search("old", min_score=0) == []
        assert store.search("new", min_score=0)[0].document_id == "doc"

    def test_full_rebuild_repairs_corrupt_state(self):
        store = VectorStore()
        store.add_chunks([_chunk("alpha beta", chunk_id="a")])
        store._postings.clear()
        store._df.clear()
        store._doc_tf.clear()

        assert store.rebuild_index() == 1
        assert store.search("alpha", min_score=0)

    def test_missing_store_load_returns_zero(self, tmp_path):
        store = VectorStore()
        assert store.load(str(tmp_path / "missing.json")) == 0
        assert store.count == 0


def _reference_top_titles(texts, query, top_k=3):
    reference = TfidfEmbedder()
    reference.fit(texts)
    query_vector = reference.embed_query(query)
    probe = VectorStore()
    query_keywords = set(probe._extract_keywords(query))
    scores = []
    for text in texts:
        vector = reference.embed([text])[0]
        vector_score = cosine_similarity(query_vector, vector)
        text_keywords = set(probe._extract_keywords(text))
        keyword_score = (
            len(query_keywords & text_keywords) / max(1, len(query_keywords))
            if query_keywords else 0.0
        )
        scores.append((vector_score + keyword_score * 0.15, text[:12]))
    scores.sort(key=lambda item: item[0], reverse=True)
    return [title for _, title in scores[:top_k]]


class TestRetrievalQuality:
    def test_chinese_english_mixed_retrieval(self):
        store = VectorStore()
        store.add_chunks([
            _chunk("中文论文格式规范：正文使用宋体小四", chunk_id="zh", title="中文规范"),
            _chunk("English style guide uses clear headings", chunk_id="en", title="英文规范"),
            _chunk("中英混排 style 规则包含 heading 和 宋体", chunk_id="mix", title="混排规范"),
        ])

        assert "中文规范" in _titles(store.search("宋体", top_k=3, min_score=0))
        assert "英文规范" in _titles(store.search("headings", top_k=3, min_score=0))
        assert "混排规范" in _titles(store.search("混排 heading", top_k=3, min_score=0))

    def test_incremental_matches_full_rebuild_reference(self):
        texts = [
            "alpha compliance policy for quarterly archive",
            "alpha retention schedule with compliance",
            "beta sales growth and margin calculation",
            "beta sales region percentage and trend",
            "gamma Chinese font size and line spacing",
            "gamma document heading and page margin",
            "delta inventory count and warehouse check",
            "delta stock take and inventory reconciliation",
        ]
        store = VectorStore()
        store.add_chunks([
            _chunk(text, chunk_id=f"d{index}", title=text[:12])
            for index, text in enumerate(texts)
        ])

        for query in ["alpha compliance", "beta sales", "gamma font", "delta inventory"]:
            incremental = set(_titles(store.search(query, top_k=3, min_score=0)))
            reference = set(_reference_top_titles(texts, query, top_k=3))
            assert incremental & reference

    def test_category_and_tag_filters_are_preserved(self):
        store = VectorStore()
        store.add_chunks([
            _chunk("alpha policy content", chunk_id="p", doc_type="policy", tags=["official"], title="alpha policy content"),
            _chunk("alpha draft content", chunk_id="d", doc_type="policy", tags=["draft"], title="alpha draft content"),
            _chunk("alpha other content", chunk_id="o", doc_type="general", tags=["official"], title="alpha other content"),
        ])

        filtered = store.search(
            "alpha", min_score=0, doc_types=["policy"], tags=["official"],
        )
        assert [result.document_id for result in filtered] == ["doc"]
        assert filtered[0].document_title == "alpha policy content"


class TestPersistenceAndRestart:
    def test_office_kb_restart_preserves_incremental_search(self, tmp_path):
        from office_agent.knowledge_base.knowledge_base import OfficeKnowledgeBase

        kb = OfficeKnowledgeBase(str(tmp_path))
        kb.add_text("alpha policy content", title="alpha", doc_type="policy")
        kb.add_text("beta policy content", title="beta", doc_type="policy")
        kb.update_text(
            next(iter(kb.documents)), "alpha updated content",
            title="alpha", doc_type="policy",
        )
        beta_id = next(
            doc_id for doc_id, doc in kb.documents.items()
            if doc.title == "beta"
        )
        kb.remove_document(beta_id)

        restarted = OfficeKnowledgeBase(str(tmp_path))
        assert restarted.search("updated", min_score=0)
        assert restarted.search("beta", min_score=0).best_score == 0

    def test_vector_store_save_load_rebuilds_index(self, tmp_path):
        path = tmp_path / "store.json"
        store = VectorStore()
        store.add_chunks([_chunk("alpha beta", chunk_id="a")])
        store.save(str(path))

        loaded = VectorStore()
        assert loaded.load(str(path)) == 1
        assert loaded.search("alpha", min_score=0)


class TestConcurrentReadWrite:
    def test_query_never_sees_half_written_add(self):
        store = VectorStore()
        store.add_chunks([_chunk("initial alpha", chunk_id="init")])

        def writer(worker_id):
            store.add_chunks([
                _chunk(f"worker {worker_id} alpha beta", chunk_id=f"w{worker_id}"),
            ])
            return store.search("alpha beta", min_score=0)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(writer, range(4)))

        assert all(result for result in results)
        assert store.count == 5
        assert store.search("worker", min_score=0)
