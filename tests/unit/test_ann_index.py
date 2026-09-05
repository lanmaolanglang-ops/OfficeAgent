"""Deterministic tests for the RAG usearch acceleration layer."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from office_agent.knowledge_base.ann_index import (
    ANNIndexCorruptedError,
    ANNIndexUnavailableError,
    KnowledgeVectorIndex,
)


def _vectors_by_id():
    return {
        "a": [1.0, 0.0],
        "b": [0.0, 1.0],
        "c": [0.7071068, 0.7071068],
        "d": [-1.0, 0.0],
    }


class TestKnowledgeVectorIndex:
    def test_rebuild_load_persist_and_search_order(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        records = list(_vectors_by_id().items())
        assert index.rebuild(records, "model-v1", "1", 2) == 4
        assert index.index_path.exists()
        assert index.manifest_path.exists()
        assert index.id_map_path.exists()

        reloaded = KnowledgeVectorIndex(tmp_path)
        assert reloaded.load("model-v1", "1", 2) is True
        assert reloaded.count == 4
        hits = reloaded.search([1.0, 0.0], 2, overfetch=1)
        assert [record_id for record_id, _ in hits] == ["a", "c"]

    def test_manifest_identity_and_dimension_mismatch(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild([("a", [1.0, 0.0])], "model-v1", "1", 2)

        assert index.manifest_matches("model-v1", "1", 2) is True
        assert index.manifest_matches("other-model", "1", 2) is False
        assert index.manifest_matches("model-v1", "2", 2) is False
        assert index.manifest_matches("model-v1", "1", 3) is False
        assert KnowledgeVectorIndex(tmp_path).load("other-model", "1", 2) is False
        assert KnowledgeVectorIndex(tmp_path).load("model-v1", "1", 3) is False

    def test_upsert_adds_and_updates_stable_ids(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild(
            [("a", [1.0, 0.0]), ("b", [0.0, 1.0])],
            "model-v1", "1", 2,
        )

        index.upsert(
            [("c", [0.7071068, 0.7071068]), ("a", [0.7071068, -0.7071068])],
            "model-v1", "1", 2,
        )
        assert index.count == 3
        hits = index.search([0.7071068, -0.7071068], 3, overfetch=1)
        assert hits[0][0] == "a"
        assert "c" in {record_id for record_id, _ in hits}

        reloaded = KnowledgeVectorIndex(tmp_path)
        assert reloaded.load("model-v1", "1", 2) is True
        assert reloaded.count == 3
        assert reloaded.search(
            [0.7071068, -0.7071068], 1, overfetch=1,
        )[0][0] == "a"

    def test_missing_index_and_corrupted_index(self, tmp_path):
        assert KnowledgeVectorIndex(tmp_path).load("model-v1", "1", 2) is False

        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild([("a", [1.0, 0.0])], "model-v1", "1", 2)
        index.index_path.write_bytes(b"not a usearch index")
        with pytest.raises(ANNIndexCorruptedError):
            KnowledgeVectorIndex(tmp_path).load("model-v1", "1", 2)

    def test_search_requires_loaded_index_and_matching_dimension(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        with pytest.raises(ANNIndexUnavailableError):
            index.search([1.0, 0.0], 1)

        index.rebuild([("a", [1.0, 0.0])], "model-v1", "1", 2)
        with pytest.raises(Exception):
            index.search([1.0, 0.0, 0.0], 1)

    def test_cosine_distance_is_converted_to_similarity(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild(
            [("same", [1.0, 0.0]), ("orthogonal", [0.0, 1.0])],
            "model-v1", "1", 2,
        )
        assert index.search([1.0, 0.0], 2, overfetch=1) == [
            ("same", 1.0),
            ("orthogonal", 0.0),
        ]

    def test_missing_ids_only_reports_unindexed_string_ids(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild([("a", [1.0, 0.0])], "model-v1", "1", 2)
        assert index.missing_ids(["a", "b"]) == ["b"]

    def test_concurrent_upsert_and_search_are_serialized(self, tmp_path):
        index = KnowledgeVectorIndex(tmp_path)
        index.rebuild(
            [(f"seed-{i}", [float(i), 1.0]) for i in range(4)],
            "model-v1", "1", 2,
        )

        def worker(worker_id: int):
            records = [
                (f"w{worker_id}-{i}", [float(i + 1), float(worker_id + 1)])
                for i in range(8)
            ]
            index.upsert(records, "model-v1", "1", 2)
            hits = index.search([1.0, 1.0], 5, overfetch=1)
            return len(hits)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))

        assert all(result > 0 for result in results)
        assert index.count == 4 + 4 * 8
        assert len(index.search([1.0, 1.0], 10, overfetch=1)) == 10


def _configure_rag_db(tmp_path, monkeypatch, request):
    from office_agent.database import session as session_module
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 'ann-rag.db'}")
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    return factory


class _AnnEmbedder:
    model_id = "semantic-ann-v1"
    version = "test"
    semantic = True
    dimension = 2

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        if "甲" in text:
            return [1.0, 0.0]
        if "乙" in text:
            return [0.0, 1.0]
        if "丙" in text:
            return [0.7071068, 0.7071068]
        return [0.0, 0.0]


def _install_ann_embedder(monkeypatch):
    from office_agent.knowledge_base import embeddings as embeddings_module

    embedder = _AnnEmbedder()
    monkeypatch.setattr(
        embeddings_module, "create_semantic_embedder", lambda *a, **kw: embedder,
    )
    return embedder


def _add_knowledge(factory, title, content, vector, *, model="semantic-ann-v1",
                   dimension=2, category="policy"):
    from office_agent.database.repository import KnowledgeRepository

    with factory() as session:
        KnowledgeRepository(session).add_knowledge(
            title=title,
            content=content,
            category=category,
            embedding=json.dumps(vector),
            embedding_model=model,
            metadata_json=json.dumps({
                "embedding_model": model,
                "embedding_dimension": dimension,
            }),
        )
        session.commit()


class TestRagAnnIntegration:
    def test_large_candidate_search_uses_persistent_ann_index(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        _add_knowledge(factory, "甲规则", "甲的归档内容", [1.0, 0.0])
        _add_knowledge(factory, "乙规则", "乙的普通内容", [0.0, 1.0])
        _add_knowledge(factory, "丙规则", "丙的相近内容", [0.7071068, 0.7071068])

        first = search_knowledge("甲", top_k=2, min_ann_candidates=2)
        second = search_knowledge("甲", top_k=2, min_ann_candidates=2)

        assert first["ann_used"] is True
        assert first["ann_status"] == "rebuilt"
        assert first["ann_index_count"] == 3
        assert first["results"][0]["title"] == "甲规则"
        assert second["ann_status"] == "active"
        assert second["ann_used"] is True

    def test_small_dataset_uses_exact_bruteforce_path(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        _add_knowledge(factory, "甲规则", "甲的归档内容", [1.0, 0.0])
        result = search_knowledge("甲", min_ann_candidates=100)
        assert result["ann_used"] is False
        assert result["ann_status"] == "small_dataset"
        assert result["results"][0]["title"] == "甲规则"

    def test_ann_failure_falls_back_to_exact_cosine(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks import rag_tasks

        _add_knowledge(factory, "甲规则", "甲的归档内容", [1.0, 0.0])
        _add_knowledge(factory, "乙规则", "乙的普通内容", [0.0, 1.0])

        def _broken(*args, **kwargs):
            raise ANNIndexUnavailableError("simulated")

        monkeypatch.setattr(rag_tasks, "_prepare_ann_index", _broken)
        result = rag_tasks.search_knowledge("甲", min_ann_candidates=1)
        assert result["ann_used"] is False
        assert result["ann_status"] == "fallback"
        assert result["ann_reason"] == "ANNIndexUnavailableError"
        assert result["results"][0]["title"] == "甲规则"

    def test_legacy_vectors_are_excluded_from_ann_index(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        _add_knowledge(
            factory, "旧索引", "旧内容", [0.0] * 4,
            model="hashing-512-v1", dimension=4,
        )
        _add_knowledge(factory, "甲规则", "甲的归档内容", [1.0, 0.0])
        _add_knowledge(factory, "乙规则", "乙的普通内容", [0.0, 1.0])

        result = search_knowledge("甲", min_ann_candidates=1)
        assert result["rebuild_required"] is True
        assert result["legacy_count"] == 1
        assert result["ann_used"] is True
        assert result["ann_index_count"] == 2

    def test_ann_path_preserves_category_metadata_filter(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        _add_knowledge(factory, "甲政策", "甲的归档内容", [1.0, 0.0])
        _add_knowledge(factory, "乙政策", "乙的普通内容", [0.0, 1.0])
        _add_knowledge(
            factory, "丙其他", "丙的相近内容", [0.7071068, 0.7071068],
            category="other",
        )

        result = search_knowledge(
            "甲", top_k=5, category="policy", min_ann_candidates=1,
        )
        assert result["ann_used"] is True
        assert result["candidate_count"] == 2
        assert {item["category"] for item in result["results"]} == {"policy"}
        assert [item["title"] for item in result["results"]] == ["甲政策", "乙政策"]

    def test_refresh_rebuilds_index_when_large(
            self, tmp_path, monkeypatch, request):
        factory = _configure_rag_db(tmp_path, monkeypatch, request)
        _install_ann_embedder(monkeypatch)
        monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path / "data"))
        from office_agent.task_queue.tasks import rag_tasks

        _add_knowledge(factory, "甲规则", "甲的归档内容", [1.0, 0.0])
        _add_knowledge(factory, "乙规则", "乙的普通内容", [0.0, 1.0])
        _add_knowledge(factory, "丙规则", "丙的相近内容", [0.7071068, 0.7071068])
        monkeypatch.setattr(rag_tasks, "ANN_MIN_CANDIDATES", 3)

        result = rag_tasks.refresh_knowledge_base()
        assert result == {"status": "success", "refreshed": 3, "total": 3}

        index = KnowledgeVectorIndex(tmp_path / "data" / "rag_index")
        assert index.load("semantic-ann-v1", "test", 2) is True
        assert index.count == 3
