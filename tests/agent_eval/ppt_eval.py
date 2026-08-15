"""
PPT Agent 评估器
"""
import time
from pathlib import Path
from .evaluator import BaseEvaluator, EvalResult, EvalType, EvalStatus, EvalMetric


class PPTEvaluator(BaseEvaluator):
    """PPT Agent 输出质量评估"""

    def evaluate_generation(self, output_path: str | Path,
                            expected_slides: int = None) -> EvalResult:
        """评估PPT生成质量"""
        start = time.time()
        metrics = []
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            prs = Presentation(str(output_path))

            # 1. 文件完整性
            metrics.append(EvalMetric("文件完整性", 100, weight=2.0))

            # 2. 幻灯片数量
            slide_count = len(prs.slides)
            if expected_slides:
                count_score = max(0, 100 - abs(slide_count - expected_slides) * 10)
            else:
                count_score = min(100, slide_count * 15) if slide_count > 0 else 0
            metrics.append(EvalMetric("幻灯片数量", count_score, weight=1.5,
                                      details=f"{slide_count}页"))

            # 3. 标题覆盖
            titled = sum(1 for s in prs.slides if s.shapes.title and s.shapes.title.text)
            title_score = (titled / slide_count * 100) if slide_count > 0 else 0
            metrics.append(EvalMetric("标题覆盖", title_score, weight=1.5,
                                      details=f"{titled}/{slide_count}有标题"))

            # 4. 内容密度
            total_text = 0
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        total_text += len(shape.text_frame.text)
            density_score = min(100, total_text / (slide_count * 50)) if slide_count > 0 else 0
            metrics.append(EvalMetric("内容密度", density_score, weight=1.0,
                                      details=f"共{total_text}字符"))

            # 5. 布局合理性（检查是否有内容占位符）
            layout_ok = 0
            for slide in prs.slides:
                if len(slide.shapes) >= 2:
                    layout_ok += 1
            layout_score = (layout_ok / slide_count * 100) if slide_count > 0 else 0
            metrics.append(EvalMetric("布局合理性", layout_score, weight=1.0,
                                      details=f"{layout_ok}/{slide_count}有内容"))

            status = EvalStatus.PASS if all(m.score >= 50 for m in metrics) else EvalStatus.PARTIAL

        except Exception as e:
            metrics.append(EvalMetric("错误", 0, details=str(e)))
            status = EvalStatus.ERROR

        return EvalResult(
            eval_type=EvalType.PPT,
            test_name="ppt_generation_eval",
            status=status,
            metrics=metrics,
            duration=time.time() - start,
        )

    def evaluate_design(self, output_path: str | Path) -> EvalResult:
        """评估PPT设计质量"""
        start = time.time()
        metrics = []
        try:
            from pptx import Presentation
            prs = Presentation(str(output_path))

            # 字体一致性
            fonts = set()
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            for run in para.runs:
                                if run.font.name:
                                    fonts.add(run.font.name)
            font_score = 100 if len(fonts) <= 3 else max(0, 100 - len(fonts) * 10)
            metrics.append(EvalMetric("字体一致性", font_score, weight=1.5,
                                      details=f"{len(fonts)}种字体"))

            # 幻灯片尺寸
            w, h = prs.slide_width, prs.slide_height
            ratio = w / h if h > 0 else 0
            ratio_ok = 1.3 < ratio < 1.8  # 16:9约1.78
            metrics.append(EvalMetric("尺寸规范", 100 if ratio_ok else 60, weight=0.5,
                                      details=f"比例{ratio:.2f}"))

            status = EvalStatus.PASS if all(m.score >= 60 for m in metrics) else EvalStatus.PARTIAL

        except Exception as e:
            metrics.append(EvalMetric("错误", 0, details=str(e)))
            status = EvalStatus.ERROR

        return EvalResult(
            eval_type=EvalType.PPT,
            test_name="ppt_design_eval",
            status=status,
            metrics=metrics,
            duration=time.time() - start,
        )
