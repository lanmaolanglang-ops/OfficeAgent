"""
Embedding 模块 - 文本向量化

默认使用纯 Python 实现的 TF-IDF（无需外部依赖），
同时预留外部 Embedding API 接口（OpenAI/豆包等）。
"""
import os
import re
import math
import hashlib
from typing import List, Dict, Optional
from collections import Counter
from abc import ABC, abstractmethod


class EmbeddingError(RuntimeError):
    """Base class for embedding backend failures."""


class EmbeddingBackendUnavailableError(EmbeddingError):
    """No semantic backend can be initialized from the current environment."""


class InvalidEmbeddingVectorError(EmbeddingError):
    """An embedding vector is empty, non-finite, or has the wrong dimension."""


class BaseEmbedder(ABC):
    """Embedding 基类"""

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """将文本列表转换为向量列表"""
        pass

    @abstractmethod
    def embed_query(self, text: str) -> List[float]:
        """将查询转换为向量"""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        pass

    @property
    def model_id(self) -> str:
        return "base-embedder"

    @property
    def version(self) -> str:
        return "1"

    @property
    def semantic(self) -> bool:
        return False

    def embed_text(self, text: str) -> List[float]:
        return self.embed_query(text)

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return self.embed(texts)


def _is_finite_vector(vector: List[float], dimension: Optional[int] = None) -> bool:
    if not vector:
        return False
    if dimension is not None and len(vector) != dimension:
        return False
    return all(isinstance(value, (int, float)) and math.isfinite(float(value))
               for value in vector)


class HashingEmbedder(BaseEmbedder):
    """Stateless, deterministic local embedder for persisted task embeddings.

    Unlike TF-IDF, the vector space does not depend on an in-memory fitted
    vocabulary, so vectors written by one worker remain queryable after restart.
    """

    def __init__(self, dimensions: int = 512):
        if dimensions < 32:
            raise ValueError("dimensions must be at least 32")
        self._dimension = dimensions

    @property
    def model_id(self) -> str:
        return "hashing-512-v1"

    @property
    def version(self) -> str:
        return "1"

    @property
    def semantic(self) -> bool:
        return False

    @staticmethod
    def _tokens(text: str) -> List[str]:
        lowered = (text or "").lower()
        words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
        # CJK single characters alone are weak signals; add adjacent bigrams.
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
        words.extend(cjk[i:i + 2] for i in range(max(0, len(cjk) - 1)))
        return words

    def _vectorize(self, text: str) -> List[float]:
        vector = [0.0] * self._dimension
        for token, count in Counter(self._tokens(text)).items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self._dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._vectorize(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vectorize(text)

    @property
    def dimension(self) -> int:
        return self._dimension


class TfidfEmbedder(BaseEmbedder):
    """
    TF-IDF 向量化器（纯 Python 实现）

    支持中文（按字+bigram）和英文（按词），无需 jieba 或 sklearn。
    """

    def __init__(self, max_features: int = 8000, min_df: int = 1):
        self.max_features = max_features
        self.min_df = min_df
        self.vocabulary: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self._fitted = False
        self._doc_count = 0

    def fit(self, texts: List[str]):
        """在语料上训练"""
        self._doc_count = len(texts)

        # 统计文档频率
        df = Counter()
        for text in texts:
            tokens = self._tokenize(text)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                df[t] += 1

        # 过滤低频词
        valid_tokens = [
            (t, count) for t, count in df.most_common()
            if count >= self.min_df
        ][:self.max_features]

        # 构建词汇表
        self.vocabulary = {t: i for i, (t, _) in enumerate(valid_tokens)}

        # 计算 IDF
        self.idf = {}
        for t, idx in self.vocabulary.items():
            self.idf[t] = math.log((self._doc_count + 1) / (df[t] + 1)) + 1

        self._fitted = True
        return self

    def embed(self, texts: List[str]) -> List[List[float]]:
        """转换为 TF-IDF 向量"""
        if not self._fitted:
            self.fit(texts)

        vectors = []
        for text in texts:
            vec = self._vectorize(text)
            vectors.append(vec)
        return vectors

    def embed_query(self, text: str) -> List[float]:
        """查询向量化（使用已训练的词汇表）"""
        if not self._fitted:
            return [0.0] * self.dimension
        return self._vectorize(text)

    def _vectorize(self, text: str) -> List[float]:
        """将单个文本转换为 TF-IDF 向量"""
        tokens = self._tokenize(text)
        tf = Counter(tokens)

        vec = [0.0] * len(self.vocabulary)
        for token, count in tf.items():
            if token in self.vocabulary:
                idx = self.vocabulary[token]
                tf_val = count / len(tokens) if tokens else 0
                idf_val = self.idf.get(token, 1.0)
                vec[idx] = tf_val * idf_val

        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec

    def _tokenize(self, text: str) -> List[str]:
        """
        分词：中文按字 + bigram，英文按词
        """
        tokens = []

        # 提取中文字符
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        # 单字
        tokens.extend(chinese_chars)
        # Bigram（相邻两字）
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])

        # 提取英文单词（小写）
        english_words = re.findall(r'[a-zA-Z]+', text.lower())
        tokens.extend(english_words)

        # 提取数字
        numbers = re.findall(r'\d+', text)
        tokens.extend(numbers)

        return tokens

    @property
    def dimension(self) -> int:
        return len(self.vocabulary)

    def get_keywords(self, text: str, top_k: int = 10) -> List[str]:
        """提取文本关键词"""
        tokens = self._tokenize(text)
        tf = Counter(tokens)
        scores = {}
        for token, count in tf.items():
            if token in self.idf:
                scores[token] = count * self.idf[token]
            elif len(token) > 1:
                scores[token] = count
        sorted_tokens = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [t for t, _ in sorted_tokens[:top_k]]


class KeywordEmbedder(BaseEmbedder):
    """
    关键词匹配向量化（更轻量，适合小知识库）
    基于关键词集合的 Jaccard/余弦相似度
    """

    def __init__(self):
        self.keywords: List[str] = []
        self._keyword_set: set = set()

    def fit(self, texts: List[str]):
        """从语料中提取关键词"""
        word_freq = Counter()
        for text in texts:
            tokens = self._tokenize(text)
            unique = set(tokens)
            for t in unique:
                word_freq[t] += 1

        # 取出现至少1次的词
        self.keywords = [w for w, c in word_freq.most_common(5000) if c >= 1]
        self._keyword_set = set(self.keywords)
        return self

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not self.keywords:
            self.fit(texts)
        return [self._vectorize(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        if not self.keywords:
            return []
        return self._vectorize(text)

    def _vectorize(self, text: str) -> List[float]:
        tokens = set(self._tokenize(text))
        vec = []
        for kw in self.keywords:
            vec.append(1.0 if kw in tokens else 0.0)
        return vec

    def _tokenize(self, text: str) -> List[str]:
        tokens = []
        chinese = re.findall(r'[\u4e00-\u9fff]', text)
        tokens.extend(chinese)
        for i in range(len(chinese) - 1):
            tokens.append(chinese[i] + chinese[i + 1])
        tokens.extend(re.findall(r'[a-zA-Z]+', text.lower()))
        return tokens

    @property
    def dimension(self) -> int:
        return len(self.keywords)


class APIEmbedder(BaseEmbedder):
    """
    外部 API Embedding（OpenAI/豆包等兼容接口）

    使用 urllib，不依赖 requests。
    """

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "text-embedding-3-small", dimension: int = 1536):
        if not api_key:
            raise EmbeddingBackendUnavailableError(
                "API embedding backend requires an API key",
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._dimension = dimension

    def embed(self, texts: List[str]) -> List[List[float]]:
        import json
        import urllib.request

        url = f"{self.base_url}/embeddings"
        payload = json.dumps({
            "model": self.model,
            "input": texts,
        }).encode("utf-8")

        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        result = [item["embedding"] for item in data["data"]]
        if result:
            self._dimension = len(result[0])
        for vector in result:
            if not _is_finite_vector(vector, self._dimension):
                raise InvalidEmbeddingVectorError(
                    "API embedding returned an empty or non-finite vector",
                )
        return result

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str:
        return f"api:{self.model}"

    @property
    def version(self) -> str:
        return "1"

    @property
    def semantic(self) -> bool:
        return True


class LocalSemanticEmbedder(BaseEmbedder):
    """Optional local sentence-transformer backend for offline semantic search.

    The dependency is intentionally lazy and optional. When the ``semantic``
    extra is not installed, construction raises
    :class:`EmbeddingBackendUnavailableError`; the embedding factory can then
    fall back to a configured API backend. It never silently degrades to the
    legacy hashing vector space.
    """

    DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(self, model_name: str = None, cache_dir: str = None):
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise EmbeddingBackendUnavailableError(
                "Local semantic backend requires the 'semantic' extra",
            ) from exc
        self.model_name = model_name or self.DEFAULT_MODEL
        self._model = SentenceTransformer(self.model_name, cache_folder=cache_dir)

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).tolist()
        for vector in vectors:
            if not _is_finite_vector(vector, self.dimension):
                raise InvalidEmbeddingVectorError(
                    "Local semantic backend returned an invalid vector",
                )
        return vectors

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    @property
    def model_id(self) -> str:
        return f"local:{self.model_name}"

    @property
    def version(self) -> str:
        return "sentence-transformers"

    @property
    def semantic(self) -> bool:
        return True


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_semantic_embedder(backend: str = None,
                             model: str = None,
                             api_key: str = None,
                             base_url: str = None) -> BaseEmbedder:
    """Create the best available true semantic embedder.

    Resolution order:
    1. Explicit ``local`` backend if the optional sentence-transformers
       dependency is installed.
    2. Explicit ``api`` backend using an OpenAI-compatible endpoint.
    3. ``auto`` tries local first, then API, and raises a clear error when
       neither backend is usable.
    """
    backend = (backend or os.environ.get("OFFICE_AGENT_EMBEDDING_BACKEND", "auto")).lower()
    model = model or os.environ.get("OFFICE_AGENT_EMBEDDING_MODEL")
    api_key = api_key or os.environ.get(
        "OFFICE_AGENT_EMBEDDING_API_KEY",
        os.environ.get("OPENAI_API_KEY", ""),
    )
    base_url = base_url or os.environ.get(
        "OFFICE_AGENT_EMBEDDING_BASE_URL",
        "https://api.openai.com/v1",
    )

    def _local():
        return LocalSemanticEmbedder(model_name=model)

    def _api():
        return APIEmbedder(
            api_key=api_key,
            base_url=base_url,
            model=model or "text-embedding-3-small",
        )

    def _persisted_api():
        from .embedding_config import EmbeddingConfigManager

        manager = EmbeddingConfigManager()
        if not manager.is_configured():
            return None
        config = manager.get_config()
        return APIEmbedder(
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
        )

    if backend == "local":
        return _local()
    if backend == "api":
        return _api() if api_key else (_persisted_api() or _api())
    if backend != "auto":
        raise EmbeddingBackendUnavailableError(
            f"Unknown embedding backend: {backend}",
        )

    try:
        return _local()
    except EmbeddingBackendUnavailableError:
        if api_key:
            return _api()
        persisted = _persisted_api()
        if persisted is not None:
            return persisted
    raise EmbeddingBackendUnavailableError(
        "No semantic embedding backend is available. Install the 'semantic' "
        "extra or configure an OpenAI-compatible embedding provider.",
    )


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def batch_cosine_similarity(query: List[float],
                            corpus: List[List[float]]) -> List[float]:
    """批量计算余弦相似度"""
    return [cosine_similarity(query, doc) for doc in corpus]
