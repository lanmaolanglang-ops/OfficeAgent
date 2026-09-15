"""SSRF-safe HTTP helpers for user-configured provider endpoints.

Native providers keep their vendor-owned URLs.  Custom endpoints cross a wider
trust boundary: resolve once, validate every resolved address, and connect to
that exact numeric address so DNS cannot change between validation and use.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit


MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024
_ResolvedEndpoint = tuple[int, int, int, str, tuple]
_RFC1918 = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


class EndpointPolicyError(ValueError):
    """The configured URL violates the custom-endpoint safety policy."""


@dataclass
class ProviderHTTPError(RuntimeError):
    status: int
    message: str

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.message}" if self.message else f"HTTP {self.status}"


def normalize_api_base_url(value: str) -> str:
    """Normalize an API base without ever manufacturing ``/v1/v1`` paths."""
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise EndpointPolicyError("服务地址格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EndpointPolicyError("服务地址必须是完整的 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise EndpointPolicyError("服务地址不能包含凭据、查询参数或片段")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/images/generations", "/messages", "/models"):
        if path.lower().endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")
            break
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def join_api_endpoint(base_url: str, endpoint: str) -> str:
    base = normalize_api_base_url(base_url)
    segment = str(endpoint or "").strip("/")
    if not segment:
        return base
    if base.lower().endswith(f"/{segment.lower()}"):
        return base
    return f"{base}/{segment}"


def _validate_request_url(value: str) -> SplitResult:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise EndpointPolicyError("服务地址格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EndpointPolicyError("服务地址必须是完整的 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise EndpointPolicyError("服务地址不能包含凭据或片段")
    return parsed


def _is_explicit_local(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_loopback or any(address in network for network in _RFC1918)


def _validate_address(address: str, *, allow_local: bool) -> None:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    # Link-local includes cloud metadata endpoints and is never enabled by the
    # ordinary "allow local model" switch.
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        raise EndpointPolicyError("拒绝访问链路本地、元数据或保留地址")
    # Python classifies IPv6 loopback (::1) as reserved.  Honor the explicit
    # local-endpoint opt-in before the broad reserved-address guard while the
    # always-dangerous link-local/metadata range remains rejected above.
    if allow_local and _is_explicit_local(ip):
        return
    if ip.is_reserved:
        raise EndpointPolicyError("拒绝访问链路本地、元数据或保留地址")
    if ip.is_global:
        return
    raise EndpointPolicyError("服务地址解析到本机或私有网络；如确需连接本地模型，请显式启用")


def resolve_endpoint(url: str, *, allow_local: bool = False) -> tuple[SplitResult, tuple[_ResolvedEndpoint, ...]]:
    parsed = _validate_request_url(url)
    host = cast(str, parsed.hostname).rstrip(".").lower()
    if (host == "localhost" or host.endswith(".localhost")) and not allow_local:
        raise EndpointPolicyError("localhost 端点需要显式启用本地连接")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        endpoints = tuple(cast(
            list[_ResolvedEndpoint],
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
        ))
    except OSError as exc:
        raise EndpointPolicyError("服务地址无法解析") from exc
    if not endpoints:
        raise EndpointPolicyError("服务地址无法解析")
    for item in endpoints:
        _validate_address(str(item[4][0]), allow_local=allow_local)
    return parsed, endpoints


def _connect(endpoint: _ResolvedEndpoint, timeout: float) -> socket.socket:
    family, socktype, proto, _, sockaddr = endpoint
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    try:
        sock.connect(sockaddr)
    except Exception:
        sock.close()
        raise
    return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, endpoint: _ResolvedEndpoint, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._endpoint = endpoint

    def connect(self) -> None:
        self.sock = _connect(self._endpoint, cast(float, self.timeout))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, endpoint: _ResolvedEndpoint, timeout: float):
        self._context = ssl.create_default_context()
        super().__init__(host, port=port, timeout=timeout, context=self._context)
        self._endpoint = endpoint

    def connect(self) -> None:
        raw = _connect(self._endpoint, cast(float, self.timeout))
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _host_header(host: str, port: int, scheme: str) -> str:
    rendered = f"[{host}]" if ":" in host else host
    return rendered if port == (443 if scheme == "https" else 80) else f"{rendered}:{port}"


def _read_limited(response: http.client.HTTPResponse, limit: int) -> bytes:
    header = response.getheader("Content-Length")
    if header and header.isdigit() and int(header) > limit:
        raise EndpointPolicyError("服务响应超过大小限制")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise EndpointPolicyError("服务响应超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_error_message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                value = error.get("message") or error.get("detail")
                if isinstance(value, str):
                    return value[:300]
    except (TypeError, ValueError):
        pass
    return text[:300]


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    allow_local: bool = False,
    max_redirects: int = 3,
) -> dict[str, Any]:
    """Issue a bounded JSON request using DNS-pinned sockets.

    Redirects are permitted only for GET/HEAD and only within the same origin;
    credentials are therefore never forwarded to another host.
    """
    current = url
    original = _validate_request_url(url)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")

    for redirect_index in range(max_redirects + 1):
        parsed, endpoints = resolve_endpoint(current, allow_local=allow_local)
        host = cast(str, parsed.hostname).rstrip(".").lower()
        ascii_host = host.encode("idna").decode("ascii")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        last_error: BaseException | None = None
        for endpoint in endpoints:
            connection: http.client.HTTPConnection
            if parsed.scheme == "https":
                connection = _PinnedHTTPSConnection(ascii_host, port, endpoint, timeout)
            else:
                connection = _PinnedHTTPConnection(ascii_host, port, endpoint, timeout)
            try:
                connection.request(
                    method.upper(), target, body=body,
                    headers={
                        "Host": _host_header(ascii_host, port, parsed.scheme),
                        "User-Agent": "OfficeAgent-provider/1",
                        **request_headers,
                    },
                )
                response = connection.getresponse()
                response_body = _read_limited(response, MAX_JSON_RESPONSE_BYTES)
                status = response.status
                location = response.getheader("Location")
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                last_error = exc
                connection.close()
                continue
            finally:
                connection.close()

            if status in {301, 302, 303, 307, 308}:
                if method.upper() not in {"GET", "HEAD"} or not location:
                    raise EndpointPolicyError("服务返回了不安全的重定向")
                if redirect_index >= max_redirects:
                    raise EndpointPolicyError("服务重定向次数过多")
                destination = urlsplit(urljoin(current, location))
                original_port = original.port or (443 if original.scheme == "https" else 80)
                destination_port = destination.port or (443 if destination.scheme == "https" else 80)
                if (destination.scheme, destination.hostname, destination_port) != (
                    original.scheme, original.hostname, original_port,
                ):
                    raise EndpointPolicyError("拒绝将 Provider 凭据转发到其他主机")
                current = destination.geturl()
                break
            if not 200 <= status < 300:
                raise ProviderHTTPError(status, _safe_error_message(response_body))
            try:
                decoded = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise EndpointPolicyError("服务响应不是有效 JSON") from exc
            if not isinstance(decoded, dict):
                raise EndpointPolicyError("服务响应 JSON 根节点必须是对象")
            return decoded
        else:
            raise EndpointPolicyError(f"无法连接服务地址: {last_error or '连接失败'}")
    raise EndpointPolicyError("服务重定向次数过多")
