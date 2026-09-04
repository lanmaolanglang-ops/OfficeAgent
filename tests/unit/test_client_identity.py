"""Trusted proxy 客户端身份解析回归。

根因（清单「限流」节）：限流/日志/审计直接使用 request.client.host，
反向代理后所有用户共享代理 IP；而旧限流实现虽仅信任回环 peer 的
X-Forwarded-For，却直接取链中最左值——该值恰恰是客户端可任意伪造的
部分，且信任边界（回环）是硬编码的，无法适配真实反代部署。

修复：security/client_identity.py 建立权威 resolver，仅当 TCP peer 属于
显式配置的 trusted proxies（OFFICE_AGENT_TRUSTED_PROXIES）时才消费
转发头，XFF 链从右向左剥离可信代理；rate limit / 请求日志 / 审计
四处消费点统一复用。
"""
from starlette.requests import Request

from office_agent.security.client_identity import (
    resolve_client_ip, resolve_request_client,
)

TRUSTED = ["10.0.0.1", "192.168.0.0/16"]


def _request(client_host=None, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "http_version": "1.1", "asgi": {"version": "3.0"},
        "method": "GET", "scheme": "http", "path": "/", "raw_path": b"/",
        "query_string": b"", "headers": raw,
        "server": ("127.0.0.1", 8765),
    }
    if client_host is not None:
        scope["client"] = (client_host, 50000)
    return Request(scope)


class TestResolveClientIp:
    def test_direct_client_without_headers_uses_peer(self):
        assert resolve_client_ip("203.0.113.9", {}, TRUSTED) == "203.0.113.9"

    def test_spoofed_forwarded_header_ignored_for_untrusted_peer(self):
        """公网客户端伪造 XFF 必须被忽略。"""
        headers = {"x-forwarded-for": "1.2.3.4"}
        assert resolve_client_ip("203.0.113.9", headers, TRUSTED) == "203.0.113.9"

    def test_no_trusted_proxies_configured_ignores_forwarded(self):
        headers = {"x-forwarded-for": "1.2.3.4"}
        assert resolve_client_ip("10.0.0.1", headers, []) == "10.0.0.1"
        assert resolve_client_ip("10.0.0.1", headers, None) == "10.0.0.1"

    def test_trusted_proxy_single_ip(self):
        headers = {"x-forwarded-for": "198.51.100.7"}
        assert resolve_client_ip("10.0.0.1", headers, TRUSTED) == "198.51.100.7"

    def test_trusted_proxy_cidr(self):
        headers = {"x-forwarded-for": "198.51.100.7"}
        assert resolve_client_ip("192.168.1.10", headers, TRUSTED) == "198.51.100.7"

    def test_multi_hop_strips_only_trusted_proxies(self):
        """client, untrusted-p, trusted-p + trusted peer → untrusted-p。

        不可信代理左侧的地址（含自称的 client）可能是伪造的，不再采信。
        """
        headers = {"x-forwarded-for": "198.51.100.7, 172.16.0.5, 10.0.0.2"}
        trusted = ["10.0.0.0/8", "192.168.0.0/16"]
        # peer=10.0.0.1 可信；链尾 10.0.0.2 可信被剥离；
        # 172.16.0.5 是第一个不可信节点 → 客户端身份
        assert resolve_client_ip("10.0.0.1", headers, trusted) == "172.16.0.5"

    def test_multi_hop_all_trusted_returns_leftmost(self):
        headers = {"x-forwarded-for": "198.51.100.7, 192.168.1.1, 10.0.0.2"}
        # 10.0.0.2 不在 TRUSTED；把 10.0.0.0/8 整段置信后全链可信
        trusted = ["10.0.0.0/8", "192.168.0.0/16"]
        assert resolve_client_ip("10.0.0.1", headers, trusted) == "198.51.100.7"

    def test_invalid_entry_in_chain_fails_safely(self):
        headers = {"x-forwarded-for": "198.51.100.7, not-an-ip"}
        # 无法解析的条目止步并作为身份（不会抛 500，也不会误信左端）
        assert resolve_client_ip("10.0.0.1", headers, TRUSTED) == "not-an-ip"

    def test_trusted_peer_without_forwarded_header_falls_back_to_peer(self):
        assert resolve_client_ip("10.0.0.1", {}, TRUSTED) == "10.0.0.1"

    def test_x_real_ip_only_from_trusted_peer(self):
        headers = {"x-real-ip": "198.51.100.7"}
        assert resolve_client_ip("10.0.0.1", headers, TRUSTED) == "198.51.100.7"
        assert resolve_client_ip("203.0.113.9", headers, TRUSTED) == "203.0.113.9"

    def test_malformed_x_real_ip_falls_back_to_peer(self):
        headers = {"x-real-ip": "garbage"}
        assert resolve_client_ip("10.0.0.1", headers, TRUSTED) == "10.0.0.1"

    def test_invalid_trusted_config_entries_ignored(self):
        trusted = ["bad-entry", "", "10.0.0.1"]
        headers = {"x-forwarded-for": "198.51.100.7"}
        assert resolve_client_ip("10.0.0.1", headers, trusted) == "198.51.100.7"

    def test_empty_or_missing_peer(self):
        assert resolve_client_ip(None, {}, TRUSTED) == "anonymous"
        assert resolve_client_ip("", {}, TRUSTED) == "anonymous"

    def test_unparseable_peer_never_trusts_headers(self):
        headers = {"x-forwarded-for": "1.2.3.4"}
        assert resolve_client_ip("bogus-peer", headers, TRUSTED) == "bogus-peer"

    def test_loopback_default_preserves_desktop_behavior(self):
        """默认配置（回环）下，本机代理的 XFF 仍被采信。"""
        headers = {"x-forwarded-for": "198.51.100.7"}
        assert resolve_client_ip("127.0.0.1", headers,
                                 ["127.0.0.1", "::1"]) == "198.51.100.7"


class TestResolveRequestClient:
    def test_reads_settings_at_call_time(self, monkeypatch):
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        request = _request("10.0.0.1", {"x-forwarded-for": "198.51.100.7"})
        assert resolve_request_client(request) == "198.51.100.7"

    def test_untrusted_peer_spoofing_ignored(self, monkeypatch):
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        request = _request("203.0.113.9", {"x-forwarded-for": "1.2.3.4"})
        assert resolve_request_client(request) == "203.0.113.9"

    def test_no_client_scope(self, monkeypatch):
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        request = _request(None, {})
        assert resolve_request_client(request) == "anonymous"


class TestRateLimitIdentityIntegration:
    """限流身份：经同一代理的不同真实客户端必须进入不同 bucket。"""

    @staticmethod
    def _identity(request):
        from office_agent.api.middleware.rate_limit import RateLimitMiddleware
        return RateLimitMiddleware._identity(request)

    def test_distinct_real_clients_get_distinct_buckets(self, monkeypatch):
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        req_a = _request("10.0.0.1", {"x-forwarded-for": "198.51.100.7"})
        req_b = _request("10.0.0.1", {"x-forwarded-for": "198.51.100.8"})
        id_a = self._identity(req_a)
        id_b = self._identity(req_b)
        assert id_a == "198.51.100.7"
        assert id_b == "198.51.100.8"
        assert id_a != id_b

    def test_spoofed_clients_share_real_peer_bucket(self, monkeypatch):
        """伪造 XFF 的公网客户端无法逃逸自己 peer 的限流桶。"""
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        req_a = _request("203.0.113.9", {"x-forwarded-for": "1.1.1.1"})
        req_b = _request("203.0.113.9", {"x-forwarded-for": "2.2.2.2"})
        assert self._identity(req_a) == "203.0.113.9"
        assert self._identity(req_b) == "203.0.113.9"

    def test_authenticated_user_takes_precedence(self, monkeypatch):
        from office_agent.api.core.config import settings
        monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.1"])
        request = _request("10.0.0.1", {"x-forwarded-for": "198.51.100.7"})
        request.state.user_id = "user-42"
        assert self._identity(request) == "user-42"


class TestConfigWiring:
    def test_settings_default_trusted_proxies_are_loopback(self):
        from office_agent.api.core.config import APIConfig
        assert APIConfig().trusted_proxies == ["127.0.0.1", "::1"]

    def test_env_override_replaces_default(self, monkeypatch):
        monkeypatch.setenv("OFFICE_AGENT_TRUSTED_PROXIES",
                           "10.0.0.1, 192.168.0.0/16")
        from office_agent.api.core.config import APIConfig
        config = APIConfig.from_env()
        assert config.trusted_proxies == ["10.0.0.1", "192.168.0.0/16"]

    def test_env_empty_keeps_loopback_default(self, monkeypatch):
        monkeypatch.delenv("OFFICE_AGENT_TRUSTED_PROXIES", raising=False)
        from office_agent.api.core.config import APIConfig
        assert APIConfig.from_env().trusted_proxies == ["127.0.0.1", "::1"]
