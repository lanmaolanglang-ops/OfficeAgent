"""
Document Structure Analyzer - 文档结构分析器

识别 Word 文档的层级结构，输出 DocumentTree。

支持识别的元素类型：
- title:       文档标题
- chapter:     章（第X章 / Chapter X / 1 xxx）
- section:     节（X.X xxx / Section X.X）
- subsection:  小节（X.X.X xxx）
- subsubsection: 小小节（X.X.X.X xxx）
- paragraph:   正文段落
- list_item:   列表项（有序/无序）
- quote:       引用块
- abstract:    摘要
- keywords:    关键词
- reference:   参考文献
- appendix:    附录

分析策略（按优先级）：
1. 样式名判定（Heading 1-6, Title, Quote, List 等）
2. 正则匹配（中文/数字/英文编号模式）
3. 格式特征（字号、加粗、缩进、对齐）
4. 语义关键词（摘要/Abstract/参考文献/References 等）
5. LLM 语义判断（可选回调）
"""
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

from docx import Document

logger = logging.getLogger(__name__)

# ==========================================
# 数据结构
# ==========================================

class NodeType(Enum):
    """节点类型"""
    TITLE = "title"
    CHAPTER = "chapter"           # 一级
    SECTION = "section"           # 二级
    SUBSECTION = "subsection"     # 三级
    SUBSUBSECTION = "subsubsection"  # 四级
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    ABSTRACT = "abstract"
    KEYWORDS = "keywords"
    REFERENCE = "reference"
    APPENDIX = "appendix"
    TABLE = "table"
    FIGURE = "figure"
    CODE = "code"
    UNKNOWN = "unknown"


# 节点类型 → 层级（用于构建树）
NODE_LEVEL = {
    NodeType.TITLE: 0,
    NodeType.CHAPTER: 1,
    NodeType.SECTION: 2,
    NodeType.SUBSECTION: 3,
    NodeType.SUBSUBSECTION: 4,
    NodeType.APPENDIX: 1,
    NodeType.ABSTRACT: 1,
    NodeType.REFERENCE: 1,
    NodeType.KEYWORDS: 2,
}


@dataclass
class DocumentNode:
    """文档树节点"""
    type: str = "paragraph"        # NodeType 值
    level: int = 0                 # 层级 0-4
    text: str = ""                 # 标题文本或段落内容
    index: int = -1                # 在文档中的段落索引
    children: list = field(default_factory=list)
    style_name: str = ""           # Word 样式名
    font_size: float = 0.0         # 字号（pt）
    bold: bool = False
    alignment: str = ""            # left/center/right/justify
    indent: float = 0.0            # 左缩进（pt）
    metadata: dict = field(default_factory=dict)

    def add_child(self, node: "DocumentNode"):
        self.children.append(node)

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "level": self.level,
            "text": self.text[:100] + "..." if len(self.text) > 100 else self.text,
        }
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def find_children(self, node_type: str) -> list:
        """查找指定类型的直接子节点"""
        return [c for c in self.children if c.type == node_type]

    def find_all(self, node_type: str) -> list:
        """递归查找指定类型的所有节点"""
        result = []
        for c in self.children:
            if c.type == node_type:
                result.append(c)
            result.extend(c.find_all(node_type))
        return result


@dataclass
class DocumentTree:
    """文档树"""
    title: str = ""
    root: DocumentNode = field(default_factory=lambda: DocumentNode(
        type="root", level=0, text="ROOT"
    ))
    nodes: list = field(default_factory=list)  # 扁平节点列表
    tables_count: int = 0
    paragraphs_count: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "structure": self.root.to_dict(),
            "stats": {
                "total_nodes": len(self.nodes),
                "paragraphs": self.paragraphs_count,
                "tables": self.tables_count,
            }
        }

    def to_json(self, indent=2) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def get_chapters(self) -> list:
        """获取所有章"""
        return self.root.find_children("chapter")

    def get_outline(self) -> list:
        """获取文档大纲（仅标题节点）"""
        return [n for n in self.nodes if n.type in (
            "title", "chapter", "section", "subsection", "subsubsection",
            "abstract", "reference", "appendix"
        )]

    def print_tree(self, node=None, prefix=""):
        """打印文档树"""
        if node is None:
            node = self.root
            if self.title:
                print(f"📄 {self.title}")
        for i, child in enumerate(node.children):
            is_last = i == len(node.children) - 1
            connector = "└─ " if is_last else "├─ "
            icon = self._type_icon(child.type)
            text = child.text[:50] + ("..." if len(child.text) > 50 else "")
            print(f"{prefix}{connector}{icon} [{child.level}] {text}")
            if child.children:
                ext = "   " if is_last else "│  "
                self.print_tree(child, prefix + ext)

    @staticmethod
    def _type_icon(node_type: str) -> str:
        icons = {
            "title": "📌",
            "chapter": "📖",
            "section": "📑",
            "subsection": "🔖",
            "subsubsection": "🏷️",
            "paragraph": "",
            "list_item": "•",
            "quote": "💬",
            "abstract": "📋",
            "keywords": "🔑",
            "reference": "📚",
            "appendix": "📎",
            "table": "📊",
        }
        return icons.get(node_type, "·")


# ==========================================
# 分析器
# ==========================================

class DocumentStructureAnalyzer:
    """
    文档结构分析器

    用法:
        analyzer = DocumentStructureAnalyzer()
        tree = analyzer.analyze("document.docx")
        tree.print_tree()

        # 可选：接入 LLM 进行语义判断
        analyzer = DocumentStructureAnalyzer(
            llm_callback=my_llm_function  # 接收 text, context 返回 NodeType
        )
    """

    # 语义关键词 → 节点类型
    SEMANTIC_KEYWORDS = {
        "abstract": [
            "摘要", "内容提要", "提要", "abstract", "summary",
            "executive summary",
        ],
        "keywords": [
            "关键词", "关键字", "keywords", "key words",
        ],
        "reference": [
            "参考文献", "引用文献", "参考资料", "references",
            "bibliography", "works cited", "引用",
        ],
        "appendix": [
            "附录", "附件", "appendix", "appendices", "supplementary",
        ],
    }

    # 中文编号模式
    CN_NUMBER_PATTERNS = [
        # 第X章/节/部分
        (r"^第[一二三四五六七八九十百千零〇\d]+[章节部分篇编卷集]", 1),
        # Chapter X / Section X / Part X（英文）
        (r"^(?:chapter|section|part|appendix)\s+\d+", 1),
        # 一、二、三、（中文数字+顿号，一级）
        (r"^[一二三四五六七八九十]+[、\.．]\s*\S", 1),
        # （一）（二）（中文数字+括号，二级）
        (r"^[（(][一二三四五六七八九十]+[）)]\s*\S", 2),
        # 1.1.1.1（四级）
        (r"^\d+\.\d+\.\d+\.\d+\s*\S", 4),
        # 1.1.1（三级）
        (r"^\d+\.\d+\.\d+\s*\S", 3),
        # 1.1（二级）
        (r"^\d+\.\d+\s*\S", 2),
        # 1. xxx / 1、xxx（单级数字，可能是一级或列表）
        (r"^\d+[、\.．]\s+\S", None),  # 需要上下文判断
        # 1) / (1) / ①（列表项）
        (r"^\d+[）)]\s*\S", -1),  # -1 表示列表
        (r"^[（(]\d+[）)]\s*\S", -1),
        (r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*\S", -1),
        # a) / b) / A)（字母列表）
        (r"^[a-zA-Z][）)]\s*\S", -1),
        # （a）（字母括号列表）
        (r"^[（(][a-zA-Z][）)]\s*\S", -1),
        # 无序列表：• - * ◦ ▪
        (r"^[•\-*◦▪‣⁃]\s+\S", -1),
    ]

    # 英文标题关键词（独立成行时可能是标题）
    EN_TITLE_KEYWORDS = [
        "introduction", "background", "overview", "related work",
        "methodology", "methods", "approach", "framework",
        "experiment", "experiments", "results", "discussion",
        "evaluation", "analysis", "implementation",
        "conclusion", "conclusions", "future work",
        "acknowledgments", "acknowledgements",
        "table of contents", "preface", "foreword",
    ]

    # 引用特征
    QUOTE_INDICATORS = [
        r"^[「『\"“]",       # 引号开头
        r"^注[：:]",          # "注："
        r"^备注[：:]",
        r"^说明[：:]",
    ]

    def __init__(self, llm_callback: Optional[Callable] = None):
        """
        Args:
            llm_callback: 可选的 LLM 回调函数，签名:
                callback(text: str, context: dict) -> Optional[str]
                返回 NodeType 的字符串值，或 None 表示无法判断
        """
        self.llm_callback = llm_callback

    def analyze(self, source) -> DocumentTree:
        """
        分析文档结构

        Args:
            source: 文件路径(str/Path) 或 python-docx Document 对象

        Returns:
            DocumentTree
        """
        if isinstance(source, (str, Path)):
            doc = Document(str(source))
        else:
            doc = source

        tree = DocumentTree()
        tree.tables_count = len(doc.tables)
        tree.paragraphs_count = len([p for p in doc.paragraphs if p.text.strip()])

        # 第一遍：识别每个段落的类型
        raw_nodes = []
        for idx, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            if not text:
                continue

            node = self._classify_paragraph(para, text, idx, raw_nodes)
            raw_nodes.append(node)

        # 第二遍：上下文修正（如单级数字编号的标题/列表判定）
        self._contextual_correction(raw_nodes)

        # 第三遍：可选 LLM 语义判断（对不确定的节点）
        if self.llm_callback:
            self._llm_refinement(raw_nodes)

        # 第四遍：构建树结构
        tree.nodes = raw_nodes
        self._build_tree(tree, raw_nodes)

        # 提取文档标题（第一个节点如果是 title 类型）
        for node in raw_nodes:
            if node.type == "title":
                tree.title = node.text
                break
        if not tree.title and raw_nodes:
            # 尝试用第一个看起来像标题的节点
            first = raw_nodes[0]
            if first.level >= 1 and len(first.text) < 50:
                tree.title = first.text

        return tree

    def _classify_paragraph(self, para, text: str, idx: int,
                            prev_nodes: list) -> DocumentNode:
        """分类单个段落"""
        node = DocumentNode(text=text, index=idx)

        # 提取格式信息
        style_name = para.style.name if para.style else ""
        node.style_name = style_name
        node.font_size, node.bold = self._get_font_info(para)
        node.alignment = self._get_alignment(para)
        node.indent = self._get_indent(para)

        # 1. 样式名判定（最可靠）
        style_type = self._classify_by_style(style_name)
        if style_type:
            node.type = style_type[0]
            node.level = style_type[1]
            return node

        # 2. 语义关键词判定（摘要、参考文献等）
        semantic_type = self._classify_by_semantic_keywords(text)
        if semantic_type:
            node.type = semantic_type
            node.level = NODE_LEVEL.get(NodeType(semantic_type), 1)
            return node

        # 3. 正则编号判定
        regex_result = self._classify_by_regex(text)
        if regex_result is not None:
            rtype, rlevel = regex_result
            if rtype == "list_item":
                node.type = "list_item"
                node.level = -1
            elif rtype == "heading":
                node.type = self._level_to_type(rlevel)
                node.level = rlevel
            return node

        # 4. 引用判定
        if self._is_quote(text, para):
            node.type = "quote"
            node.level = 0
            return node

        # 5. 格式特征判定
        # 文档开头、居中、大字号加粗 → 文档标题
        if idx <= 2 and node.alignment == "center" and node.bold and node.font_size >= 16:
            if not re.match(r"^(第|chapter|abstract|摘要|参考)", text, re.IGNORECASE):
                node.type = "title"
                node.level = 0
                return node

        # 大字号加粗 → 章标题（小三号15pt及以上）
        if node.font_size >= 15 and node.bold:
            node.type = "chapter"
            node.level = 1
            return node
        # 中号加粗 → 节标题（四号14pt及以上，但小于15pt）
        if node.font_size >= 13 and node.bold and len(text) < 60:
            node.type = "section"
            node.level = 2
            return node

        # 6. 英文标题关键词（独立成行且较短）
        if len(text) < 60 and text.lower().rstrip(":") in self.EN_TITLE_KEYWORDS:
            node.type = "section"
            node.level = 2
            return node

        # 7. 居中加粗且短（非开头位置）→ 可能是章标题
        if node.alignment == "center" and node.bold and len(text) < 40 and node.font_size >= 14:
            node.type = "chapter"
            node.level = 1
            return node

        # 默认：正文
        node.type = "paragraph"
        node.level = 0
        return node

    def _classify_by_style(self, style_name: str) -> Optional[tuple]:
        """通过样式名判定类型，返回 (type_str, level)"""
        if not style_name:
            return None

        style_lower = style_name.lower()

        # 标题样式
        if style_lower.startswith("heading"):
            try:
                level = int(style_name.split()[-1])
                if 1 <= level <= 4:
                    type_map = {1: "chapter", 2: "section",
                                3: "subsection", 4: "subsubsection"}
                    return (type_map[level], level)
            except (ValueError, IndexError):
                pass

        # Title 样式
        if style_lower in ("title", "标题", "文档标题", "document title"):
            return ("title", 0)

        # Subtitle
        if style_lower in ("subtitle", "副标题"):
            return ("title", 0)

        # 引用样式
        if "quote" in style_lower or "引用" in style_name or "blockquote" in style_lower:
            return ("quote", 0)

        # 列表样式
        if "list" in style_lower:
            return ("list_item", -1)

        # 摘要/参考文献样式
        if "abstract" in style_lower or "摘要" in style_name:
            return ("abstract", 1)
        if "reference" in style_lower or "bibliography" in style_lower or "参考文献" in style_name:
            return ("reference", 1)

        return None

    def _classify_by_semantic_keywords(self, text: str) -> Optional[str]:
        """通过语义关键词判定类型"""
        text_lower = text.lower().strip().rstrip(":：.。")

        for node_type, keywords in self.SEMANTIC_KEYWORDS.items():
            for kw in keywords:
                if text_lower == kw or text_lower.startswith(kw + " ") or \
                   text_lower.startswith(kw + ":") or text_lower.startswith(kw + "："):
                    # 确保是独立标题（较短）
                    if len(text) < 50:
                        return node_type
        return None

    def _classify_by_regex(self, text: str) -> Optional[tuple]:
        """通过正则编号判定，返回 (type, level) 或 None"""
        for pattern, level in self.CN_NUMBER_PATTERNS:
            if re.match(pattern, text, re.IGNORECASE):
                if level == -1:
                    return ("list_item", -1)
                elif level is None:
                    # 单级数字 "1. xxx" — 暂时标记为 heading level 1
                    # 后续 _contextual_correction 会根据上下文修正
                    return ("heading", 1)
                else:
                    return ("heading", level)
        return None

    def _is_quote(self, text: str, para) -> bool:
        """判断是否为引用"""
        for pattern in self.QUOTE_INDICATORS:
            if re.match(pattern, text):
                return True

        # 左缩进较大且短 → 可能是引用
        indent = self._get_indent(para)
        if indent > 48 and len(text) < 200:  # 缩进超过约2字符
            return True

        return False

    def _contextual_correction(self, nodes: list):
        """
        上下文修正：
        1. 单级数字 "1. xxx" 需要判断是标题还是列表
        2. 连续编号（1. 2. 3.）归为列表
        3. 前一段以"："结尾时，编号更可能是列表
        """
        # 第一遍：找出连续数字编号的序列
        i = 0
        while i < len(nodes):
            node = nodes[i]
            # 检查是否是单级数字编号（被初步标记为 chapter）
            if node.type == "chapter" and re.match(r"^\d+[、\.．]\s+\S", node.text):
                # 提取编号数字
                num_match = re.match(r"^(\d+)[、\.．]\s+", node.text)
                if num_match:
                    start_num = int(num_match.group(1))
                    # 检查是否形成连续序列
                    sequence = [node]
                    expected = start_num + 1
                    j = i + 1
                    while j < len(nodes):
                        next_node = nodes[j]
                        next_match = re.match(r"^(\d+)[、\.．]\s+", next_node.text)
                        if next_match and int(next_match.group(1)) == expected:
                            sequence.append(next_node)
                            expected += 1
                            j += 1
                        elif next_node.type == "paragraph" or not next_match:
                            # 遇到非编号段落，序列结束
                            break
                        else:
                            break

                    # 连续2个以上编号 → 列表
                    if len(sequence) >= 2:
                        for seq_node in sequence:
                            seq_node.type = "list_item"
                            seq_node.level = -1
                        i = j
                        continue

                    # 单个编号：检查上下文
                    # 前一段以"："":"结尾 → 列表
                    if i > 0:
                        prev_text = nodes[i-1].text.rstrip()
                        if prev_text.endswith(("：", ":", "如下", "以下", "包括")):
                            node.type = "list_item"
                            node.level = -1
                            i += 1
                            continue
                        # 前一段是正文且不是标题 → 可能是列表
                        if nodes[i-1].type == "paragraph":
                            text_after_num = re.sub(r"^\d+[、\.．]\s+", "", node.text)
                            # 如果文本较长（>15字）或像完整句子 → 列表
                            if len(text_after_num) > 15:
                                node.type = "list_item"
                                node.level = -1
                                i += 1
                                continue
                            # 以动词开头 → 列表
                            list_verbs = ["完成", "实现", "建立", "支持", "提供", "增加",
                                         "修改", "删除", "添加", "检查", "确保", "进行",
                                         "采用", "使用", "通过", "根据", "按照", "首先",
                                         "可以", "需要", "应该", "必须", "能够", "分析",
                                         "研究", "设计", "开发", "测试", "验证", "评估"]
                            if any(text_after_num.startswith(v) for v in list_verbs):
                                node.type = "list_item"
                                node.level = -1
                                i += 1
                                continue
            i += 1

    def _llm_refinement(self, nodes: list):
        """使用 LLM 对不确定的节点进行语义判断"""
        for position, node in enumerate(nodes):
            # 只对不确定的节点（正文但可能是标题，或未知类型）调用 LLM
            if node.type not in ("paragraph", "unknown"):
                continue
            if len(node.text) < 5 or len(node.text) > 200:
                continue

            context = {
                "font_size": node.font_size,
                "bold": node.bold,
                "alignment": node.alignment,
                "prev_types": [n.type for n in nodes[max(0, position - 3):position]],
            }

            try:
                result = self.llm_callback(node.text, context)
                if result and result in [t.value for t in NodeType]:
                    node.type = result
                    node.level = NODE_LEVEL.get(NodeType(result), 0)
                    node.metadata["llm_verified"] = True
            except Exception:
                logger.exception("LLM 文档结构细化失败，保留规则识别结果")

    def _build_tree(self, tree: DocumentTree, nodes: list):
        """根据层级构建树结构"""
        stack = [tree.root]  # 栈顶是当前父节点

        for node in nodes:
            if node.type in ("paragraph", "list_item", "quote", "table", "figure"):
                # 正文类节点作为当前父节点的子节点
                stack[-1].add_child(node)
            elif node.level == 0 and node.type == "title":
                # 文档标题放在根节点
                tree.root.add_child(node)
            else:
                # 标题节点：找到合适的父节点
                # 弹出栈中级别 >= 当前级别的节点
                while len(stack) > 1 and stack[-1].level >= node.level:
                    stack.pop()

                # 当前栈顶就是父节点
                stack[-1].add_child(node)
                stack.append(node)

    # ==========================================
    # 格式信息提取
    # ==========================================

    def _get_font_info(self, para) -> tuple:
        """获取段落的主要字号和加粗状态"""
        size_weights = {}
        bold_chars = 0
        total_chars = 0

        for run in para.runs:
            if not run.text.strip():
                continue
            char_count = len(run.text.strip())
            total_chars += char_count
            size = run.font.size.pt if run.font.size else None
            if size is not None:
                size_weights[size] = size_weights.get(size, 0) + char_count
            if run.font.bold:
                bold_chars += char_count

        # 以承载字符最多的字号为主字号，避免单个大号符号/局部强调
        # 把整段正文误判为标题。
        main_size = max(size_weights, key=size_weights.get) if size_weights else 0.0
        is_bold = bool(total_chars and bold_chars >= total_chars / 2)
        if not main_size and para.style and para.style.font:
            if para.style.font.size:
                main_size = para.style.font.size.pt
            if para.style.font.bold:
                is_bold = True

        return main_size, is_bold

    def _get_alignment(self, para) -> str:
        """获取对齐方式"""
        if para.alignment is not None:
            align_map = {
                0: "left", 1: "center", 2: "right",
                3: "justify", 4: "distribute",
            }
            return align_map.get(int(para.alignment), "")
        return ""

    def _get_indent(self, para) -> float:
        """获取左缩进（pt）"""
        if para.paragraph_format and para.paragraph_format.left_indent:
            return para.paragraph_format.left_indent.pt
        return 0.0

    @staticmethod
    def _level_to_type(level: int) -> str:
        """层级数字 → 类型字符串"""
        type_map = {
            1: "chapter",
            2: "section",
            3: "subsection",
            4: "subsubsection",
        }
        return type_map.get(level, "paragraph")


# ==========================================
# 便捷函数
# ==========================================

def analyze_document(source, llm_callback=None) -> DocumentTree:
    """分析文档结构的便捷函数"""
    analyzer = DocumentStructureAnalyzer(llm_callback=llm_callback)
    return analyzer.analyze(source)


def get_outline(source) -> list:
    """获取文档大纲"""
    tree = analyze_document(source)
    return tree.get_outline()
