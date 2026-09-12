"""
Office Knowledge Base - 企业级知识库系统

让 Agent 能够检索办公规范、模板规则和专业知识。
支持导入 Word/PPT/Excel 规范文档，自动解析、切片、向量化、语义检索。
"""
import os
import json
import logging
import threading
from functools import wraps
from pathlib import Path
from typing import Optional, List, Dict, Any

from .models import (
    KnowledgeDocument, KnowledgeChunk, KnowledgeContext,
)
from .document_parser import DocumentParser
from .text_chunker import TextChunker, ChunkConfig
from .embeddings import TfidfEmbedder, BaseEmbedder
from .vector_store import VectorStore
from ..runtime_config import get_data_root

logger = logging.getLogger("office_agent.knowledge_base")


def _atomic_json_write(path: str, data: Any, *, indent=None) -> None:
    """在目标目录内写临时文件并原子替换，避免中断留下半截 JSON。

    实现统一委托给 :mod:`office_agent.persistence`，避免各模块各自维护一套
    「临时文件 + fsync + replace」且口径漂移。
    """
    from ..persistence import atomic_write_json

    atomic_write_json(path, data, indent=indent, ensure_ascii=False)


def _default_kb_storage_dir() -> str:
    return str(get_data_root() / "kb_data")


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class OfficeKnowledgeBase:
    """
    企业级知识库

    使用方式:
        kb = OfficeKnowledgeBase(storage_dir="./kb_data")

        # 导入规范文档
        kb.import_document("论文格式要求.docx", doc_type="word_spec")
        kb.import_document("商务PPT规范.pdf", doc_type="ppt_spec")
        kb.import_document("财务计算规则.xlsx", doc_type="excel_rule")

        # 直接添加文本规则
        kb.add_text(
            "正文使用宋体小四号字，1.5倍行距，首行缩进2字符",
            title="Word正文格式",
            doc_type="word_spec"
        )

        # 检索
        ctx = kb.search("正文应该用什么字体", doc_type="word_spec")
        print(ctx.to_text())
    """

    def __init__(self, storage_dir: str | None = None,
                 embedder: Optional[BaseEmbedder] = None,
                 chunk_config: Optional[ChunkConfig] = None):
        storage_dir = storage_dir or _default_kb_storage_dir()
        self.storage_dir = storage_dir
        self.parser = DocumentParser()
        self.chunker = TextChunker(chunk_config or ChunkConfig())
        self.embedder = embedder or TfidfEmbedder()
        self.store = VectorStore(self.embedder)
        self.documents: Dict[str, KnowledgeDocument] = {}
        self._lock = threading.RLock()

        os.makedirs(storage_dir, exist_ok=True)

        # 自动加载已有数据
        self._auto_load()

    # ==================== 导入文档 ====================

    @_locked
    def import_document(self, file_path: str,
                        doc_type: str = "",
                        tags: List[str] | None = None,
                        title: str = "") -> KnowledgeDocument:
        """
        导入规范文档

        Args:
            file_path: 文件路径
            doc_type: 文档类型 (word_spec/ppt_spec/excel_rule/general)
            tags: 标签
            title: 自定义标题
        """
        # 解析文档
        parsed = self.parser.parse(file_path, doc_type=doc_type)

        if not title:
            title = parsed.title or os.path.basename(file_path)

        # 创建文档对象
        doc = KnowledgeDocument(
            title=title,
            doc_type=parsed.doc_type or doc_type or "general",
            source_path=Path(file_path).name,
            content=parsed.full_text,
            tags=tags or [],
        )

        # 切片
        chunks = self.chunker.chunk_document(parsed, doc_id=doc.id)

        # 给每个 chunk 添加文档元数据
        for chunk in chunks:
            chunk.metadata["doc_type"] = doc.doc_type
            chunk.metadata["tags"] = tags or []
            chunk.metadata["source"] = Path(file_path).name

        doc.chunks = chunks
        doc.chunk_count = len(chunks)

        # 添加到向量存储
        self.store.add_chunks(chunks)
        self.documents[doc.id] = doc

        # 自动保存
        try:
            self.save()
        except Exception:
            self.documents.pop(doc.id, None)
            self._rebuild_index()
            raise

        return doc

    @_locked
    def add_text(self, text: str, title: str = "",
                 doc_type: str = "general",
                 tags: List[str] | None = None) -> KnowledgeDocument:
        """
        直接添加文本规则

        Args:
            text: 规则文本
            title: 规则标题
            doc_type: 文档类型
            tags: 标签
        """
        parsed = self.parser.parse_text(text, title=title or "直接添加", doc_type=doc_type)

        doc = KnowledgeDocument(
            title=title or "直接添加",
            doc_type=doc_type,
            content=text,
            tags=tags or [],
        )

        chunks = self.chunker.chunk_document(parsed, doc_id=doc.id)
        for chunk in chunks:
            chunk.metadata["doc_type"] = doc_type
            chunk.metadata["tags"] = tags or []

        doc.chunks = chunks
        doc.chunk_count = len(chunks)

        self.store.add_chunks(chunks)
        self.documents[doc.id] = doc
        try:
            self.save()
        except Exception:
            self.documents.pop(doc.id, None)
            self._rebuild_index()
            raise

        return doc

    @_locked
    def import_directory(self, dir_path: str,
                         doc_type: str = "") -> List[KnowledgeDocument]:
        """导入目录下所有支持的文档"""
        supported = {".docx", ".pptx", ".xlsx",
                     ".csv", ".txt", ".md", ".pdf"}
        docs = []

        for fname in os.listdir(dir_path):
            ext = os.path.splitext(fname)[1].lower()
            if ext in supported:
                fpath = os.path.join(dir_path, fname)
                try:
                    # 根据文件名推断类型
                    inferred_type = doc_type or self._infer_type(fname)
                    doc = self.import_document(fpath, doc_type=inferred_type)
                    docs.append(doc)
                except Exception as e:
                    # 单文档失败不阻断目录导入：跳过并保留其余部分结果
                    logger.warning("导入失败 %s: %s", fname, e, exc_info=True)

        return docs

    def _infer_type(self, filename: str) -> str:
        """根据文件名推断文档类型"""
        name = filename.lower()
        if any(k in name for k in ["论文", "公文", "报告", "word", "格式", "排版"]):
            return "word_spec"
        if any(k in name for k in ["ppt", "演示", "幻灯片", "答辩", "发布"]):
            return "ppt_spec"
        if any(k in name for k in ["excel", "财务", "销售", "计算", "指标", "规则"]):
            return "excel_rule"
        return "general"

    # ==================== 检索 ====================

    @_locked
    def search(self, query: str, top_k: int = 5,
               doc_type: str = "",
               min_score: float = 0.05) -> KnowledgeContext:
        """
        语义检索

        Args:
            query: 查询文本
            top_k: 返回前 K 个结果
            doc_type: 过滤文档类型
            min_score: 最低分数
        """
        doc_types = [doc_type] if doc_type else None
        results = self.store.search(
            query, top_k=top_k,
            min_score=min_score,
            doc_types=doc_types,
        )

        ctx = KnowledgeContext(
            query=query,
            results=results,
            doc_types=[doc_type] if doc_type else [],
            total_results=len(results),
        )
        return ctx

    def search_word_specs(self, query: str, top_k: int = 5) -> KnowledgeContext:
        """检索 Word 规范"""
        return self.search(query, top_k=top_k, doc_type="word_spec")

    def search_ppt_specs(self, query: str, top_k: int = 5) -> KnowledgeContext:
        """检索 PPT 规范"""
        return self.search(query, top_k=top_k, doc_type="ppt_spec")

    def search_excel_rules(self, query: str, top_k: int = 5) -> KnowledgeContext:
        """检索 Excel 业务规则"""
        return self.search(query, top_k=top_k, doc_type="excel_rule")

    # ==================== 管理 ====================

    @_locked
    def list_documents(self) -> List[Dict[str, Any]]:
        """列出所有文档"""
        return [doc.to_dict() for doc in self.documents.values()]

    @_locked
    def remove_document(self, doc_id: str) -> bool:
        """删除文档并增量移除其 lexical postings。"""
        if doc_id in self.documents:
            removed = self.documents.pop(doc_id)
            self.store.remove_chunks([chunk.id for chunk in removed.chunks])
            try:
                self.save()
            except Exception:
                self.documents[doc_id] = removed
                self._rebuild_index()
                raise
            return True
        return False

    @_locked
    def update_text(self, doc_id: str, text: str, title: str = "",
                    doc_type: str = "", tags: List[str] | None = None) -> Optional[KnowledgeDocument]:
        """按 doc_id 增量替换文档文本，旧 postings 立即失效。"""
        old_doc = self.documents.get(doc_id)
        if old_doc is None:
            return None

        resolved_title = title or old_doc.title
        resolved_type = doc_type or old_doc.doc_type
        resolved_tags = tags if tags is not None else old_doc.tags
        parsed = self.parser.parse_text(
            text, title=resolved_title, doc_type=resolved_type,
        )
        new_doc = KnowledgeDocument(
            id=doc_id,
            title=resolved_title,
            doc_type=resolved_type,
            source_path=old_doc.source_path,
            content=text,
            tags=resolved_tags,
        )
        chunks = self.chunker.chunk_document(parsed, doc_id=doc_id)
        for chunk in chunks:
            chunk.metadata["doc_type"] = resolved_type
            chunk.metadata["tags"] = resolved_tags
        new_doc.chunks = chunks
        new_doc.chunk_count = len(chunks)

        self.documents.pop(doc_id, None)
        self.store.remove_chunks([chunk.id for chunk in old_doc.chunks])
        self.store.add_chunks(chunks)
        self.documents[doc_id] = new_doc
        try:
            self.save()
        except Exception:
            self.documents[doc_id] = old_doc
            self._rebuild_index()
            raise
        return new_doc

    @_locked
    def _rebuild_index(self):
        """重建索引"""
        self.store.clear()
        all_chunks = []
        for doc in self.documents.values():
            for chunk in doc.chunks:
                all_chunks.append(chunk)
        if all_chunks:
            self.store.add_chunks(all_chunks)

    @_locked
    def clear(self):
        """清空知识库"""
        self.documents.clear()
        self.store.clear()
        self.save()

    @property
    @_locked
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        type_counts: dict[str, int] = {}
        for doc in self.documents.values():
            t = doc.doc_type
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "total_documents": len(self.documents),
            "total_chunks": self.store.count,
            "by_type": type_counts,
            "storage_dir": self.storage_dir,
        }

    # ==================== 持久化 ====================

    @_locked
    def save(self):
        """保存到磁盘"""
        if hasattr(self.store, "_refresh_embedder_stats"):
            self.store._refresh_embedder_stats()
        # 保存文档元数据
        docs_data = {
            doc_id: {
                "id": doc.id,
                "title": doc.title,
                "doc_type": doc.doc_type,
                "source_path": doc.source_path,
                "tags": doc.tags,
                "metadata": doc.metadata,
                "created_at": doc.created_at,
                "chunk_count": doc.chunk_count,
            }
            for doc_id, doc in self.documents.items()
        }

        docs_path = os.path.join(self.storage_dir, "documents.json")
        _atomic_json_write(docs_path, docs_data, indent=2)

        # 保存 chunks（含向量）
        chunks_data = []
        embeddings_by_chunk_id = {
            stored.chunk.id: list(stored.embedding)
            for stored in self.store._chunks
        }
        for doc in self.documents.values():
            for chunk in doc.chunks:
                cd = chunk.to_dict(include_embedding=False)
                cd["embedding"] = embeddings_by_chunk_id.get(chunk.id, [])
                chunks_data.append(cd)

        chunks_path = os.path.join(self.storage_dir, "chunks.json")
        _atomic_json_write(chunks_path, chunks_data)

        # 保存 embedder 词汇表
        if hasattr(self.embedder, 'vocabulary'):
            embedder_data = {
                "vocabulary": self.embedder.vocabulary,
                "idf": self.embedder.idf,
                "doc_count": self.embedder._doc_count,
            }
            emb_path = os.path.join(self.storage_dir, "embedder.json")
            _atomic_json_write(emb_path, embedder_data, indent=2)

    def _auto_load(self):
        """自动加载已有数据"""
        docs_path = os.path.join(self.storage_dir, "documents.json")
        chunks_path = os.path.join(self.storage_dir, "chunks.json")
        emb_path = os.path.join(self.storage_dir, "embedder.json")

        # 加载 embedder
        if os.path.exists(emb_path) and hasattr(self.embedder, 'vocabulary'):
            try:
                with open(emb_path, "r", encoding="utf-8") as f:
                    emb_data = json.load(f)
                self.embedder.vocabulary = emb_data.get("vocabulary", {})
                self.embedder.idf = emb_data.get("idf", {})
                self.embedder._doc_count = emb_data.get("doc_count", 0)
                fitted = bool(self.embedder.vocabulary)
                self.embedder._fitted = fitted
                self.store._fitted = fitted
            except Exception as exc:
                logger.warning("加载知识库词表失败 %s: %s", emb_path, exc)

        # 加载 chunks
        if os.path.exists(chunks_path):
            try:
                with open(chunks_path, "r", encoding="utf-8") as f:
                    chunks_data = json.load(f)

                for index, raw_chunk in enumerate(chunks_data):
                    try:
                        cd = dict(raw_chunk)
                        emb = cd.pop("embedding", [])
                        chunk = KnowledgeChunk(**{
                            k: v for k, v in cd.items()
                            if k in KnowledgeChunk.__dataclass_fields__
                        })
                        from .vector_store import StoredChunk
                        self.store._chunks.append(StoredChunk(chunk=chunk, embedding=emb))
                    except Exception as exc:
                        logger.warning("跳过损坏的知识块 #%d: %s", index, exc)
            except Exception as exc:
                logger.warning("加载知识块文件失败 %s: %s", chunks_path, exc)

        # 加载文档元数据
        if os.path.exists(docs_path):
            try:
                with open(docs_path, "r", encoding="utf-8") as f:
                    docs_data = json.load(f)

                for doc_id, dd in docs_data.items():
                    try:
                        doc = KnowledgeDocument(
                            id=dd["id"],
                            title=dd["title"],
                            doc_type=dd["doc_type"],
                            source_path=dd.get("source_path", ""),
                            tags=dd.get("tags", []),
                            metadata=dd.get("metadata", {}),
                            created_at=dd.get("created_at", ""),
                        )
                        # 关联 chunks
                        doc.chunks = [
                            sc.chunk for sc in self.store._chunks
                            if sc.chunk.document_id == doc_id
                        ]
                        doc.chunk_count = len(doc.chunks)
                        self.documents[doc_id] = doc
                    except Exception as exc:
                        logger.warning("跳过损坏的知识文档 %s: %s", doc_id, exc)
            except Exception as exc:
                logger.warning("加载知识文档文件失败 %s: %s", docs_path, exc)

        # 重建索引。chunks.json 是 source of truth，这里只做一次可靠的启动恢复，
        # 不依赖进程内隐藏状态。**但不能无条件走 TF-IDF 路径**：
        # rebuild_index() 内部调用 self._tfidf()，对非 TfidfEmbedder 会直接 raise，
        # 导致"用户配置了远端/稠密 embedding 时知识库加载即失败"。
        if self.store._is_incremental():
            self.store.rebuild_index()
        elif self.store._chunks:
            # 稠密/远端 embedding：持久向量已随 chunk 载入（见上面的 emb），
            # 只需重建 id -> chunk 映射；不偷偷替换用户配置的 embedding backend。
            self.store._chunk_by_id = {
                stored.chunk.id: stored for stored in self.store._chunks
            }
            self.store._fitted = True

    def info(self) -> str:
        """知识库信息"""
        s = self.stats
        lines = [
            f"知识库目录: {s['storage_dir']}",
            f"文档数: {s['total_documents']}",
            f"知识块: {s['total_chunks']}",
        ]
        if s["by_type"]:
            type_names = {
                "word_spec": "Word规范",
                "ppt_spec": "PPT规范",
                "excel_rule": "Excel规则",
                "general": "通用",
            }
            parts = [f"{type_names.get(k, k)}:{v}" for k, v in s["by_type"].items()]
            lines.append("分类: " + ", ".join(parts))
        return "\n".join(lines)


def create_default_kb(storage_dir: str | None = None) -> OfficeKnowledgeBase:
    """
    创建带内置知识的默认知识库

    内置一些常用办公规范，开箱即用。
    """
    kb = OfficeKnowledgeBase(storage_dir=storage_dir or _default_kb_storage_dir())

    # 如果已有数据，不重复添加
    if kb.stats["total_documents"] > 0:
        return kb

    # 内置 Word 规范
    kb.add_text(
        "中文论文格式规范：\n"
        "1. 正文使用宋体小四号字（12pt）\n"
        "2. 行间距1.5倍\n"
        "3. 首行缩进2字符\n"
        "4. 一级标题黑体三号，二级标题黑体四号，三级标题黑体小四号\n"
        "5. 页边距：上下2.54cm，左右3.17cm\n"
        "6. 表格使用三线表格式\n"
        "7. 页码居中放置在页面底部",
        title="中文论文格式规范",
        doc_type="word_spec",
        tags=["论文", "学术", "默认"]
    )

    kb.add_text(
        "公文格式规范：\n"
        "1. 正文使用仿宋_GB2312三号字\n"
        "2. 标题使用方正小标宋简体二号字\n"
        "3. 一级标题黑体三号，二级标题楷体_GB2312三号\n"
        "4. 行间距固定值28磅\n"
        "5. 页边距：上3.7cm，下3.5cm，左2.8cm，右2.6cm\n"
        "6. 加盖印章的公文需注意印章位置",
        title="公文格式规范",
        doc_type="word_spec",
        tags=["公文", "行政", "默认"]
    )

    # 内置 PPT 规范
    kb.add_text(
        "商务PPT设计规范：\n"
        "1. 配色以深蓝、灰色为主，不超过3种主色\n"
        "2. 中文字体推荐微软雅黑或思源黑体\n"
        "3. 每页文字不超过6行，每行不超过20字\n"
        "4. 标题字号32-40pt，正文18-24pt\n"
        "5. 图表优先于大段文字\n"
        "6. 保持页面留白，不要塞满内容\n"
        "7. 封面包含标题、副标题、汇报人、日期\n"
        "8. 结尾页包含感谢语和联系方式",
        title="商务PPT设计规范",
        doc_type="ppt_spec",
        tags=["商务", "PPT", "默认"]
    )

    kb.add_text(
        "答辩PPT规范：\n"
        "1. 页数控制在12-15页\n"
        "2. 结构：封面→研究背景→研究问题→方法→结果→结论→致谢\n"
        "3. 文字精简，突出核心观点\n"
        "4. 数据图表清晰可读\n"
        "5. 配色简洁专业，避免花哨动画\n"
        "6. 每页有明确的主题句",
        title="答辩PPT规范",
        doc_type="ppt_spec",
        tags=["答辩", "学术", "默认"]
    )

    # 内置 Excel 规则
    kb.add_text(
        "财务计算规则：\n"
        "1. 金额保留2位小数，使用千分位格式\n"
        "2. 增长率 = (本期-上期)/上期 × 100%\n"
        "3. 毛利率 = (收入-成本)/收入 × 100%\n"
        "4. 净利率 = 净利润/收入 × 100%\n"
        "5. 合计行使用SUM函数，不手动计算\n"
        "6. 避免除零错误，使用IFERROR包裹\n"
        "7. 货币单位统一为元或万元",
        title="财务计算规则",
        doc_type="excel_rule",
        tags=["财务", "计算", "默认"]
    )

    kb.add_text(
        "销售分析常用指标：\n"
        "1. 销售额 = 单价 × 数量\n"
        "2. 同比增长率 = (今年-去年)/去年\n"
        "3. 环比增长率 = (本月-上月)/上月\n"
        "4. 客单价 = 销售额/订单数\n"
        "5. TOP N产品按销售额降序\n"
        "6. 地区销售占比用饼图展示\n"
        "7. 月度趋势用折线图",
        title="销售分析指标",
        doc_type="excel_rule",
        tags=["销售", "分析", "默认"]
    )

    return kb
