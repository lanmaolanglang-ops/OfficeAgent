"""P3-7/P3-10/P3-12：输入边界、CSV 编码链与分片核心不变量回归。

P3-7：分片协议为 **1-based**（API 契约"分片序号，从1开始"，封装层
StorageService 同口径）。此前核心层 ``LocalStorage.upload_part`` 对
part_number 零校验（负数/bool/浮点/超大值直接落盘）、``complete``
按调用方列表合并（重复编号内容翻倍、缺号静默产出截断文件），
HTTP 校验可被内部调用绕过。现核心层自带不变量。

P3-10：编码探测链去掉 latin-1（它永不失败，把真实编码错误静默变成
乱码数据）；``_csv_to_xlsx`` 转换失败时清理自己产生的临时文件。

P3-12：PDF 缺库不再"降级"到按文本读取二进制（乱码假装成功），改为
带安装指引的明确错误；ChunkConfig 在构造期校验 overlap < chunk_size
等不变量（overlap==size 会让切片 range(step=0) 崩溃、>size 静默丢块）。
"""
import pytest

from office_agent.storage.local_storage import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(str(tmp_path / "objects"))


class TestPartNumberInvariants:
    def _init(self, storage):
        return storage.init_multipart_upload("outputs/demo.pdf")

    def test_valid_flow_assembles_in_order(self, storage, tmp_path):
        upload_id = self._init(storage)
        for number, content in [(1, b"AAA"), (2, b"BBB"), (3, b"CCC")]:
            storage.upload_part("outputs/demo.pdf", upload_id, number, content)
        result = storage.complete_multipart_upload(
            "outputs/demo.pdf", upload_id,
            [{"part_number": 3}, {"part_number": 1}, {"part_number": 2}])
        assert result["size"] == 9
        full_path = (tmp_path / "objects" / "outputs" / "demo.pdf").resolve()
        assert full_path.read_bytes() == b"AAABBBCCC", "乱序上传按编号归并"

    @pytest.mark.parametrize("bad", [-1, 0, True, False, 1.5, "1", None,
                                     10001, 10**9])
    def test_invalid_part_numbers_rejected(self, storage, bad):
        upload_id = self._init(storage)
        with pytest.raises(ValueError):
            storage.upload_part("outputs/demo.pdf", upload_id, bad, b"data")

    def test_part_content_must_be_bytes(self, storage):
        upload_id = self._init(storage)
        with pytest.raises(ValueError):
            storage.upload_part("outputs/demo.pdf", upload_id, 1, "text")

    def test_malformed_upload_id_rejected(self, storage):
        with pytest.raises(ValueError):
            storage.upload_part("outputs/demo.pdf", "../../evil", 1, b"data")
        with pytest.raises(ValueError):
            storage.upload_part("outputs/demo.pdf", "mp_ZZ", 1, b"data")

    def test_part_number_cap_matches_object_storage_protocol(self, storage):
        upload_id = self._init(storage)
        with pytest.raises(ValueError, match="上限"):
            storage.upload_part("outputs/demo.pdf", upload_id, 10001, b"data")

    def test_complete_rejects_empty_and_non_list(self, storage):
        upload_id = self._init(storage)
        storage.upload_part("outputs/demo.pdf", upload_id, 1, b"data")
        with pytest.raises(ValueError):
            storage.complete_multipart_upload("outputs/demo.pdf", upload_id, [])
        with pytest.raises(ValueError):
            storage.complete_multipart_upload(
                "outputs/demo.pdf", upload_id, {"part_number": 1})

    def test_complete_rejects_duplicate_and_gaps(self, storage):
        """回归核心：[1,2,2]（重复+缺3）不得静默合并出内容翻倍的截断文件。"""
        upload_id = self._init(storage)
        for number in (1, 2):
            storage.upload_part("outputs/demo.pdf", upload_id, number, b"data")
        with pytest.raises(ValueError, match="重复"):
            storage.complete_multipart_upload(
                "outputs/demo.pdf", upload_id,
                [{"part_number": 1}, {"part_number": 2}, {"part_number": 2}])
        with pytest.raises(ValueError, match="连续"):
            storage.complete_multipart_upload(
                "outputs/demo.pdf", upload_id,
                [{"part_number": 1}, {"part_number": 3}])

    def test_complete_rejects_missing_part_file(self, storage):
        upload_id = self._init(storage)
        storage.upload_part("outputs/demo.pdf", upload_id, 1, b"AAA")
        # 列表连续 [1,2]，但 2 号分片文件从未上传
        with pytest.raises(ValueError, match="不存在"):
            storage.complete_multipart_upload(
                "outputs/demo.pdf", upload_id,
                [{"part_number": 1}, {"part_number": 2}])


class TestCsvEncodingChain:
    def test_utf8_and_bom(self, tmp_path):
        from office_agent.task_queue.tasks.excel_tasks import _read_csv_any_encoding
        plain = tmp_path / "plain.csv"
        plain.write_bytes("名称,数量\n钢笔,3".encode("utf-8"))
        assert list(_read_csv_any_encoding(str(plain)).columns) == ["名称", "数量"]

        bom = tmp_path / "bom.csv"
        bom.write_bytes("名称,数量\n钢笔,3".encode("utf-8-sig"))
        frame = _read_csv_any_encoding(str(bom))
        assert list(frame.columns) == ["名称", "数量"], \
            "BOM 必须剥离，不得让首列列名变成 \\ufeff名称"

    def test_gbk_and_gb18030(self, tmp_path):
        from office_agent.task_queue.tasks.excel_tasks import _read_csv_any_encoding
        gbk = tmp_path / "gbk.csv"
        gbk.write_bytes("名称,数量\n钢笔,3".encode("gbk"))
        frame = _read_csv_any_encoding(str(gbk))
        assert frame.iloc[0]["名称"] == "钢笔"

    def test_undecodable_bytes_raise_instead_of_mojibake(self, tmp_path):
        """回归核心：全部候选失败必须显式报错，不得 latin-1 静默乱码。"""
        from office_agent.task_queue.tasks.excel_tasks import _read_csv_any_encoding
        bad = tmp_path / "bad.csv"
        bad.write_bytes(b"\xff\xfe\x00\x00broken,name")
        with pytest.raises(RuntimeError, match="无法识别 CSV 文件编码"):
            _read_csv_any_encoding(str(bad))

    def test_latin1_removed_from_chain(self):
        from office_agent.task_queue.tasks import excel_tasks
        assert "latin-1" not in excel_tasks.CSV_ENCODINGS

    def test_csv_to_xlsx_cleans_temp_on_write_failure(self, tmp_path, monkeypatch):
        """to_excel 失败时转换函数自己清理临时文件（ownership 尚未移交）。"""
        import pandas as pd
        from office_agent.task_queue.tasks import excel_tasks

        source = tmp_path / "src.csv"
        source.write_bytes("a,b\n1,2".encode("utf-8"))
        assert source.exists()

        created = {}
        real_to_excel = pd.DataFrame.to_excel

        def failing_to_excel(self, path, *args, **kwargs):
            created["path"] = str(path)
            real_to_excel(self, path, *args, **kwargs)
            raise RuntimeError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_excel", failing_to_excel)
        with pytest.raises(RuntimeError, match="disk full"):
            excel_tasks._csv_to_xlsx(str(source))
        import os
        assert not os.path.exists(created["path"]), \
            "转换失败不得留下半截临时 xlsx"
        assert source.exists(), "用户原始 CSV 永不被清理"

    def test_csv_source_never_deleted_on_success(self, tmp_path):
        from office_agent.task_queue.tasks.excel_tasks import _csv_to_xlsx
        source = tmp_path / "src.csv"
        source.write_bytes("a,b\n1,2".encode("utf-8"))
        tmp_xlsx = _csv_to_xlsx(str(source))
        try:
            assert source.exists()
            assert tmp_xlsx.endswith(".xlsx")
        finally:
            import os
            if os.path.exists(tmp_xlsx):
                os.remove(tmp_xlsx)


class TestPdfDependencyAndChunkContract:
    def test_missing_pymupdf_raises_install_hint(self, monkeypatch, tmp_path):
        """回归核心：缺库不再"降级"按文本读二进制 PDF，而是明确报错。"""
        import sys
        from office_agent.knowledge_base.document_parser import DocumentParser

        monkeypatch.setitem(sys.modules, "pymupdf", None)
        parser = DocumentParser()
        with pytest.raises(RuntimeError, match="pip install pymupdf") as excinfo:
            parser._parse_pdf(str(tmp_path / "doc.pdf"), "general")
        assert isinstance(excinfo.value.__cause__, ImportError), \
            "必须保留原始 ImportError 作为 cause"

    def test_damaged_pdf_not_misjudged_as_missing_dependency(self, tmp_path):
        """损坏的 PDF 走 PyMuPDF 自己的异常，不得被当成缺库。"""
        from office_agent.knowledge_base.document_parser import DocumentParser
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 this is not a real pdf body")
        parser = DocumentParser()
        with pytest.raises(Exception) as excinfo:
            parser._parse_pdf(str(broken), "general")
        assert "pip install pymupdf" not in str(excinfo.value)

    @pytest.mark.parametrize("kwargs", [
        {"max_chunk_size": 0},
        {"max_chunk_size": -5},
        {"overlap": -1},
        {"overlap": 500},          # == max_chunk_size（默认500）
        {"overlap": 900},          # > max_chunk_size
        {"max_chunk_size": True},
    ])
    def test_invalid_chunk_config_rejected(self, kwargs):
        from office_agent.knowledge_base.text_chunker import ChunkConfig
        with pytest.raises(ValueError):
            ChunkConfig(**kwargs)

    def test_valid_boundary_configs_accepted(self):
        from office_agent.knowledge_base.text_chunker import ChunkConfig
        assert ChunkConfig(max_chunk_size=1, overlap=0).overlap == 0
        assert ChunkConfig(max_chunk_size=10, overlap=9).overlap == 9

    def test_long_text_terminates_with_max_overlap(self):
        """overlap = size-1（最容易卡死的合法配置）：有限时间完成且不丢块。"""
        from office_agent.knowledge_base.text_chunker import ChunkConfig, TextChunker
        from office_agent.knowledge_base.document_parser import ParsedDocument

        config = ChunkConfig(max_chunk_size=10, overlap=9, strategy="fixed")
        chunker = TextChunker(config)
        doc = ParsedDocument(title="t", doc_type="general", full_text="x" * 100)
        chunks = chunker.chunk_document(doc, doc_id="d")
        assert 0 < len(chunks) < 100, "切片必须有限且推进"
        assert all(len(c.content) <= 10 for c in chunks)

    def test_fixed_overlap_zero_multi_chunk(self):
        from office_agent.knowledge_base.text_chunker import ChunkConfig, TextChunker
        from office_agent.knowledge_base.document_parser import ParsedDocument

        config = ChunkConfig(max_chunk_size=10, overlap=0, min_chunk_size=1,
                             strategy="fixed")
        doc = ParsedDocument(title="t", doc_type="general", full_text="y" * 55)
        chunks = TextChunker(config).chunk_document(doc, doc_id="d")
        assert len(chunks) == 6  # 10+10+10+10+10+5
        assert b"".join(c.content.encode() for c in chunks).decode() == "y" * 55
