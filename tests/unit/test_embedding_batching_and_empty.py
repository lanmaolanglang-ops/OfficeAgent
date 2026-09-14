"""P1-18（A/B）：Embedding provider 空返回必须区别于合法空输入，且请求必须分批。

修复前行为（旧源码副本实测）：
- ``APIEmbedder.embed`` 直接 ``[item["embedding"] for item in data["data"]]``，
  provider 返回 ``{"data": []}`` 时静默返回 ``[]``：非空输入却拿到 0 条向量，
  下游会以为 embedding 成功。
- 整个输入一次性发给 provider，1000 条文本就是一次无界请求。

全部 mock，禁止真实 embedding 网络请求。
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from office_agent.knowledge_base.embeddings import (
    APIEmbedder,
    EmbeddingProviderResponseError,
    InvalidEmbeddingVectorError,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeProvider:
    """按批次记录请求输入，并可注入各类畸形响应。"""

    def __init__(self, *, dimension=4, vector_count=None,
                 empty_batch_index=None, raise_on_call=None,
                 empty_vector=False, malformed_item=False, missing_data=False):
        self.calls: list[list[str]] = []
        self.dimension = dimension
        self.vector_count = vector_count
        self.empty_batch_index = empty_batch_index
        self.raise_on_call = raise_on_call
        self.empty_vector = empty_vector
        self.malformed_item = malformed_item
        self.missing_data = missing_data

    def _vector(self, text):
        try:
            head = float(str(text)[1:])
        except (TypeError, ValueError):
            head = 0.0
        return [head] + [0.0] * (self.dimension - 1)

    def urlopen(self, request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        inputs = list(payload["input"])
        index = len(self.calls)
        self.calls.append(inputs)

        if self.raise_on_call is not None and index == self.raise_on_call:
            raise TimeoutError("simulated provider failure")
        if self.missing_data:
            return _Response({"error": "rate limited"})
        if self.malformed_item:
            return _Response({"data": [{"not_embedding": 1}]})
        if self.empty_batch_index is not None and index == self.empty_batch_index:
            return _Response({"data": []})

        count = self.vector_count if self.vector_count is not None else len(inputs)
        if self.empty_vector:
            items = [{"embedding": []} for _ in range(count)]
        else:
            items = [{"embedding": self._vector(text)} for text in inputs[:count]]
            while len(items) < count:
                items.append({"embedding": [0.0] * self.dimension})
        return _Response({"data": items})


def _make_embedder(provider, monkeypatch, batch_size=None):
    monkeypatch.setattr(urllib.request, "urlopen", provider.urlopen)
    return APIEmbedder(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model="text-embedding-3-small",
        batch_size=batch_size,
        # 配置维度必须与 mock 响应一致：P2-67 禁止用响应长度静默覆写
        dimension=getattr(provider, "dimension", 4) or 4,
    )


class TestEmptyResultsAreNotSilentSuccess:
    def test_empty_input_returns_empty_and_never_calls_provider(self, monkeypatch):
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch, batch_size=4)

        assert embedder.embed([]) == []
        assert provider.calls == []

    def test_single_input_with_empty_data_raises(self, monkeypatch):
        provider = _FakeProvider(vector_count=0)
        embedder = _make_embedder(provider, monkeypatch, batch_size=8)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["hello"])

    def test_multi_input_with_empty_data_raises(self, monkeypatch):
        provider = _FakeProvider(vector_count=0)
        embedder = _make_embedder(provider, monkeypatch, batch_size=8)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["a", "b", "c"])

    @pytest.mark.parametrize("vector_count", [1, 3])
    def test_fewer_vectors_than_inputs_raises(self, monkeypatch, vector_count):
        provider = _FakeProvider(vector_count=vector_count)
        embedder = _make_embedder(provider, monkeypatch, batch_size=8)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["t0", "t1"])

    def test_more_vectors_than_inputs_raises(self, monkeypatch):
        provider = _FakeProvider(vector_count=5)
        embedder = _make_embedder(provider, monkeypatch, batch_size=8)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["t0", "t1"])

    def test_one_batch_empty_poisons_whole_call(self, monkeypatch):
        """第 2 批返回空 → 整体抛出，绝不返回"少了一半"的向量列表。"""
        provider = _FakeProvider(empty_batch_index=1)
        embedder = _make_embedder(provider, monkeypatch, batch_size=2)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["t0", "t1", "t2", "t3"])
        assert len(provider.calls) == 2

    def test_missing_data_key_raises(self, monkeypatch):
        provider = _FakeProvider(missing_data=True)
        embedder = _make_embedder(provider, monkeypatch)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["t0"])

    def test_malformed_item_raises(self, monkeypatch):
        provider = _FakeProvider(malformed_item=True)
        embedder = _make_embedder(provider, monkeypatch)

        with pytest.raises(EmbeddingProviderResponseError):
            embedder.embed(["t0"])

    def test_provider_error_still_propagates(self, monkeypatch):
        provider = _FakeProvider(raise_on_call=1)
        embedder = _make_embedder(provider, monkeypatch, batch_size=2)

        with pytest.raises(TimeoutError):
            embedder.embed(["t0", "t1", "t2", "t3"])
        assert len(provider.calls) == 2


class TestBatching:
    @pytest.mark.parametrize("count,batch_size,expected", [
        (1, 4, [1]),
        (3, 4, [3]),
        (4, 4, [4]),
        (5, 4, [4, 1]),
        (9, 4, [4, 4, 1]),
        (150, 64, [64, 64, 22]),
    ])
    def test_batch_sizes_and_order(self, monkeypatch, count, batch_size, expected):
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch, batch_size=batch_size)

        texts = [f"t{i}" for i in range(count)]
        vectors = embedder.embed(texts)

        assert [len(call) for call in provider.calls] == expected
        assert len(vectors) == count
        # 顺序必须与输入严格一致
        assert [v[0] for v in vectors] == [float(i) for i in range(count)]
        # 每批输入切片连续
        flat = [text for call in provider.calls for text in call]
        assert flat == texts

    def test_batch_size_from_env(self, monkeypatch):
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BATCH_SIZE", "2")
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch)

        assert embedder.batch_size == 2
        embedder.embed(["t0", "t1", "t2"])
        assert [len(call) for call in provider.calls] == [2, 1]

    @pytest.mark.parametrize("raw", ["abc", "", "  ", "3.5"])
    def test_invalid_batch_size_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BATCH_SIZE", raw)
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch)

        assert embedder.batch_size == APIEmbedder.DEFAULT_BATCH_SIZE

    @pytest.mark.parametrize("raw", ["0", "-3"])
    def test_non_positive_batch_size_is_clamped_to_one(self, monkeypatch, raw):
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BATCH_SIZE", raw)
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch)

        assert embedder.batch_size == 1

    def test_explicit_batch_size_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OFFICE_AGENT_EMBEDDING_BATCH_SIZE", "2")
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch, batch_size=3)

        assert embedder.batch_size == 3


class TestExistingValidationNotRegressed:
    def test_empty_vector_raises_invalid_embedding_vector(self, monkeypatch):
        provider = _FakeProvider(empty_vector=True)
        embedder = _make_embedder(provider, monkeypatch, batch_size=4)

        with pytest.raises(InvalidEmbeddingVectorError):
            embedder.embed(["t0"])

    def test_normal_response_returns_finite_vectors(self, monkeypatch):
        provider = _FakeProvider(dimension=3)
        embedder = _make_embedder(provider, monkeypatch, batch_size=4)

        vectors = embedder.embed(["t0", "t1"])
        assert vectors == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        assert embedder.dimension == 3

    def test_embed_query_uses_single_input_batch(self, monkeypatch):
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch, batch_size=4)

        vector = embedder.embed_query("中文语义检索")
        assert len(provider.calls) == 1
        assert provider.calls[0] == ["中文语义检索"]
        assert len(vector) == 4

    def test_model_metadata_unchanged(self, monkeypatch):
        provider = _FakeProvider()
        embedder = _make_embedder(provider, monkeypatch)
        assert embedder.model_id == "api:text-embedding-3-small"
        assert embedder.semantic is True


class TestProviderErrorHierarchy:
    def test_provider_response_error_is_embedding_error(self):
        from office_agent.knowledge_base.embeddings import EmbeddingError

        assert issubclass(EmbeddingProviderResponseError, EmbeddingError)

    def test_exported_from_package(self):
        from office_agent.knowledge_base import EmbeddingProviderResponseError as Exported

        assert Exported is EmbeddingProviderResponseError
