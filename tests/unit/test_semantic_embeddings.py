"""Semantic embedding Phase 1 contract tests.

The product backend is real when installed/configured; these tests prove the
contract with deterministic fakes and verify legacy-vector isolation without
downloading any model.
"""

from __future__ import annotations

import json
import math

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _ParaphraseEmbedder:
    model_id = "fake-semantic-v1"
    version = "test"
    semantic = True
    dimension = 2

    def embed(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        text = text.lower()
        if "库存" in text or "盘点" in text:
            return [0.0, 1.0]
        if "燃烧效率" in text or "能源" in text:
            return [0.9, 0.1]
        if "锅炉" in text or "燃料" in text or "燃烧" in text:
            return [1.0, 0.0]
        return [0.0, 0.0]


class _CountingEmbedder:
    model_id = "counting-v1"
    version = "test"
    semantic = True
    dimension = 3

    def __init__(self):
        self.batch_calls = 0
        self.query_calls = 0

    def embed(self, texts):
        self.batch_calls += 1
        return [[1.0 / math.sqrt(3)] * 3 for _ in texts]

    def embed_query(self, text):
        self.query_calls += 1
        return [1.0 / math.sqrt(3)] * 3


def _configure_db(tmp_path, monkeypatch, request):
    from office_agent.database.base import Base
    import office_agent.database.models  # noqa: F401
    from office_agent.database import session as session_module

    engine = create_engine(f"sqlite:///{tmp_path / 'semantic-rag.db'}")
    request.addfinalizer(engine.dispose)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    return factory


class TestEmbeddingContract:
    def test_backend_factory_fails_clearly_when_nothing_configured(self, monkeypatch):
        from office_agent.knowledge_base.embeddings import (
            EmbeddingBackendUnavailableError, LocalSemanticEmbedder,
            create_semantic_embedder,
        )

        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BACKEND", "auto")
        monkeypatch.delenv("OFFICE_AGENT_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def _unavailable(*args, **kwargs):
            raise EmbeddingBackendUnavailableError("no local backend")

        monkeypatch.setattr(LocalSemanticEmbedder, "__init__", _unavailable)
        with pytest.raises(EmbeddingBackendUnavailableError):
            create_semantic_embedder()

    def test_backend_factory_uses_explicit_api(self, monkeypatch):
        from office_agent.knowledge_base import embeddings as embeddings_module

        fake = _CountingEmbedder()
        monkeypatch.setattr(
            embeddings_module, "APIEmbedder", lambda **kwargs: fake,
        )
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BACKEND", "api")
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_API_KEY", "sk-test")
        assert embeddings_module.create_semantic_embedder() is fake

    def test_paraphrase_scores_higher_than_lexical_distractor(self):
        from office_agent.knowledge_base.embeddings import cosine_similarity

        embedder = _ParaphraseEmbedder()
        query = embedder.embed_query("如何降低锅炉燃料消耗")
        positive = embedder.embed_query("提高燃烧效率可以减少单位蒸汽的能源使用")
        distractor = embedder.embed_query("燃料仓库今天完成了库存盘点")

        assert cosine_similarity(query, positive) > cosine_similarity(
            query, distractor,
        )

    def test_batch_embedding_uses_one_backend_call(self):
        embedder = _CountingEmbedder()
        vectors = embedder.embed(["第一段", "第二段", "第三段"])
        assert embedder.batch_calls == 1
        assert len(vectors) == 3
        assert all(len(vector) == embedder.dimension for vector in vectors)

    def test_vectors_are_normalized_and_finite(self):
        embedder = _CountingEmbedder()
        vector = embedder.embed_query("任意文本")
        assert len(vector) == 3
        assert all(math.isfinite(value) for value in vector)
        assert math.isclose(sum(value * value for value in vector), 1.0)

    def test_empty_text_has_explicit_behavior(self):
        embedder = _CountingEmbedder()
        vector = embedder.embed_query("")
        assert vector == [1.0 / math.sqrt(3)] * 3


class TestLegacyVectorIsolation:
    def _add_knowledge(self, factory, *, model, vector, content, title):
        from office_agent.database.repository import KnowledgeRepository

        with factory() as session:
            repo = KnowledgeRepository(session)
            repo.add_knowledge(
                title=title,
                content=content,
                category="policy",
                embedding=json.dumps(vector),
                embedding_model=model,
                metadata_json=json.dumps({
                    "embedding_model": model,
                    "embedding_dimension": len(vector),
                }),
            )
            session.commit()

    def test_legacy_hashing_is_flagged_and_not_mixed(
            self, tmp_path, monkeypatch, request, fake_semantic_embedder):
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        factory = _configure_db(tmp_path, monkeypatch, request)
        self._add_knowledge(
            factory,
            model="hashing-512-v1",
            vector=[0.0] * 4,
            content="锅炉节能与燃料消耗优化",
            title="旧索引",
        )

        result = search_knowledge("锅炉", category="policy")
        assert result["status"] == "success"
        assert result["rebuild_required"] is True
        assert result["legacy_count"] == 1
        assert result["results"][0]["title"] == "旧索引"

    def test_model_mismatch_is_flagged_not_mixed(
            self, tmp_path, monkeypatch, request, fake_semantic_embedder):
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        factory = _configure_db(tmp_path, monkeypatch, request)
        self._add_knowledge(
            factory,
            model="other-semantic-v1",
            vector=[0.0, 0.0, 0.0, 0.0, 0.0],
            content="完全不相关的另一句话",
            title="不兼容索引",
        )

        result = search_knowledge("锅炉", category="policy")
        assert result["status"] == "success"
        assert result["rebuild_required"] is True
        assert result["incompatible_count"] == 1

    def test_current_semantic_rows_use_cosine(
            self, tmp_path, monkeypatch, request, fake_semantic_embedder):
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        factory = _configure_db(tmp_path, monkeypatch, request)
        self._add_knowledge(
            factory,
            model="semantic-test-v1",
            vector=[0.1, 0.2, 0.3, 0.4],
            content="与查询向量完全一致",
            title="语义索引",
        )

        result = search_knowledge("查询", category="policy")
        assert result["status"] == "success"
        assert result["rebuild_required"] is False
        assert result["results"][0]["title"] == "语义索引"
        assert result["results"][0]["score"] > 0.9

    def test_backend_failure_is_not_silently_hashing(
            self, tmp_path, monkeypatch, request):
        from office_agent.knowledge_base import embeddings as embeddings_module
        from office_agent.knowledge_base.embeddings import (
            EmbeddingBackendUnavailableError,
        )
        from office_agent.task_queue.tasks.rag_tasks import search_knowledge

        _configure_db(tmp_path, monkeypatch, request)

        def _unavailable(*args, **kwargs):
            raise EmbeddingBackendUnavailableError("backend down")

        monkeypatch.setattr(
            embeddings_module, "create_semantic_embedder", _unavailable,
        )
        result = search_knowledge("查询", category="policy")
        assert result["status"] == "failed"
        assert "RAG 语义检索后端未配置" in result["error"]
        assert "Embedding 模型" in result["error"]


class TestCosineContract:
    def test_bruteforce_cosine_still_works(self):
        from office_agent.knowledge_base.embeddings import (
            batch_cosine_similarity, cosine_similarity,
        )

        query = [1.0, 0.0]
        corpus = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
        assert cosine_similarity(query, [0.0, 1.0]) == 0.0
        scores = batch_cosine_similarity(query, corpus)
        assert scores[0] > scores[1]
        assert scores[2] == 0.0


class TestEmbeddingRuntimeDeployment:
    def test_embedding_config_manager_persists_without_plaintext_key(
            self, tmp_path):
        from office_agent.knowledge_base.embedding_config import (
            EmbeddingConfigManager,
        )

        manager = EmbeddingConfigManager(config_dir=str(tmp_path))
        config = manager.save_config(
            provider="custom",
            api_key="sk-secret-value",
            base_url="https://example.com/v1",
            model="text-embedding-3-small",
        )
        assert manager.is_configured() is True
        assert config["api_key"] == "sk-secret-value"

        raw = json.loads((tmp_path / "embedding_config.json").read_text())
        assert "sk-secret-value" not in raw
        assert raw["api_key_enc"]

        reloaded = EmbeddingConfigManager(config_dir=str(tmp_path))
        assert reloaded.get_config()["api_key"] == "sk-secret-value"

    def test_embedding_config_manager_preserves_secret_when_blank(
            self, tmp_path):
        from office_agent.knowledge_base.embedding_config import (
            EmbeddingConfigManager,
        )

        manager = EmbeddingConfigManager(config_dir=str(tmp_path))
        manager.save_config(
            provider="custom",
            api_key="sk-existing-secret",
            base_url="https://old.example.com/v1",
            model="old-model",
        )
        updated = manager.save_config(
            provider="custom",
            api_key="",
            base_url="https://new.example.com/v1",
            model="new-model",
        )
        assert updated["api_key"] == "sk-existing-secret"
        assert updated["base_url"] == "https://new.example.com/v1"
        assert updated["model"] == "new-model"

    def test_create_semantic_embedder_uses_persisted_config(self, monkeypatch):
        from office_agent.knowledge_base import embeddings as embeddings_module
        from office_agent.knowledge_base import embedding_config as config_module

        captured = {}
        fake = _CountingEmbedder()

        class _ConfiguredManager:
            def is_configured(self):
                return True

            def get_config(self):
                return {
                    "provider": "custom",
                    "api_key": "sk-test",
                    "base_url": "https://example.com/v1",
                    "model": "text-embedding-3-small",
                }

        monkeypatch.setattr(
            config_module, "EmbeddingConfigManager", _ConfiguredManager,
        )
        monkeypatch.setattr(
            embeddings_module,
            "APIEmbedder",
            lambda **kwargs: captured.update(kwargs) or fake,
        )
        monkeypatch.setattr(
            embeddings_module,
            "LocalSemanticEmbedder",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                embeddings_module.EmbeddingBackendUnavailableError("local"),
            ),
        )
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BACKEND", "auto")
        monkeypatch.delenv("OFFICE_AGENT_EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        assert embeddings_module.create_semantic_embedder() is fake
        assert captured["model"] == "text-embedding-3-small"

    def test_api_embedder_controlled_integration(self, monkeypatch):
        import urllib.request

        from office_agent.knowledge_base.embeddings import APIEmbedder

        class _Response:
            def read(self):
                return json.dumps({
                    "data": [{"embedding": [0.0, 0.0, 0.0, 1.0]}],
                }).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda request, timeout: (_Response()),
        )
        embedder = APIEmbedder(
            api_key="sk-test",
            base_url="https://example.com/v1",
            model="text-embedding-3-small",
            dimension=4,
        )
        vector = embedder.embed_query("中文语义检索")
        assert embedder.semantic is True
        assert embedder.model_id == "api:text-embedding-3-small"
        assert len(vector) == 4
        assert vector == [0.0, 0.0, 0.0, 1.0]
