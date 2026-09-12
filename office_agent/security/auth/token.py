"""持久化令牌生命周期管理。"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable
from weakref import WeakSet

from .jwt import JWTManager, TokenPayload

logger = logging.getLogger("office_agent.security.token")

# API Key 使用统计（last_used/use_count）是观测性数据，不参与认证判定、
# 配额或计费：认证路径不为它逐请求 COMMIT。内存累计 + 按键节流落库，
# 节流窗口内的少量增量在 flush/shutdown 时收尾（进程崩溃最多丢一个
# 窗口的观测计数，可接受）。
USAGE_FLUSH_INTERVAL_SECONDS = 60.0


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    TEMPORARY = "temporary"


@dataclass
class TokenInfo:
    token: str
    token_type: TokenType
    user_id: str
    role: str
    created_at: float
    expires_at: float
    is_active: bool = True
    revoked_at: float | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired


def _api_key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _timestamp(value: datetime | None, default: float) -> float:
    if value is None:
        return default
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class TokenManager:
    """JWT、数据库 API Key 与短期内存令牌的统一入口。"""

    # 存活实例登记：应用关闭时逐一 flush 各实例累计的使用统计。
    _live_instances: "WeakSet[TokenManager]" = WeakSet()

    def __init__(self, jwt_manager: JWTManager | None = None,
                 session_factory: Callable | None = None,
                 usage_flush_interval: float = USAGE_FLUSH_INTERVAL_SECONDS):
        self.jwt = jwt_manager or JWTManager()
        if session_factory is None:
            from office_agent.database import init_db
            from office_agent.database.session import SessionLocal
            init_db(drop_all=False)
            session_factory = SessionLocal
        self._session_factory = session_factory
        self._usage_flush_interval = max(0.0, float(usage_flush_interval))
        self._usage_lock = threading.Lock()
        # key_id -> {"last_used": utc, "count": int}；认证路径只做内存累计。
        self._pending_usage: dict[str, dict] = {}
        self._usage_persisted_at: dict[str, float] = {}
        # 仅保留兼容观测镜像；验证与撤销始终查询数据库。
        self._api_keys: dict[str, TokenInfo] = {}
        self._temp_tokens: dict[str, TokenInfo] = {}
        TokenManager._live_instances.add(self)

    # ------------------------------------------------------------
    # API Key 使用统计的节流落库
    # ------------------------------------------------------------

    def _record_usage(self, key_id: str, now: datetime) -> bool:
        """内存累计一次使用；返回是否达到该键的落库节流阈值。"""
        with self._usage_lock:
            pending = self._pending_usage.setdefault(
                key_id, {"last_used": now, "count": 0})
            if now > pending["last_used"]:
                pending["last_used"] = now
            pending["count"] += 1
            last_persisted = self._usage_persisted_at.get(key_id)
            due = (last_persisted is None or time.monotonic() - last_persisted
                   >= self._usage_flush_interval)
            if due:
                # 到达阈值即推进水位：后续请求继续只做内存累计。
                self._usage_persisted_at[key_id] = time.monotonic()
            return due

    def _pop_usage(self, key_id: str) -> dict | None:
        with self._usage_lock:
            return self._pending_usage.pop(key_id, None)

    def _restore_usage(self, key_id: str, pending: dict) -> None:
        """落库失败时把快照并回内存，下一个节流窗口重试，不丢计数。"""
        with self._usage_lock:
            existing = self._pending_usage.get(key_id)
            if existing is None:
                self._pending_usage[key_id] = pending
                return
            if pending["last_used"] > existing["last_used"]:
                existing["last_used"] = pending["last_used"]
            existing["count"] += pending["count"]

    def login(self, user_id: str, username: str, role: str = "user",
              extra: dict | None = None) -> dict:
        access = self.jwt.create_access_token(user_id, username, role, extra)
        refresh = self.jwt.create_refresh_token(user_id, username, role)
        return {"access_token": access, "refresh_token": refresh,
                "token_type": "bearer", "expires_in": self.jwt.access_token_expire}

    def refresh(self, refresh_token: str) -> dict:
        new_access, new_refresh = self.jwt.rotate_refresh_token(refresh_token)
        return {"access_token": new_access, "refresh_token": new_refresh,
                "token_type": "bearer",
                "expires_in": self.jwt.access_token_expire}

    def logout(self, token: str) -> bool:
        return self.jwt.revoke(token)

    def authenticate(self, token: str) -> TokenPayload | None:
        return self.jwt.verify(token)

    @staticmethod
    def _ensure_user(session, user_id: str, role: str):
        from sqlalchemy import inspect, text
        from office_agent.database.models import User
        from office_agent.database.time import utc_now
        user = session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                username=f"api-{hashlib.sha256(user_id.encode()).hexdigest()[:20]}",
                display_name=user_id,
                role=role,
                is_active=True,
            )
            session.add(user)
            session.flush()
        if not user.is_active:
            raise ValueError("不能为已停用用户创建 API Key")

        # 滚动升级期间，旧库的 API Key 外键仍可能指向 security_users。
        # 该镜像只满足旧约束，认证读取始终以 canonical user 表为准；007
        # 迁移会复制历史用户并删除旧表。
        inspector = inspect(session.connection())
        if "security_users" in inspector.get_table_names() and any(
            fk.get("referred_table") == "security_users"
            for fk in inspector.get_foreign_keys("security_api_keys")
        ):
            now = utc_now()
            session.execute(text(
                "INSERT INTO security_users (id, username, password_hash, role, is_active, "
                "is_verified, failed_login_count, extra, created_at, updated_at) "
                "SELECT :id, :username, '', :role, 1, 0, 0, '{}', :now, :now "
                "WHERE NOT EXISTS (SELECT 1 FROM security_users WHERE id=:id)"
            ), {"id": user.id, "username": user.username, "role": user.role, "now": now})
        return user

    def create_api_key(self, user_id: str, role: str = "user",
                       description: str = "", expires_days: int = 365) -> str:
        if expires_days <= 0:
            raise ValueError("expires_days 必须大于 0")
        from office_agent.database.models import APIKeyModel
        from office_agent.database.time import utc_now

        api_key = f"oak_{secrets.token_urlsafe(32)}"
        now = utc_now()
        expires = now + timedelta(days=expires_days)
        with self._session_factory() as session:
            user = self._ensure_user(session, user_id, role)
            session.add(APIKeyModel(
                user_id=user.id,
                key_hash=_api_key_hash(api_key),
                key_prefix=api_key[:12],
                description=description,
                expires_at=expires,
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            session.commit()
        self._api_keys[api_key] = TokenInfo(
            token=api_key, token_type=TokenType.API_KEY, user_id=user_id,
            role=role, created_at=now.timestamp(), expires_at=expires.timestamp(),
            metadata={"description": description},
        )
        return api_key

    def import_legacy_api_keys(self, api_keys: list[str],
                               user_id: str = "local_api_user") -> int:
        """把环境变量中的历史 Key 幂等导入数据库，明文不落盘。"""
        from sqlalchemy import select
        from office_agent.database.models import APIKeyModel
        from office_agent.database.time import utc_now

        clean = [key.strip() for key in api_keys if key and key.strip()]
        if not clean:
            return 0
        imported = 0
        now = utc_now()
        with self._session_factory() as session:
            user = self._ensure_user(session, user_id, "admin")
            for api_key in clean:
                digest = _api_key_hash(api_key)
                if session.scalar(select(APIKeyModel.id).where(APIKeyModel.key_hash == digest)):
                    continue
                session.add(APIKeyModel(
                    user_id=user.id, key_hash=digest, key_prefix=api_key[:12],
                    name="legacy-environment-key", is_active=True,
                    created_at=now, updated_at=now,
                ))
                imported += 1
            session.commit()
        return imported

    def verify_api_key(self, api_key: str) -> TokenInfo | None:
        if not api_key:
            return None
        from sqlalchemy import select
        from office_agent.database.models import APIKeyModel, User
        from office_agent.database.time import utc_now

        digest = _api_key_hash(api_key)
        now = utc_now()
        with self._session_factory() as session:
            row = session.scalar(select(APIKeyModel).where(APIKeyModel.key_hash == digest))
            if row is None or not secrets.compare_digest(row.key_hash, digest):
                return None
            user = session.get(User, row.user_id)
            expires_at = _timestamp(row.expires_at, float("inf"))
            if not row.is_active or user is None or not user.is_active or expires_at <= now.timestamp():
                return None
            # 使用统计只做内存累计，达到节流阈值才在本会话写一次；
            # 统计写失败不影响认证结果（fail-soft，观测数据可重试）。
            if self._record_usage(row.id, now):
                pending = self._pop_usage(row.id)
                if pending is not None:
                    try:
                        row.last_used = pending["last_used"]
                        row.use_count = (row.use_count or 0) + pending["count"]
                        session.commit()
                    except Exception as exc:
                        session.rollback()
                        self._restore_usage(row.id, pending)
                        logger.warning("API Key 使用统计落库失败，已回退到内存: %s", exc)
            return TokenInfo(
                token="", token_type=TokenType.API_KEY, user_id=row.user_id,
                role=user.role, created_at=_timestamp(row.created_at, now.timestamp()),
                expires_at=expires_at, metadata={"key_id": row.id, "name": row.name},
            )

    def flush_usage(self) -> int:
        """把内存中累计的 API Key 使用统计落库（应用关闭/测试收尾调用）。

        单事务批量写入；键已被删除时丢弃其计数。成功后推进各键的
        节流水位，避免重启后立即重写。
        """
        with self._usage_lock:
            pending_items = list(self._pending_usage.items())
            self._pending_usage.clear()
        if not pending_items:
            return 0
        from office_agent.database.models import APIKeyModel

        flushed = 0
        with self._session_factory() as session:
            for key_id, pending in pending_items:
                row = session.get(APIKeyModel, key_id)
                if row is None:
                    continue
                row.last_used = pending["last_used"]
                row.use_count = (row.use_count or 0) + pending["count"]
                flushed += 1
            session.commit()
        for key_id, _pending in pending_items:
            self._usage_persisted_at[key_id] = time.monotonic()
        return flushed

    def revoke_api_key(self, api_key: str) -> bool:
        from sqlalchemy import select
        from office_agent.database.models import APIKeyModel

        digest = _api_key_hash(api_key)
        with self._session_factory() as session:
            row = session.scalar(select(APIKeyModel).where(APIKeyModel.key_hash == digest))
            if row is None:
                return False
            row.is_active = False
            session.commit()
        cached = self._api_keys.get(api_key)
        if cached:
            cached.is_active = False
            cached.revoked_at = time.time()
        return True

    def create_temp_token(self, user_id: str, purpose: str,
                          expires_seconds: int = 300) -> str:
        token = f"tmp_{secrets.token_urlsafe(24)}"
        now = time.time()
        self._temp_tokens[token] = TokenInfo(
            token=token, token_type=TokenType.TEMPORARY, user_id=user_id,
            role="user", created_at=now, expires_at=now + expires_seconds,
            metadata={"purpose": purpose},
        )
        return token

    def verify_temp_token(self, token: str, purpose: str | None = None) -> TokenInfo | None:
        info = self._temp_tokens.get(token)
        if info and info.is_valid and (purpose is None or info.metadata.get("purpose") == purpose):
            return info
        return None

    def cleanup_expired(self) -> int:
        from sqlalchemy import select
        from office_agent.database.models import APIKeyModel
        from office_agent.database.time import utc_now

        now = utc_now()
        with self._session_factory() as session:
            expired = list(session.scalars(select(APIKeyModel).where(
                APIKeyModel.is_active.is_(True), APIKeyModel.expires_at.is_not(None),
                APIKeyModel.expires_at <= now,
            )))
            for row in expired:
                row.is_active = False
            # 兼容旧调用方修改观测镜像的测试/运维脚本，但数据库仍是最终状态。
            for key, info in list(self._api_keys.items()):
                if info.expires_at <= now.timestamp():
                    digest = _api_key_hash(key)
                    row = session.scalar(select(APIKeyModel).where(APIKeyModel.key_hash == digest))
                    if row is not None and row.is_active:
                        row.is_active = False
                        expired.append(row)
                    self._api_keys.pop(key, None)
            session.commit()
        self._temp_tokens = {key: info for key, info in self._temp_tokens.items()
                             if info.expires_at > now.timestamp()}
        return len({row.id for row in expired})


def flush_all_token_usage() -> int:
    """冲洗所有存活 TokenManager 实例累计的 API Key 使用统计。

    供应用关闭路径调用（各实例的认证中间件/启动任务各自持有实例）；
    实例级失败只记录，不阻断其它实例的收尾。
    """
    flushed = 0
    for manager in list(TokenManager._live_instances):
        try:
            flushed += manager.flush_usage()
        except Exception:
            logger.warning("TokenManager 使用统计关闭冲洗失败", exc_info=True)
    return flushed
