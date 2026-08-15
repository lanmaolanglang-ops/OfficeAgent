"""
Test Dataset Generator - 测试数据集生成器
生成覆盖简单/中等/复杂场景的Word/PPT/Excel测试文件
"""
import os
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATASET_DIR = PROJECT_ROOT / "test_dataset"


def generate_word_simple(path: Path):
    """简单Word: 单页纯文本"""
    from docx import Document
    doc = Document()
    doc.add_heading("测试文档", 0)
    doc.add_paragraph("这是一个简单的测试文档。")
    doc.add_paragraph("包含基本的文本内容。")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def generate_word_medium(path: Path):
    """中等Word: 多节、表格"""
    from docx import Document
    doc = Document()
    doc.add_heading("中等复杂度文档", 0)
    for i in range(1, 4):
        doc.add_heading(f"第{i}节", level=1)
        doc.add_paragraph(f"第{i}节的内容。" * 10)
    doc.add_heading("数据表", level=1)
    table = doc.add_table(rows=6, cols=4)
    table.style = "Table Grid"
    headers = ["项目", "数量", "单价", "合计"]
    for j, h in enumerate(headers):
        table.cell(0, j).text = h
    for i in range(1, 6):
        table.cell(i, 0).text = f"产品{i}"
        table.cell(i, 1).text = str(i * 10)
        table.cell(i, 2).text = str(i * 100)
        table.cell(i, 3).text = str(i * 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def generate_word_complex(path: Path):
    """复杂Word: 论文格式、页眉页脚、多级标题、目录占位"""
    from docx import Document
    from docx.shared import Pt, Inches
    doc = Document()
    # 页眉页脚
    section = doc.sections[0]
    section.header.paragraphs[0].text = "学术论文"
    section.footer.paragraphs[0].text = "第 X 页"
    # 标题
    doc.add_heading("基于深度学习的文本分类研究", 0)
    doc.add_paragraph("作者：张三\n单位：某大学")
    # 摘要
    doc.add_heading("摘要", level=1)
    doc.add_paragraph("本文研究了..." * 20)
    # 正文章节
    for chapter in range(1, 5):
        doc.add_heading(f"第{chapter}章", level=1)
        for section_num in range(1, 4):
            doc.add_heading(f"{chapter}.{section_num} 小节", level=2)
            doc.add_paragraph("内容" * 50)
    # 参考文献
    doc.add_heading("参考文献", level=1)
    for i in range(1, 11):
        doc.add_paragraph(f"[{i}] 文献{i}的引用信息")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def generate_ppt_simple(path: Path):
    """简单PPT: 5页基础"""
    from pptx import Presentation
    prs = Presentation()
    # 封面
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "简单演示"
    # 内容页
    for i in range(1, 4):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = f"第{i}页"
        slide.placeholders[1].text = f"内容{i}"
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))


def generate_ppt_medium(path: Path):
    """中等PPT: 10页，含表格"""
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "季度报告"
    for i in range(1, 8):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = f"章节{i}"
        slide.placeholders[1].text = f"章节{i}的详细内容"
    # 表格页
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "数据表"
    table = slide.shapes.add_table(5, 4, Inches(1), Inches(2), Inches(8), Inches(3)).table
    for r in range(5):
        for c in range(4):
            table.cell(r, c).text = f"R{r}C{c}"
    # 结束页
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "谢谢"
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))


def generate_ppt_complex(path: Path):
    """复杂PPT: 20页多布局"""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    layouts = list(prs.slide_layouts)
    for i in range(20):
        layout = layouts[i % len(layouts)]
        slide = prs.slides.add_slide(layout)
        if slide.shapes.title:
            slide.shapes.title.text = f"幻灯片 {i+1}"
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))


def generate_excel_simple(path: Path):
    """简单Excel: 10行5列"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["姓名", "年龄", "城市"])
    data = [("张三", 25, "北京"), ("李四", 30, "上海"), ("王五", 28, "广州")]
    for row in data:
        ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def generate_excel_medium(path: Path):
    """中等Excel: 多sheet、公式"""
    from openpyxl import Workbook
    wb = Workbook()
    # Sheet1: 销售数据
    ws1 = wb.active
    ws1.title = "销售"
    ws1.append(["月份", "销售额", "成本", "利润"])
    for i in range(1, 13):
        ws1.append([f"{i}月", i*10000, i*6000, f"=B{i+1}-C{i+1}"])
    ws1.append(["合计", "=SUM(B2:B13)", "=SUM(C2:C13)", "=SUM(D2:D13)"])
    # Sheet2: 统计
    ws2 = wb.create_sheet("统计")
    ws2.append(["指标", "值"])
    ws2.append(["平均销售额", "=AVERAGE(销售!B2:B13)"])
    ws2.append(["最大销售额", "=MAX(销售!B2:B13)"])
    ws2.append(["最小销售额", "=MIN(销售!B2:B13)"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def generate_excel_complex(path: Path):
    """复杂Excel: 1000行、多sheet、图表"""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    wb = Workbook()
    # 数据sheet
    ws = wb.active
    ws.title = "明细"
    headers = ["ID", "日期", "产品", "数量", "单价", "金额", "区域"]
    ws.append(headers)
    products = ["A", "B", "C", "D"]
    regions = ["华北", "华东", "华南", "西部"]
    for i in range(1, 1001):
        ws.append([
            i, f"2024-{(i%12)+1:02d}-{(i%28)+1:02d}",
            products[i % 4], (i % 100) + 1,
            (i % 50) * 10 + 100,
            f"=D{i+1}*E{i+1}",
            regions[i % 4]
        ])
    # 汇总sheet
    ws2 = wb.create_sheet("汇总")
    ws2.append(["产品", "总数量", "总金额"])
    for p in products:
        row = ws2.max_row + 1
        ws2.append([p, f'=SUMIF(明细!C:C,"{p}",明细!D:D)', f'=SUMIF(明细!C:C,"{p}",明细!F:F)'])
    # 图表
    chart = BarChart()
    data = Reference(ws2, min_col=2, min_row=1, max_row=5)
    chart.add_data(data, titles_from_data=True)
    ws2.add_chart(chart, "E2")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def generate_dataset():
    """生成所有测试数据集"""
    print("Generating test dataset...")
    # Word
    generate_word_simple(DATASET_DIR / "word" / "simple.docx")
    generate_word_medium(DATASET_DIR / "word" / "medium.docx")
    generate_word_complex(DATASET_DIR / "word" / "complex.docx")
    print("  Word: 3 files")
    # PPT
    generate_ppt_simple(DATASET_DIR / "ppt" / "simple.pptx")
    generate_ppt_medium(DATASET_DIR / "ppt" / "medium.pptx")
    generate_ppt_complex(DATASET_DIR / "ppt" / "complex.pptx")
    print("  PPT: 3 files")
    # Excel
    generate_excel_simple(DATASET_DIR / "excel" / "simple.xlsx")
    generate_excel_medium(DATASET_DIR / "excel" / "medium.xlsx")
    generate_excel_complex(DATASET_DIR / "excel" / "complex.xlsx")
    print("  Excel: 3 files")
    # 清单
    manifest = {
        "version": "0.47.5",
        "generated_at": os.path.getmtime(str(DATASET_DIR)),
        "files": {
            "word": {
                "simple": {"path": "word/simple.docx", "description": "单页纯文本"},
                "medium": {"path": "word/medium.docx", "description": "多节+表格"},
                "complex": {"path": "word/complex.docx", "description": "论文格式+页眉页脚+多级标题"},
            },
            "ppt": {
                "simple": {"path": "ppt/simple.pptx", "description": "5页基础"},
                "medium": {"path": "ppt/medium.pptx", "description": "10页+表格"},
                "complex": {"path": "ppt/complex.pptx", "description": "20页多布局"},
            },
            "excel": {
                "simple": {"path": "excel/simple.xlsx", "description": "10行5列"},
                "medium": {"path": "excel/medium.xlsx", "description": "多sheet+公式"},
                "complex": {"path": "excel/complex.xlsx", "description": "1000行+图表+汇总"},
            },
        },
    }
    with open(DATASET_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"Dataset generated at: {DATASET_DIR}")
    return manifest


if __name__ == "__main__":
    generate_dataset()
