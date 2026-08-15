"""
Agent Evaluation - Agent评分系统
Word格式准确率、PPT视觉质量、Excel计算准确率
"""
import os
import sys
import json
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@dataclass
class EvaluationScore:
    """评分结果"""
    agent_type: str  # word/ppt/excel
    total_score: float  # 0-100
    dimensions: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "total_score": round(self.total_score, 1),
            "passed": self.passed,
            "dimensions": {k: round(v, 1) if isinstance(v, float) else v for k, v in self.dimensions.items()},
            "issues": self.issues,
        }


class WordAgentEvaluator:
    """Word Agent评分"""

    def evaluate(self, doc_path: str) -> EvaluationScore:
        from docx import Document
        score = EvaluationScore(agent_type="word", total_score=0)
        try:
            doc = Document(doc_path)
            dims = {}
            issues = []
            # 1. 结构完整性 (25分)
            has_title = any(p.style.name.startswith("Heading") for p in doc.paragraphs)
            has_content = len(doc.paragraphs) > 1
            structure_score = 25 if (has_title and has_content) else (15 if has_content else 5)
            dims["结构完整性"] = structure_score
            if not has_title:
                issues.append("缺少标题")
            # 2. 格式规范 (25分)
            format_score = 25
            for p in doc.paragraphs[:10]:
                if p.runs and p.runs[0].font.size:
                    if p.runs[0].font.size.pt < 8:
                        format_score -= 2
            dims["格式规范"] = max(format_score, 0)
            # 3. 表格质量 (25分)
            table_score = 25
            for table in doc.tables:
                if not table.rows or not table.columns:
                    table_score -= 5
                for row in table.rows:
                    for cell in row.cells:
                        if not cell.text.strip():
                            table_score -= 1
            dims["表格质量"] = max(table_score, 0)
            # 4. 可读性 (25分)
            total_text = sum(len(p.text) for p in doc.paragraphs)
            readability = 25 if total_text > 100 else (15 if total_text > 20 else 5)
            dims["可读性"] = readability
            if total_text < 20:
                issues.append("内容过少")
            total = sum(dims.values())
            score.dimensions = dims
            score.issues = issues
            score.total_score = total
            score.passed = total >= 60
        except Exception as e:
            score.issues.append(f"评估失败: {e}")
        return score


class PPTAgentEvaluator:
    """PPT Agent评分"""

    def evaluate(self, ppt_path: str) -> EvaluationScore:
        from pptx import Presentation
        from pptx.util import Emu
        score = EvaluationScore(agent_type="ppt", total_score=0)
        try:
            prs = Presentation(ppt_path)
            dims = {}
            issues = []
            slide_w = prs.slide_width
            slide_h = prs.slide_height
            # 1. 布局完整性 (25分)
            layout_score = 25
            if len(prs.slides) < 3:
                layout_score -= 10
                issues.append("幻灯片数量过少")
            dims["布局完整性"] = layout_score
            # 2. 内容质量 (25分)
            content_score = 25
            for slide in prs.slides:
                has_content = False
                for shape in slide.shapes:
                    if shape.has_text_frame and shape.text_frame.text.strip():
                        has_content = True
                        break
                if not has_content:
                    content_score -= 3
            dims["内容质量"] = max(content_score, 0)
            # 3. 无溢出 (25分)
            overflow_score = 25
            for slide in prs.slides:
                for shape in slide.shapes:
                    try:
                        if shape.left is not None and shape.top is not None:
                            right = shape.left + (shape.width or 0)
                            bottom = shape.top + (shape.height or 0)
                            if right > slide_w + Emu(100000) or bottom > slide_h + Emu(100000):
                                overflow_score -= 2
                    except Exception:
                        pass
            dims["无溢出"] = max(overflow_score, 0)
            # 4. 字体统一 (25分)
            font_score = 25
            fonts = set()
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            for run in para.runs:
                                if run.font.name:
                                    fonts.add(run.font.name)
            if len(fonts) > 5:
                font_score -= 10
                issues.append(f"字体种类过多: {len(fonts)}种")
            dims["字体统一"] = font_score
            total = sum(dims.values())
            score.dimensions = dims
            score.issues = issues
            score.total_score = total
            score.passed = total >= 60
        except Exception as e:
            score.issues.append(f"评估失败: {e}")
        return score


class ExcelAgentEvaluator:
    """Excel Agent评分"""

    def evaluate(self, xlsx_path: str) -> EvaluationScore:
        from openpyxl import load_workbook
        score = EvaluationScore(agent_type="excel", total_score=0)
        try:
            wb = load_workbook(xlsx_path, data_only=False)
            dims = {}
            issues = []
            # 1. 数据完整性 (25分)
            data_score = 25
            total_cells = 0
            filled_cells = 0
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        total_cells += 1
                        if cell.value is not None:
                            filled_cells += 1
            if total_cells > 0 and filled_cells / total_cells < 0.3:
                data_score -= 10
                issues.append("填充率过低")
            dims["数据完整性"] = data_score
            # 2. 公式正确性 (25分)
            formula_score = 25
            formula_count = 0
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.startswith("="):
                            formula_count += 1
            if formula_count == 0:
                formula_score = 15  # 无公式不扣分太多
            dims["公式正确性"] = formula_score
            # 3. 结构规范 (25分)
            struct_score = 25
            for ws in wb.worksheets:
                if ws.max_row > 1 and ws.max_column > 1:
                    header = ws.cell(row=1, column=1).value
                    if header is None:
                        struct_score -= 5
            dims["结构规范"] = max(struct_score, 0)
            # 4. 可读性 (25分)
            read_score = 25
            if len(wb.worksheets) == 0:
                read_score -= 10
            for ws in wb.worksheets:
                if ws.title == "Sheet" or ws.title == "Sheet1":
                    read_score -= 3
            dims["可读性"] = max(read_score, 0)
            total = sum(dims.values())
            score.dimensions = dims
            score.issues = issues
            score.total_score = total
            score.passed = total >= 60
        except Exception as e:
            score.issues.append(f"评估失败: {e}")
        return score


class AgentEvaluationSuite:
    """Agent评估套件"""

    def __init__(self):
        self.word_evaluator = WordAgentEvaluator()
        self.ppt_evaluator = PPTAgentEvaluator()
        self.excel_evaluator = ExcelAgentEvaluator()
        self.results: list[EvaluationScore] = []

    def evaluate_word(self, path: str) -> EvaluationScore:
        r = self.word_evaluator.evaluate(path)
        self.results.append(r)
        return r

    def evaluate_ppt(self, path: str) -> EvaluationScore:
        r = self.ppt_evaluator.evaluate(path)
        self.results.append(r)
        return r

    def evaluate_excel(self, path: str) -> EvaluationScore:
        r = self.excel_evaluator.evaluate(path)
        self.results.append(r)
        return r

    def get_summary(self) -> dict:
        if not self.results:
            return {"average": 0, "by_type": {}}
        by_type = {}
        for r in self.results:
            by_type.setdefault(r.agent_type, []).append(r.total_score)
        avg_by_type = {k: round(sum(v)/len(v), 1) for k, v in by_type.items()}
        overall = round(sum(r.total_score for r in self.results) / len(self.results), 1)
        return {
            "overall": overall,
            "by_type": avg_by_type,
            "total_evaluated": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
        }


def run_evaluation_tests():
    """运行评估测试"""
    from tests.generate_dataset import generate_dataset, DATASET_DIR
    generate_dataset()
    suite = AgentEvaluationSuite()
    # Word
    for level in ["simple", "medium", "complex"]:
        path = DATASET_DIR / "word" / f"{level}.docx"
        if path.exists():
            result = suite.evaluate_word(str(path))
            print(f"  Word/{level}: {result.total_score}分 {'✓' if result.passed else '✗'}")
    # PPT
    for level in ["simple", "medium", "complex"]:
        path = DATASET_DIR / "ppt" / f"{level}.pptx"
        if path.exists():
            result = suite.evaluate_ppt(str(path))
            print(f"  PPT/{level}: {result.total_score}分 {'✓' if result.passed else '✗'}")
    # Excel
    for level in ["simple", "medium", "complex"]:
        path = DATASET_DIR / "excel" / f"{level}.xlsx"
        if path.exists():
            result = suite.evaluate_excel(str(path))
            print(f"  Excel/{level}: {result.total_score}分 {'✓' if result.passed else '✗'}")
    summary = suite.get_summary()
    print(f"\n  Overall: {summary['overall']}分")
    return suite


if __name__ == "__main__":
    run_evaluation_tests()
