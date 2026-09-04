"""text_encoding 统一编码探测入口专项测试。

钉住的契约：
1. BOM 优先且权威；
2. UTF-8 strict 成功即接受；
3. GB 家族（gbk/gb2312/gb18030）经 gb18030 单候选覆盖，但要做乱码质量评分；
4. "能 decode 但明显乱码"的内容显式拒绝（TextDecodeError），绝不静默吞字节；
5. document_parser / word_service / slide_planner 三入口消费同一 helper，结果一致。
"""
import codecs

import pytest

from office_agent.text_encoding import (
    TextDecodeError,
    decode_bytes,
    read_text_file,
)
from office_agent.knowledge_base.document_parser import DocumentParser
from office_agent.ppt_agent.slide_planner import SlidePlanner


# ---------------------------------------------------------------------------
# decode_bytes 单元行为
# ---------------------------------------------------------------------------

class TestDecodeBytes:
    def test_utf8_chinese(self):
        text, enc = decode_bytes("第一季度总结\n营收增长 20%".encode("utf-8"))
        assert enc == "utf-8"
        assert "第一季度总结" in text

    def test_utf8_bom(self):
        text, enc = decode_bytes(codecs.BOM_UTF8 + "中文标题".encode("utf-8"))
        assert enc == "utf-8-sig"
        assert text == "中文标题"
        assert not text.startswith("\ufeff")

    def test_gbk_chinese(self):
        text, enc = decode_bytes("中文内容GBK编码，包含标点：；。".encode("gbk"))
        assert enc == "gb18030"
        assert "中文内容GBK编码" in text

    def test_gb2312_content_decoded_via_gb18030(self):
        # gb2312 双字节区被 gb18030 解码兼容
        text, enc = decode_bytes("简体中文测试".encode("gb2312"))
        assert enc == "gb18030"
        assert text == "简体中文测试"

    def test_gb18030_four_byte_chars(self):
        # 合法 gb18030 四字节生僻字不得被误拒
        text, enc = decode_bytes("生僻字：𠮷𡃁".encode("gb18030"))
        assert enc == "gb18030"
        assert "𠮷" in text

    def test_ascii_english(self):
        text, enc = decode_bytes(b"hello world 123")
        assert enc == "utf-8"
        assert text == "hello world 123"

    def test_mixed_chinese_english_gbk(self):
        text, enc = decode_bytes("Q3 revenue 增长 20%，profit up".encode("gbk"))
        assert enc == "gb18030"
        assert "Q3 revenue 增长 20%" in text

    def test_utf16_le_bom(self):
        text, enc = decode_bytes("中文 UTF-16".encode("utf-16"))
        assert enc == "utf-16"
        assert "中文 UTF-16" in text

    def test_utf16_be_bom(self):
        text, enc = decode_bytes(codecs.BOM_UTF16_BE + "中文".encode("utf-16-be"))
        assert enc == "utf-16"
        assert text == "中文"

    def test_empty_bytes(self):
        text, enc = decode_bytes(b"")
        assert text == ""

    def test_legit_long_gbk_not_rejected(self):
        # 合法 GBK 长文不得误判为乱码
        data = ("中华人民共和国成立以来，经济社会发展取得了举世瞩目的成就。" * 200).encode("gbk")
        text, enc = decode_bytes(data)
        assert enc == "gb18030"
        assert "举世瞩目" in text

    def test_corrupted_utf8_mojibake_rejected(self):
        # 构造"错误编码可成功 decode 但明显乱码"样例：
        # UTF-8 长文混入孤立 continuation byte 使 strict 失败，
        # gb18030 虽能 decode 但产物是典型"涓"式乱码，必须拒绝。
        data = ("公司年度经营报告：各部门营收稳步增长，利润率持续提升，市场前景良好。" * 5).encode("utf-8") + b"\x80"
        with pytest.raises(TextDecodeError):
            decode_bytes(data)

    def test_latin1_european_rejected(self):
        # latin-1 欧洲文本不是受支持编码；gb18030 误吞的产物必须拒绝，
        # 不允许回退到 latin-1 静默接受。
        with pytest.raises(TextDecodeError):
            decode_bytes("café résumé naïve élève".encode("latin-1"))

    def test_binary_garbage_rejected(self):
        with pytest.raises(TextDecodeError):
            decode_bytes(bytes(range(256)) * 4)

    def test_no_silent_byte_loss(self):
        # 禁止 errors="ignore" 式静默丢字节：要么完整正确解码，要么抛错
        data = "完整的中文句子不能被截断。".encode("gbk")
        text, _ = decode_bytes(data)
        assert text == "完整的中文句子不能被截断。"
        assert "\ufffd" not in text


# ---------------------------------------------------------------------------
# read_text_file
# ---------------------------------------------------------------------------

class TestReadTextFile:
    def test_reads_gbk_file(self, tmp_path):
        p = tmp_path / "legacy.txt"
        p.write_bytes("中文内容GBK编码".encode("gbk"))
        assert read_text_file(p) == "中文内容GBK编码"

    def test_undecodable_raises(self, tmp_path):
        p = tmp_path / "garbage.txt"
        p.write_bytes(bytes(range(256)) * 4)
        with pytest.raises(TextDecodeError):
            read_text_file(p)


# ---------------------------------------------------------------------------
# 三入口一致性：document_parser / word_service / slide_planner
# ---------------------------------------------------------------------------

class TestEntryPointConsistency:
    @pytest.mark.parametrize(
        "payload",
        [
            "统一入口 UTF-8 中文\n第二行内容".encode("utf-8"),
            codecs.BOM_UTF8 + "统一入口 UTF-8 BOM".encode("utf-8"),
            "统一入口 GBK 编码内容".encode("gbk"),
            "统一入口 GB18030 编码内容".encode("gb18030"),
            b"plain ascii content",
        ],
        ids=["utf8", "utf8-bom", "gbk", "gb18030", "ascii"],
    )
    def test_three_entries_agree(self, tmp_path, payload):
        src = tmp_path / "sample.txt"
        src.write_bytes(payload)
        expected = decode_bytes(payload)[0]

        # document_parser
        parsed = DocumentParser().parse(str(src), doc_type="general")
        assert expected.split("\n")[0].strip() in parsed.full_text

        # word_service -> docx
        from office_agent.services.word_service import WordService

        doc = WordService().read_file(str(src))
        docx_text = "\n".join(p.text for p in doc.paragraphs)
        assert expected.split("\n")[0].strip() in docx_text

        # slide_planner
        content = SlidePlanner()._read_document(str(src))
        assert expected.split("\n")[0].strip() in content

    def test_entries_reject_mojibake_consistently(self, tmp_path):
        # 乱码内容：document_parser 直接抛 TextDecodeError；
        # slide_planner 包装为 RuntimeError，但都不得静默接受。
        data = ("公司年度经营报告：各部门营收稳步增长，利润率持续提升，市场前景良好。" * 5).encode("utf-8") + b"\x80"
        src = tmp_path / "mojibake.txt"
        src.write_bytes(data)

        with pytest.raises(TextDecodeError):
            DocumentParser().parse(str(src), doc_type="general")

        from office_agent.services.word_service import WordService

        with pytest.raises(ValueError):  # TextDecodeError 是 ValueError 子类
            WordService().read_file(str(src))

        with pytest.raises(RuntimeError, match="编码无法识别"):
            SlidePlanner()._read_document(str(src))
