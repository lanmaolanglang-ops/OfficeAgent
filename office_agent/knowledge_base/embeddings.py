"""
Embedding 模块 - 文本向量化

默认使用纯 Python 实现的 TF-IDF（无需外部依赖），
同时预留外部 Embedding API 接口（OpenAI/豆包等）。
"""
import re
import math
from typing import List, Dict, Optional, Any
from collections import Counter
from abc import ABC, abstractmethod


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
        return result

    def embed_query(self, text: str) -> List[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        return self._dimension


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
