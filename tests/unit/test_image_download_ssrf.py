"""Remote image downloads must pin validated DNS results to the socket."""
import socket

import pytest


PUBLIC_ENDPOINT = (
    socket.AF_INET,
    socket.SOCK_STREAM,
    socket.IPPROTO_TCP,
    "",
    ("93.184.216.34", 80),
)


class _Response:
    def __init__(self, status=200, body=b"image", location=None):
        self.status = status
        self._body = body
        self._location = location

    def getheader(self, name):
        if name == "Location":
            return self._location
        if name == "Content-Length":
            return str(len(self._body))
        return None

    def read(self, _size=-1):
        body, self._body = self._body, b""
        return body


class _Connection:
    responses = []
    endpoints = []

    def __init__(self, _host, _port, endpoint, _timeout):
        self.endpoints.append(endpoint)

    def request(self, *_args, **_kwargs):
        return None

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        return None


def test_download_connects_with_the_single_validated_dns_result(monkeypatch):
    from office_agent.image_generation import gateway

    dns_calls = []
    monkeypatch.setattr(
        gateway.socket,
        "getaddrinfo",
        lambda *args, **kwargs: dns_calls.append((args, kwargs)) or [PUBLIC_ENDPOINT],
    )
    _Connection.responses = [_Response(body=b"safe-image")]
    _Connection.endpoints = []
    monkeypatch.setattr(gateway, "_PinnedHTTPConnection", _Connection)

    assert gateway._get_bytes("http://images.example/picture.png") == b"safe-image"
    assert len(dns_calls) == 1
    assert _Connection.endpoints == [PUBLIC_ENDPOINT]


def test_redirect_is_resolved_and_validated_again(monkeypatch):
    from office_agent.image_generation import gateway

    answers = [
        [PUBLIC_ENDPOINT],
        [(
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("127.0.0.1", 80),
        )],
    ]
    monkeypatch.setattr(
        gateway.socket, "getaddrinfo", lambda *_args, **_kwargs: answers.pop(0)
    )
    _Connection.responses = [
        _Response(status=302, location="http://rebound.example/private.png")
    ]
    _Connection.endpoints = []
    monkeypatch.setattr(gateway, "_PinnedHTTPConnection", _Connection)

    with pytest.raises(gateway.ImageGenerationError, match="内网或保留地址"):
        gateway._get_bytes("http://images.example/picture.png")
    assert len(_Connection.endpoints) == 1


def test_pinned_socket_connect_does_not_resolve_hostname_again(monkeypatch):
    from office_agent.image_generation import gateway

    connected = []

    class FakeSocket:
        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, address):
            connected.append(address)

        def close(self):
            return None

    monkeypatch.setattr(
        gateway.socket,
        "socket",
        lambda family, socktype, proto: FakeSocket(),
    )
    monkeypatch.setattr(
        gateway.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pinned connection must not resolve again")
        ),
    )

    gateway._connect_endpoint(PUBLIC_ENDPOINT, 3.0)
    assert connected == [("93.184.216.34", 80)]


def test_dns_answer_is_rejected_if_any_endpoint_is_non_public(monkeypatch):
    from office_agent.image_generation import gateway

    private_endpoint = (
        socket.AF_INET,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
        "",
        ("127.0.0.1", 80),
    )
    monkeypatch.setattr(
        gateway.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [PUBLIC_ENDPOINT, private_endpoint],
    )

    with pytest.raises(gateway.ImageGenerationError, match="内网或保留地址"):
        gateway._validate_remote_url("http://images.example/picture.png")


# ------------------------------------------------------------
# P2-72: the request base_url itself must be public BEFORE a Bearer key is sent
# ------------------------------------------------------------


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.2.3.4", "192.168.1.1", "172.16.9.9",
    "169.254.1.1", "169.254.169.254", "::1",
])
def test_base_url_resolving_to_reserved_address_is_rejected(monkeypatch, ip):
    from office_agent.image_generation import gateway

    def fake_dns(host, port, *a, **k):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    monkeypatch.setattr(gateway.socket, "getaddrinfo", fake_dns)
    with pytest.raises(gateway.ImageGenerationError):
        gateway._assert_public_api_url("http://internal.example/v1", label="base_url")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://evil/x",
    "https://user:pass@api.example/v1",
    "http://localhost:8080/v1",
    "http://api.localhost/v1",
    "   ",
])
def test_base_url_scheme_credential_localhost_rejected(url):
    from office_agent.image_generation.gateway import _assert_public_api_url, ImageGenerationError

    with pytest.raises(ImageGenerationError):
        _assert_public_api_url(url, label="base_url")


def test_base_url_mixed_public_private_dns_rejected(monkeypatch):
    from office_agent.image_generation import gateway

    def fake_dns(*_a, **_k):
        return [PUBLIC_ENDPOINT, (
            socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)
        )]

    monkeypatch.setattr(gateway.socket, "getaddrinfo", fake_dns)
    with pytest.raises(gateway.ImageGenerationError, match="内网或保留地址"):
        gateway._assert_public_api_url("https://api.example/v1", label="base_url")


def test_base_url_public_endpoint_accepted(monkeypatch):
    from office_agent.image_generation import gateway

    monkeypatch.setattr(
        gateway.socket, "getaddrinfo", lambda *_a, **_k: [PUBLIC_ENDPOINT]
    )
    # must not raise
    gateway._assert_public_api_url("https://api.example/v1/", label="base_url")
