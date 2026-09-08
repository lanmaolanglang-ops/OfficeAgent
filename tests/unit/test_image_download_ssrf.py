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
