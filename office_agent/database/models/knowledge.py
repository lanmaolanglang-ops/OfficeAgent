"""知识库模型"""
import uuid
from sqlalchemy import String, Text, Integer, Float
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin


def _uuid():
    return f"kb_{uuid.uuid4().hex[:12]}"


class Knowledge(Base, TimestampMixin):
    """知识条目表（RAG检索用）"""
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[str] = mapped_column(Text, nullable=True)
    # JSON array of floats, 向量序列化存储
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(256), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=True)
    # document/web/manual/feedback
    category: Mapped[str] = mapped_column(String(64), nullable=True, index=True)
    tags: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=1)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)

    def __repr__(self):
        return f"<Knowledge {self.title[:30]}...>"
