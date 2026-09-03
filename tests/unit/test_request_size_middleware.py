"""RequestSizeLimitMiddleware 专项回归。

覆盖根因：超限上传此前依赖路由内 413 检查，但非 multipart 请求会先被
FastAPI 依赖校验以 422 拦下，拒绝行为随 Content-Type 不稳定；前置守卫
统一为确定性 413 且不读取请求体。multipart 开销口径与路由内一致。
"""
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from office_agent.api.middleware.request_size import RequestSizeLimitMiddleware


def _make_client(max_file_size=1024, multipart_overhead=512):
    async def upload(request):
        return PlainTextResponse("ok")

    async def other(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/api/file/upload", upload, methods=["POST"]),
        Route("/api/file/upload/multipart/init", upload, methods=["POST"]),
        Route("/api/other", other, methods=["POST"]),
    ])
    app.add_middleware(RequestSizeLimitMiddleware,
                       max_file_size=max_file_size,
                       multipart_overhead=multipart_overhead)
    return TestClient(app)


def test_oversize_raw_body_gets_deterministic_413():
    """非 multipart 超限请求不再落到 422，而是确定性 413。"""
    client = _make_client(max_file_size=1024)
    resp = client.post("/api/file/upload", content=b"B" * 2048,
                       headers={"Content-Type": "application/octet-stream"})
    assert resp.status_code == 413


def test_oversize_multipart_beyond_overhead_gets_413():
    """multipart 允许协议开销，但超出 文件上限+开销 仍 413。"""
    client = _make_client(max_file_size=1024, multipart_overhead=512)
    resp = client.post(
        "/api/file/upload", content=b"B" * 2048,
        headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert resp.status_code == 413


def test_multipart_within_overhead_passes_to_route():
    """multipart 声明大小在 文件上限+开销 内时放行给路由校验。"""
    client = _make_client(max_file_size=1024, multipart_overhead=512)
    resp = client.post(
        "/api/file/upload", content=b"B" * 1200,
        headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert resp.status_code == 200


def test_oversize_multipart_subroutes_also_guarded():
    client = _make_client(max_file_size=1024)
    resp = client.post("/api/file/upload/multipart/init",
                       content=b"B" * 2048)
    assert resp.status_code == 413


def test_normal_size_and_other_routes_pass_through():
    client = _make_client(max_file_size=1024)
    assert client.post("/api/file/upload", content=b"small").status_code == 200
    # 非上传路由不受守卫限制
    assert client.post("/api/other", content=b"B" * 2048).status_code == 200


def test_missing_content_length_passes_to_route_validation():
    """没有 Content-Length 时放行，由路由/存储层做实际大小校验。"""
    client = _make_client(max_file_size=1024)
    resp = client.post("/api/file/upload", content=b"")
    assert resp.status_code == 200
