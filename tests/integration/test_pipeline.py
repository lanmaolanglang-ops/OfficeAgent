"""
端到端管线集成测试

真实拉起 uvicorn 子进程（隔离数据目录 + 随机端口），覆盖：
上传 → 创建任务 → Worker 执行 → 输出登记 → 流式下载

另含 API 级安全断言：恶意 Origin 拦截、中文文件名 RFC 5987。
"""
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(url: str, origin: str = None, **kwargs):
    req = urllib.request.Request(url, **kwargs)
    if origin:
        req.add_header("Origin", origin)
    return req


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("pipeline_data")
    port = _free_port()
    env = dict(os.environ)
    env["OFFICE_AGENT_DATA_DIR"] = str(data_dir)
    env["OFFICE_AGENT_LOG_DIR"] = str(data_dir / "logs")
    env["TASK_SOFT_TIMEOUT"] = "120"
    env["LOG_DIR"] = str(data_dir / "logs")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "office_agent.api.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    healthy = False
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=3) as r:
                data = json.loads(r.read())
                if data.get("status") in ("healthy", "degraded"):
                    healthy = True
                    break
        except Exception:
            time.sleep(0.5)
    if not healthy:
        proc.terminate()
        pytest.fail("后端 60s 内未通过健康检查")
    yield base, data_dir
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestOriginGuard:
    def test_malicious_origin_blocked(self, backend):
        base, _ = backend
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(
                _req(f"{base}/api/task/", origin="https://evil.example.com"), timeout=10)
        assert exc_info.value.code == 403

    def test_tauri_origin_allowed(self, backend):
        base, _ = backend
        with urllib.request.urlopen(
                _req(f"{base}/api/task/", origin="http://tauri.localhost"), timeout=10) as r:
            assert r.status == 200


class TestUploadDownload:
    def test_upload_task_output_pipeline(self, backend):
        """上传 → 任务 → 输出登记 → 流式下载全链"""
        import openpyxl
        base, _ = backend

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "销售 数据"
        ws.append(["月份", "销售额"])
        for m, v in [("1月", 100), ("2月", 200)]:
            ws.append([m, v])
        buf = io.BytesIO()
        wb.save(buf)
        content = buf.getvalue()

        boundary = "----pipelinetest"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"季度销售 数据.xlsx\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() \
            + content + f"\r\n--{boundary}--\r\n".encode()
        req = _req(f"{base}/api/file/upload", origin="http://tauri.localhost",
                   data=body, method="POST",
                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            uploaded = json.loads(r.read())
        file_id = uploaded["data"]["file_id"]
        assert file_id.startswith("file_")

        # 创建任务
        req = _req(f"{base}/api/task/create", origin="http://tauri.localhost",
                   data=json.dumps({"task_type": "excel_analyze", "instruction": "计算合计",
                                    "file_ids": [file_id]}).encode(),
                   method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            created = json.loads(r.read())
        task_id = created["data"]["task_id"]
        assert created["data"]["status"] == "queued"

        # 轮询到终态
        final = None
        deadline = time.time() + 150
        while time.time() < deadline:
            with urllib.request.urlopen(
                    _req(f"{base}/api/task/{task_id}", origin="http://tauri.localhost"),
                    timeout=10) as r:
                t = json.loads(r.read())["data"]
            if t["status"] in ("success", "failed", "cancelled"):
                final = t
                break
            time.sleep(1.5)
        assert final is not None, "任务未在时限内完成"
        assert final["status"] == "success", final.get("error")
        assert final.get("output_files"), "成功任务必须登记输出文件"

        # 下载输出：流式 + RFC 5987 中文文件名
        out_id = final["output_files"][0]["file_id"]
        req = _req(f"{base}/api/file/download/{out_id}", origin="http://tauri.localhost")
        with urllib.request.urlopen(req, timeout=30) as r:
            blob = r.read()
            disposition = r.headers.get("Content-Disposition", "")
        assert blob[:2] == b"PK", "输出应为合法 xlsx（zip）"
        assert len(blob) > 1000
        assert "filename*=UTF-8''" in disposition
        assert "_processed" in final["output_files"][0]["filename"]

    def test_data_dir_redirect(self, backend):
        base, data_dir = backend
        assert (data_dir / "db" / "office_agent.db").is_file()


class TestUploadGuard:
    def test_oversize_upload_rejected(self, backend):
        """超过上限的真实请求体在进入内存前被 413 拒绝"""
        base, _ = backend
        max_size = 100 * 1024 * 1024
        body = b"B" * (max_size + 1)
        req = _req(f"{base}/api/file/upload", origin="http://tauri.localhost",
                   data=body, method="POST",
                   headers={"Content-Type": "application/octet-stream"})
        rejected = False
        try:
            urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            # 服务端在读取请求体前返回 413
            assert e.code == 413
            rejected = True
        except (ConnectionError, OSError):
            # 服务端提前关闭连接（同样未读取超限请求体）
            rejected = True
        finally:
            del body
        assert rejected, "超限上传未被拒绝"
