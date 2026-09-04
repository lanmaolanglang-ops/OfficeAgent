"""权威客户端身份解析（trusted proxy model）。

部署在反向代理（nginx / Caddy / Traefik / LB）之后时，TCP peer 是代理
地址，直接使用 ``request.client.host`` 会让所有用户共享同一个限流桶、
审计来源与日志身份。但 ``X-Forwarded-For`` / ``X-Real-IP`` 等转发头可被
公网客户端任意伪造，绝不能无条件信任。

本模块是唯一权威解析入口，供 rate limit / 请求日志 / 安全审计统一复用：

- 仅当当前 TCP peer 属于显式配置的 trusted proxies（IP 或 CIDR，
  ``settings.trusted_proxies``，环境变量 ``OFFICE_AGENT_TRUSTED_PROXIES``）
  时才消费转发头；
- ``X-Forwarded-For`` 链从右向左剥离可信代理，第一个不可信地址即真实
  客户端（不可信节点左侧的内容可能是伪造的，不再采信）；
- 任何格式损坏 / 无法解析的输入都安全回退，绝不抛异常；
- peer 不可信时一律忽略所有转发头，直接返回 peer。
"""
from __future__ import annotations

import ipaddress
from typing import Iterable, Mapping

# 解析结果的进程内缓存：key 为规范化后的配置元组。
_networks_cache: dict[tuple[str, ...], tuple] = {}

_ANONYMOUS = "anonymous"


def _parse_networks(entries: Iterable[str]) -> tuple:
    """把配置项解析为 ip_network 元组；无效条目静默忽略（fail-safe）。"""
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry.strip(), strict=False))
        except (ValueError, AttributeError):
            continue
    return tuple(networks)


def _trusted_networks(entries: Iterable[str] | None) -> tuple:
    key = tuple(e.strip() for e in (entries or ()) if e and e.strip())
    cached = _networks_cache.get(key)
    if cached is None:
        cached = _parse_networks(key)
        _networks_cache[key] = cached
    return cached


def _is_trusted(peer_ip, networks: tuple) -> bool:
    return any(peer_ip in network for network in networks)


def resolve_client_ip(
    peer: str | None,
    headers: Mapping[str, str] | None,
    trusted_entries: Iterable[str] | None,
) -> str:
    """解析真实客户端地址。

    参数：
        peer: 当前 TCP 对端地址（``request.client.host``），可为 None。
        headers: 请求头映射（大小写不敏感的 Starlette Headers 或普通 dict）。
        trusted_entries: trusted proxy 配置项（IP 或 CIDR 字符串）。

    返回：客户端身份字符串。无法确定时返回 ``peer`` 原值或 ``"anonymous"``。
    """
    peer = (peer or "").strip()
    if not peer:
        return _ANONYMOUS

    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        # peer 本身不是合法 IP（异常部署）：不采信任何转发头
        return peer

    networks = _trusted_networks(trusted_entries)
    if not networks or not _is_trusted(peer_ip, networks):
        # 未配置可信代理，或 peer 不在可信范围内：忽略全部转发头
        return peer

    get_header = headers.get if headers is not None else lambda _k, _d="": _d

    forwarded = (get_header("x-forwarded-for", "") or "").strip()
    if forwarded:
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        # 从右向左剥离可信代理；第一个不可信（或无法解析）的条目即客户端
        for candidate in reversed(chain):
            try:
                candidate_ip = ipaddress.ip_address(candidate)
            except ValueError:
                # 链中出现无法解析的条目：fail-safe，止步于此
                return candidate or peer
            if not _is_trusted(candidate_ip, networks):
                return str(candidate_ip)
        # 整条链全部可信：最左端是最早的可信观察点记录的客户端
        if chain:
            return chain[0]
        return peer

    real_ip = (get_header("x-real-ip", "") or "").strip()
    if real_ip:
        try:
            return str(ipaddress.ip_address(real_ip))
        except ValueError:
            return peer

    return peer


def resolve_request_client(request) -> str:
    """从 Starlette/FastAPI Request 解析客户端身份（读取当前 settings）。"""
    from ..api.core.config import settings  # 延迟导入，允许测试 monkeypatch

    peer = request.client.host if getattr(request, "client", None) else ""
    return resolve_client_ip(peer, request.headers, settings.trusted_proxies)
