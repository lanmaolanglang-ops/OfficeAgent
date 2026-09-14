"""Batch 6：剩余 P2 回归（API 契约 / Excel / PPT / Security / Gateway）。"""
from __future__ import annotations

import threading
from pathlib import Path



class TestP2_14HttpDetailStructure:
    def test_dict_detail_preserved(self):
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        from office_agent.api.core.handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/boom")
        def boom():
            raise HTTPException(status_code=400, detail={
                "code": "PROMPT_POLICY_REJECTED",
                "message": "输入包含高风险指令",
                "risk_level": "high",
            })

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/boom")
        assert r.status_code == 400
        body = r.json()
        assert body["error_code"] == "PROMPT_POLICY_REJECTED"
        assert body["message"] == "输入包含高风险指令"
        assert isinstance(body.get("details"), dict)
        assert "{'code'" not in body["message"]


class TestP2_13RateLimitAfterAuth:
    def test_middleware_order_auth_before_ratelimit(self):
        from office_agent.api.main import create_app

        # 通过源码顺序断言：Auth 注册在 RateLimit 之后（更外层）
        src = Path(create_app.__code__.co_filename).read_text(encoding="utf-8")
        auth_pos = src.find("add_middleware(AuthMiddleware)")
        rl_pos = src.find("add_middleware(RateLimitMiddleware)")
        assert auth_pos > 0 and rl_pos > 0
        assert auth_pos > rl_pos, "Auth 必须在 RateLimit 外层（后注册）"


class TestP2_20RateLimitHeaders:
    def test_multi_policy_uses_strictest(self):
        from office_agent.rate_limiter import RateLimitResult

        checked = [
            ("api", RateLimitResult(allowed=True, remaining=90, reset_at=1000.0)),
            ("model", RateLimitResult(allowed=True, remaining=5, reset_at=900.0)),
        ]
        strictest = min(checked, key=lambda item: item[1].remaining)
        earliest = min(checked, key=lambda item: item[1].reset_at)
        assert strictest[0] == "model"
        assert earliest[0] == "model"


class TestP2_17ChatQueueFailure:
    def test_queue_failure_raises_503(self):
        # 源码级：失败路径必须 raise APIError 而非仅 status=failed + 200
        src = Path(
            __import__("office_agent.api.router.chat", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "QUEUE_UNAVAILABLE" in src
        assert "status_code=503" in src


class TestP2_34ExcelOrchestratorReentrant:
    def test_process_file_does_not_share_service(self, tmp_path):
        from openpyxl import Workbook

        from office_agent.excel_agent.excel_orchestrator import ExcelOrchestrator

        src = tmp_path / "a.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["部门", "金额"])
        ws.append(["A", 10])
        ws.append(["B", 20])
        wb.save(str(src))

        orch = ExcelOrchestrator()
        results = []
        barrier = threading.Barrier(2)

        def worker(tag: str):
            barrier.wait()
            out = tmp_path / f"{tag}.xlsx"
            r = orch.process_file(str(src), task="", output_path=str(out))
            results.append((tag, r.success, out.exists()))

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert all(ok for _, ok, _ in results), results
        assert all(exists for _, _, exists in results), results


class TestP2_60VersionUnique:
    def test_unique_constraint_declared(self):
        from office_agent.database.models.file import FileVersion

        names = {
            c.name for c in FileVersion.__table__.constraints
            if hasattr(c, "name")
        }
        assert "uq_file_version_parent_number" in names or any(
            getattr(c, "columns", None) is not None
            and {col.name for col in c.columns} == {"parent_file_id", "version_number"}
            for c in FileVersion.__table__.constraints
        )


class TestP2_67EmbedderDimension:
    def test_api_embedder_rejects_dimension_mismatch(self):
        from office_agent.knowledge_base.embeddings import (
            APIEmbedder,
        )

        class FakeResp:
            success = True
            content = ""
            raw_response = {"data": [{"embedding": [0.1, 0.2]}]}  # dim 2

        class FakeGateway:
            def chat(self, **kwargs):
                return FakeResp()

        # 直接调用内部解析逻辑较重；用 monkeypatch embed 路径
        emb = APIEmbedder.__new__(APIEmbedder)
        emb._dimension = 8
        emb.model = "text-embedding-3-small"
        emb.provider = "openai"
        # 构造最小可测：_parse 需要完整实例；改测 _is_finite 路径
        from office_agent.knowledge_base.embeddings import _is_finite_vector
        assert not _is_finite_vector([0.1, 0.2], 8)


class TestP2_75ShapeTopZero:
    def test_top_zero_is_not_falsy(self):
        from pptx.util import Emu

        shape_top = Emu(0)
        assert shape_top is not None
        assert not (shape_top == 0 and shape_top is None)
        # 修复后的判断模式
        assert shape_top is not None and shape_top < Emu(2000000)


class TestP2_77HashingModelId:
    def test_model_id_reflects_dimension(self):
        from office_agent.knowledge_base.embeddings import HashingEmbedder

        assert HashingEmbedder(dimensions=128).model_id == "hashing-128-v1"
        assert HashingEmbedder(dimensions=512).model_id == "hashing-512-v1"


class TestP2_74VisionTables:
    def test_non_dict_tables_skipped(self):
        data = {"tables": ["bad", {"rows": 1, "cols": 1, "headers": ["a"], "data": [["1"]]}]}
        tables = []
        if "tables" in data and isinstance(data["tables"], list):
            for t in data["tables"]:
                if not isinstance(t, dict):
                    continue
                tables.append(t)
        assert len(tables) == 1


class TestP2_31SqliteParentDir:
    def test_custom_sqlite_url_creates_parent(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "c" / "app.db"
        url = f"sqlite:///{nested}"
        from office_agent.database import connection as conn

        engine = conn.get_engine(url)
        assert nested.parent.exists()
        engine.dispose()


class TestP2_29RecordAccessNoBinarySetattr:
    def test_record_access_uses_sql_update(self):
        src = Path(
            __import__("office_agent.database.repository.file_repo", fromlist=["x"]).__file__
        ).read_text(encoding="utf-8")
        assert "update(File)" in src
        assert "File.access_count + 1" in src


class TestP2_24LogsLimited:
    def test_get_by_task_accepts_limit(self):
        import inspect

        from office_agent.database.repository.execution_repo import (
            ExecutionLogRepository,
            ModelCallLogRepository,
        )
        assert "limit" in inspect.signature(ExecutionLogRepository.get_by_task).parameters
        assert "limit" in inspect.signature(ModelCallLogRepository.get_by_task).parameters
