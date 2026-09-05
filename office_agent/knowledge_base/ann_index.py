"""Persistent ANN acceleration layer for RAG semantic retrieval.

The database remains the source of truth.  This module only maintains a
rebuildable in-process acceleration index over already-embedded ``Knowledge``
rows.  It never stores text, categories, or any second copy of the knowledge
base, and every method fails closed by telling the caller to fall back to the
existing exact cosine path when the index is missing, corrupted, stale, or
unavailable.

The selected backend is ``usearch``:

* Windows ``cp312``/``cp314`` wheels are available on PyPI, so it can be
  installed both in development and in the frozen desktop build.
* It is a small, embedded, CPU-first library with persistent binary files and
  stable 64-bit key identities (not list positions).
* ``metric="cos"`` directly matches the existing semantic similarity contract.

The on-disk layout is intentionally isolated under
``get_data_root() / "rag_index"`` and never written into the source tree.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..persistence import atomic_write_bytes, atomic_write_json
from ..runtime_config import get_data_root

logger = logging.getLogger("office_agent.knowledge_base.ann_index")

ANN_BACKEND = "usearch"
ANN_INDEX_VERSION = 1
#: Named small-dataset threshold.  Below this size the exact brute-force path
#: is both simpler and faster; ANN is used only when the candidate set is large
#: enough for index construction/search to pay off.
ANN_MIN_CANDIDATES = 1000
#: How many candidates ANN returns for each requested final slot.  This
#: over-fetch factor leaves headroom for metadata filtering and approximation
#: noise before the final deterministic lexical/cosine reconciliation.
ANN_SEARCH_OVERFETCH = 4

_INDEX_FILENAME = "ann.usearch"
_MANIFEST_FILENAME = "manifest.json"
_ID_MAP_FILENAME = "id_map.json"


class ANNIndexError(RuntimeError):
    """Base class for ANN index failures."""


class ANNIndexUnavailableError(ANNIndexError):
    """The ANN backend cannot be imported from the current environment."""


class ANNIndexCorruptedError(ANNIndexError):
    """The persisted ANN index cannot be trusted and must be rebuilt."""


def get_default_ann_index_dir() -> Path:
    """Return the authoritative ANN index directory without creating it."""
    return get_data_root() / "rag_index"


def _import_usearch():
    try:
        from usearch.index import Index
        return Index
    except Exception as exc:  # pragma: no cover - import guard only
        raise ANNIndexUnavailableError(
            "ANN acceleration requires the 'usearch' package",
        ) from exc


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _vector_array(vector: Sequence[float]):
    import numpy as np

    return np.asarray(vector, dtype=np.float32)


class KnowledgeVectorIndex:
    """Thread-safe persistent usearch index with stable string-ID mapping."""

    def __init__(self, directory: Optional[str | Path] = None):
        self.directory = Path(directory) if directory else get_default_ann_index_dir()
        self.index_path = self.directory / _INDEX_FILENAME
        self.manifest_path = self.directory / _MANIFEST_FILENAME
        self.id_map_path = self.directory / _ID_MAP_FILENAME
        self._lock = threading.RLock()
        self._index = None
        self._manifest: dict = {}
        self._id_to_key: dict[str, int] = {}
        self._key_to_id: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Read / state helpers
    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._index is not None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._index) if self._index is not None else 0

    @property
    def indexed_ids(self) -> set[str]:
        with self._lock:
            return set(self._id_to_key)

    def manifest_matches(self, model_id: str, version: str,
                         dimension: int) -> bool:
        manifest = _read_json(self.manifest_path)
        if not isinstance(manifest, dict):
            return False
        return (
            manifest.get("backend") == ANN_BACKEND
            and manifest.get("index_version") == ANN_INDEX_VERSION
            and manifest.get("embedding_model") == model_id
            and manifest.get("embedding_version") == version
            and manifest.get("dimension") == dimension
        )

    def load(self, model_id: str, version: str,
             dimension: int) -> bool:
        """Load a persisted index only when its manifest is trustworthy."""
        with self._lock:
            self._index = None
            self._manifest = {}
            self._id_to_key = {}
            self._key_to_id = {}

            manifest = _read_json(self.manifest_path)
            id_map = _read_json(self.id_map_path)
            if not isinstance(manifest, dict) or not isinstance(id_map, dict):
                logger.info("ANN 索引清单或 ID 映射不存在: %s", self.directory)
                return False
            if not self._manifest_dict_matches(manifest, model_id, version, dimension):
                logger.info(
                    "ANN 索引清单不匹配，忽略缓存: model=%s dimension=%s",
                    model_id, dimension,
                )
                return False
            try:
                Index = _import_usearch()
                index = Index(ndim=dimension, metric="cos", dtype="f32")
                # usearch's native ``load(path)`` is not reliably Unicode-safe
                # on Windows paths; load bytes through Python file I/O instead.
                index.load(self.index_path.read_bytes())
            except ANNIndexUnavailableError:
                raise
            except Exception as exc:
                logger.warning("ANN 索引加载失败，将按损坏处理: %s", exc)
                raise ANNIndexCorruptedError(
                    "Persisted ANN index could not be loaded",
                ) from exc

            id_to_key = {
                str(record_id): int(key)
                for record_id, key in id_map.items()
                if isinstance(key, int)
            }
            if len(index) != len(id_to_key) or index.ndim != dimension:
                logger.warning(
                    "ANN 索引与 ID 映射不一致: index=%s map=%s dim=%s",
                    len(index), len(id_to_key), index.ndim,
                )
                raise ANNIndexCorruptedError(
                    "Persisted ANN index is inconsistent with its manifest",
                )

            self._index = index
            self._manifest = manifest
            self._id_to_key = id_to_key
            self._key_to_id = {key: record_id for record_id, key in id_to_key.items()}
            return True

    @staticmethod
    def _manifest_dict_matches(manifest: dict, model_id: str,
                               version: str, dimension: int) -> bool:
        return (
            manifest.get("backend") == ANN_BACKEND
            and manifest.get("index_version") == ANN_INDEX_VERSION
            and manifest.get("embedding_model") == model_id
            and manifest.get("embedding_version") == version
            and manifest.get("dimension") == dimension
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def rebuild(self, records: Iterable[tuple[str, Sequence[float]]],
                model_id: str, version: str, dimension: int) -> int:
        """Create a fresh index from ``(knowledge_id, vector)`` pairs."""
        Index = _import_usearch()
        records = list(records)
        with self._lock:
            index = Index(ndim=dimension, metric="cos", dtype="f32")
            id_to_key: dict[str, int] = {}
            key_to_id: dict[int, str] = {}
            next_key = 1
            for record_id, vector in records:
                if not record_id:
                    continue
                if record_id in id_to_key:
                    key = id_to_key[record_id]
                else:
                    key = next_key
                    next_key += 1
                    id_to_key[record_id] = key
                    key_to_id[key] = record_id
                index.add(key, _vector_array(vector))

            self._index = index
            self._id_to_key = id_to_key
            self._key_to_id = key_to_id
            self._manifest = {
                "backend": ANN_BACKEND,
                "index_version": ANN_INDEX_VERSION,
                "embedding_model": model_id,
                "embedding_version": version,
                "dimension": dimension,
                "item_count": len(id_to_key),
            }
            self._save_locked()
            logger.info(
                "ANN 索引已重建: %s records model=%s dim=%s",
                len(id_to_key), model_id, dimension,
            )
            return len(id_to_key)

    def upsert(self, records: Iterable[tuple[str, Sequence[float]]],
               model_id: str, version: str, dimension: int) -> int:
        """Add or replace records in the currently loaded index."""
        if self._index is None or not self._manifest_dict_matches(
                self._manifest, model_id, version, dimension):
            return self.rebuild(records, model_id, version, dimension)

        records = list(records)
        with self._lock:
            changed = False
            next_key = max(self._id_to_key.values(), default=0) + 1
            for record_id, vector in records:
                if not record_id:
                    continue
                changed = True
                key = self._id_to_key.get(record_id)
                if key is None:
                    is_new = True
                    key = next_key
                    next_key += 1
                    self._id_to_key[record_id] = key
                    self._key_to_id[key] = record_id
                else:
                    is_new = False
                if not is_new:
                    self._index.remove(key)
                self._index.add(key, _vector_array(vector))
            if changed:
                self._manifest["item_count"] = len(self._id_to_key)
                self._save_locked()
            return len(records)

    def _save_locked(self) -> None:
        """Persist index, manifest, and ID map while holding the lock."""
        self.directory.mkdir(parents=True, exist_ok=True)
        # Binary index first, then metadata.  If a crash lands between the
        # files, ``load`` detects the count/id-map mismatch and rebuilds.
        index_bytes = self._index.save()
        atomic_write_bytes(self.index_path, bytes(index_bytes))
        atomic_write_json(self.manifest_path, self._manifest, indent=2)
        atomic_write_json(self.id_map_path, self._id_to_key, indent=2)

    def missing_ids(self, ids: Iterable[str]) -> list[str]:
        with self._lock:
            return [record_id for record_id in ids
                    if record_id not in self._id_to_key]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(self, query_vector: Sequence[float], top_k: int,
               *, overfetch: int = ANN_SEARCH_OVERFETCH) -> list[tuple[str, float]]:
        """Return ``(knowledge_id, cosine_similarity)`` ordered best-first."""
        if self._index is None:
            raise ANNIndexUnavailableError("ANN index is not loaded")
        if top_k <= 0 or not query_vector:
            return []
        if len(query_vector) != self._index.ndim:
            raise ANNIndexError(
                "Query vector dimension does not match ANN index",
            )
        with self._lock:
            count = min(max(top_k * overfetch, 1), len(self._index))
            if count <= 0:
                return []
            matches = self._index.search(_vector_array(query_vector), count)
            results: list[tuple[str, float]] = []
            for key, distance in zip(matches.keys, matches.distances):
                record_id = self._key_to_id.get(int(key))
                if record_id is None:
                    logger.warning("ANN 返回未知内部 key: %s", key)
                    continue
                # usearch cosine distance is ``1 - cosine_similarity``.
                results.append((record_id, round(float(1.0 - distance), 6)))
            return results
