"""
专项回归：async 路由不再把同步磁盘 IO 压在事件循环上。

覆盖清单条目：
- 1.6 async 文件路由仍执行同步磁盘 IO，可能阻塞事件循环
- 3.3 file.py 中的同步磁盘 IO
- 1.6 每次 worker 健康检查都新建 ModelGateway，开销和副作用重复
"""
import asyncio
import io
import time
import types

from office_agent.api.router import health as health_router


class _BlockingUpload:
    """模拟同步上传：只有被放到工作线程时才不会阻塞事件循环。"""

    def __init__(self, delay=0.3):
        self.delay = delay
        self.thread_name = None
        self.config = types.SimpleNamespace(max_file_size=1024 * 1024)

    def upload_fileobj(self, **kwargs):
        self.thread_name = _current_thread_name()
        self.kwargs = kwargs
        time.sleep(self.delay)
        return _FakeInfo()


class _FakeInfo:
    file_id = "file_test"
    original_name = "demo.txt"
    file_type = "text"
    size = 4


def _current_thread_name():
    import threading

    return threading.current_thread().name


def test_upload_route_runs_storage_io_off_the_event_loop(monkeypatch):
    """上传的磁盘写入必须在工作线程执行，否则会拖垮整个事件循环。"""
    import office_agent.api.router.file as file_router

    fake_storage = _BlockingUpload(delay=0.3)
    monkeypatch.setattr(file_router, "_get_storage", lambda: fake_storage)
    monkeypatch.setattr(file_router.settings, "auth_enabled", False)

    async def _drive():
        loop = asyncio.get_running_loop()
        main_thread = _current_thread_name()

        # 事件循环自身的探针：若上传阻塞循环，这批 tick 会被整体推迟
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        started = loop.time()
        upload = _FakeUploadFile(b"abcd")
        await file_router.upload_file(request=_FakeRequest(), file=upload)
        elapsed = loop.time() - started
        tick_task.cancel()
        return main_thread, ticks, elapsed

    main_thread, ticks, elapsed = asyncio.run(_drive())

    # 上传耗时约 0.3s，期间事件循环仍应推进十余次 tick
    assert elapsed >= 0.25, elapsed
    assert ticks >= 5, f"事件循环被阻塞，仅推进 {ticks} 次 tick"
    assert fake_storage.thread_name != main_thread


class _FakeRequest:
    headers = {"content-type": "multipart/form-data", "content-length": "4"}
    state = None


class _FakeUploadFile:
    def __init__(self, payload: bytes):
        self.filename = "demo.txt"
        self.content_type = "text/plain"
        self.file = io.BytesIO(payload)

    async def read(self, size: int = -1) -> bytes:
        return self.file.read(size)


def test_upload_part_runs_off_the_event_loop(monkeypatch):
    """分片落盘同样不能在事件循环里同步执行。"""
    import office_agent.api.router.file as file_router

    calls = {}

    class _Storage:
        def upload_part(self, file_id, upload_id, part_number, content):
            calls["thread"] = _current_thread_name()
            calls["args"] = (file_id, upload_id, part_number, content)
            return {"etag": "abc"}

    monkeypatch.setattr(file_router, "_get_storage", lambda: _Storage())

    result = asyncio.run(file_router.upload_part(
        file_id="file_1", upload_id="up_1", part_number=1,
        file=_FakeUploadFile(b"chunk-bytes"),
    ))

    assert result.data["etag"] == "abc"
    assert calls["args"][:3] == ("file_1", "up_1", 1)
    assert calls["thread"] != _current_thread_name()


def test_health_model_check_reuses_gateway_within_ttl(monkeypatch):
    """健康检查不应每次都重建 ModelGateway（每次都会读盘+抢配置锁）。"""
    constructions = []

    class _Manager:
        def list_available_models(self):
            return []

        def get_default_model(self):
            return None

    class _Gateway:
        def __init__(self, *args, **kwargs):
            constructions.append(object())
            self.manager = _Manager()

    class _Module:
        ModelGateway = _Gateway

    import sys
    import types

    module = types.ModuleType("office_agent.model_gateway")
    module.ModelGateway = _Gateway
    monkeypatch.setitem(sys.modules, "office_agent.model_gateway", module)

    # 重置缓存，确保本用例从冷缓存开始
    monkeypatch.setattr(health_router, "_cached_gateway", None)
    monkeypatch.setattr(health_router, "_cached_gateway_at", 0.0)
    monkeypatch.setattr(health_router, "_MODEL_GATEWAY_TTL_SECONDS", 30)

    for _ in range(5):
        health_router._check_models()

    assert len(constructions) == 1, f"重建了 {len(constructions)} 次 ModelGateway"


def test_health_model_check_rebuilds_after_ttl(monkeypatch):
    """TTL 过期后必须重建，避免配置变更永远不生效。"""
    constructions = []

    class _Manager:
        def list_available_models(self):
            return []

        def get_default_model(self):
            return None

    class _Gateway:
        def __init__(self, *args, **kwargs):
            constructions.append(object())
            self.manager = _Manager()

    import sys
    import types

    module = types.ModuleType("office_agent.model_gateway")
    module.ModelGateway = _Gateway
    monkeypatch.setitem(sys.modules, "office_agent.model_gateway", module)

    monkeypatch.setattr(health_router, "_cached_gateway", None)
    monkeypatch.setattr(health_router, "_cached_gateway_at", 0.0)
    monkeypatch.setattr(health_router, "_MODEL_GATEWAY_TTL_SECONDS", 0.0)

    health_router._check_models()
    health_router._check_models()

    assert len(constructions) == 2, "TTL 过期后应重建 ModelGateway"


def test_health_model_check_survives_broken_gateway(monkeypatch):
    """ModelGateway 构造失败时健康检查降级为 error，不能抛出。"""
    import sys
    import types

    class _Boom:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("models.json 损坏")

    module = types.ModuleType("office_agent.model_gateway")
    module.ModelGateway = _Boom
    monkeypatch.setitem(sys.modules, "office_agent.model_gateway", module)
    monkeypatch.setattr(health_router, "_cached_gateway", None)
    monkeypatch.setattr(health_router, "_cached_gateway_at", 0.0)

    result = health_router._check_models()
    assert result["status"] == "error"
