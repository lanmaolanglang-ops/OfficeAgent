"""
JWT (JSON Web Token) - 令牌生成与验证
使用标准库实现HS256签名，不依赖外部JWT库
"""
from __future__ import annotations

import json
import time
import hmac
import hashlib
import base64
import secrets
import os
import threading
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass

from ...runtime_config import get_data_root


def _b64encode(data: bytes) -> str:
    """Base64url编码（无填充）"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    """Base64url解码"""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


@dataclass
class TokenPayload:
    """Token载荷"""
    user_id: str
    username: str
    role: str
    exp: float  # 过期时间戳
    iat: float  # 签发时间戳
    jti: str    # 唯一ID
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d = {
            "sub": self.user_id,
            "username": self.username,
            "role": self.role,
            "exp": self.exp,
            "iat": self.iat,
            "jti": self.jti,
        }
        if self.extra:
            d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TokenPayload":
        return cls(
            user_id=data.get("sub", ""),
            username=data.get("username", ""),
            role=data.get("role", "guest"),
            exp=data.get("exp", 0),
            iat=data.get("iat", 0),
            jti=data.get("jti", ""),
            extra={k: v for k, v in data.items()
                   if k not in ("sub", "username", "role", "exp", "iat", "jti")},
        )

    def is_expired(self) -> bool:
        return time.time() >= self.exp


class JWTManager:
    """JWT令牌管理器"""

    def __init__(self, secret_key: str | None = None,
                 access_token_expire: int = 3600,
                 refresh_token_expire: int = 86400 * 7,
                 state_dir: str | Path | None = None):
        """
        Args:
            secret_key: 签名密钥；未提供时使用环境变量或本地持久化密钥
            access_token_expire: Access Token过期秒数（默认1小时）
            refresh_token_expire: Refresh Token过期秒数（默认7天）
        """
        self._state_dir = Path(state_dir) if state_dir else get_data_root() / "security"
        self._secret_path = self._state_dir / "jwt_secret"
        self._revocation_path = self._state_dir / "jwt_revocations.json"
        self._state_lock = threading.RLock()
        resolved_secret = secret_key or os.environ.get("OFFICE_AGENT_JWT_SECRET")
        if not resolved_secret:
            resolved_secret = self._load_or_create_secret()
        self.secret_key = resolved_secret.encode("utf-8")
        self.access_token_expire = access_token_expire
        self.refresh_token_expire = refresh_token_expire
        self._revocation_signature: tuple[int, int] | None = None
        self._revoked: dict[str, float] = self._load_revocations()

    def _ensure_state_dir(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, content: str) -> None:
        # 统一原子写工具：临时文件 + fsync + replace，失败不留半截内容。
        # 撤销清单与密钥材料属于敏感数据，替换前收紧为 0o600。
        from ...persistence import atomic_write_text

        self._ensure_state_dir()
        atomic_write_text(path, content, mode=0o600)

    def _load_or_create_secret(self) -> str:
        with self._state_lock:
            try:
                secret = self._secret_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                secret = ""
            if secret:
                return secret
            secret = secrets.token_urlsafe(48)
            self._atomic_write(self._secret_path, secret)
            return secret

    def _load_revocations(self) -> dict[str, float]:
        with self._state_lock:
            try:
                stat = self._revocation_path.stat()
                self._revocation_signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                self._revocation_signature = None
            try:
                raw = json.loads(self._revocation_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return {}
            now = time.time()
            return {
                str(jti): float(expires_at)
                for jti, expires_at in raw.items()
                if isinstance(expires_at, (int, float)) and expires_at > now
            }

    def _persist_revocations(self) -> None:
        now = time.time()
        self._revoked = {
            jti: expires_at for jti, expires_at in self._revoked.items()
            if expires_at > now
        }
        self._atomic_write(
            self._revocation_path,
            json.dumps(self._revoked, ensure_ascii=False, sort_keys=True),
        )
        stat = self._revocation_path.stat()
        self._revocation_signature = (stat.st_mtime_ns, stat.st_size)

    def _is_revoked(self, jti: str) -> bool:
        with self._state_lock:
            # A cheap metadata check keeps workers coherent while avoiding a
            # synchronous read + JSON parse on every authenticated request.
            try:
                stat = self._revocation_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                signature = None
            if signature != self._revocation_signature:
                self._revoked.update(self._load_revocations())
            return self._revoked.get(jti, 0) > time.time()

    def _sign(self, header_b64: str, payload_b64: str) -> str:
        """HMAC-SHA256签名"""
        message = f"{header_b64}.{payload_b64}".encode("ascii")
        sig = hmac.new(self.secret_key, message, hashlib.sha256).digest()
        return _b64encode(sig)

    def create_access_token(self, user_id: str, username: str,
                            role: str = "user",
                            extra: dict | None = None) -> str:
        """创建Access Token"""
        now = time.time()
        payload = TokenPayload(
            user_id=user_id,
            username=username,
            role=role,
            iat=now,
            exp=now + self.access_token_expire,
            jti=secrets.token_hex(8),
            extra=extra,
        )
        return self._encode(payload, token_type="access")

    def create_refresh_token(self, user_id: str, username: str,
                             role: str = "user") -> str:
        """创建Refresh Token"""
        now = time.time()
        payload = TokenPayload(
            user_id=user_id,
            username=username,
            role=role,
            iat=now,
            exp=now + self.refresh_token_expire,
            jti=secrets.token_hex(8),
        )
        return self._encode(payload, token_type="refresh")

    def _encode(self, payload: TokenPayload, token_type: str = "access") -> str:
        header = {"alg": "HS256", "typ": "JWT", "ttype": token_type}
        header_b64 = _b64encode(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = _b64encode(json.dumps(payload.to_dict(), separators=(",", ":")).encode())
        signature = self._sign(header_b64, payload_b64)
        return f"{header_b64}.{payload_b64}.{signature}"

    def decode(self, token: str, expected_type: str | None = "access") -> TokenPayload:
        """
        解码并验证Token
        Raises:
            ValueError: Token无效/过期/签名错误
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError("Token格式错误")
            header_b64, payload_b64, signature = parts

            # 验证签名
            expected_sig = self._sign(header_b64, payload_b64)
            if not hmac.compare_digest(signature, expected_sig):
                raise ValueError("签名验证失败")

            # 解码header
            header = json.loads(_b64decode(header_b64))
            if header.get("alg") != "HS256":
                raise ValueError("不支持的算法")
            if header.get("typ") != "JWT":
                raise ValueError("Token 类型头无效")
            token_type = header.get("ttype")
            if token_type not in {"access", "refresh"}:
                raise ValueError("Token 用途无效")
            if expected_type is not None and token_type != expected_type:
                raise ValueError(f"需要 {expected_type} token，收到 {token_type} token")

            # 解码payload
            payload_data = json.loads(_b64decode(payload_b64))
            payload = TokenPayload.from_dict(payload_data)

            # 检查过期
            if payload.is_expired():
                raise ValueError("Token已过期")

            # 检查是否已撤销
            if self._is_revoked(payload.jti):
                raise ValueError("Token已被撤销")

            return payload
        except ValueError:
            raise
        except (KeyError, TypeError) as e:
            # base64/JSON 解析错误均为 ValueError 子类，已由上一分支处理；
            # 这里只兜住 payload 结构缺字段/类型错误，编程错误直接传播。
            raise ValueError(f"Token无效: {e}")

    def refresh_access_token(self, refresh_token: str) -> str:
        """兼容入口：刷新 access，并撤销已消费的 refresh token。"""
        access, _refresh = self.rotate_refresh_token(refresh_token)
        return access

    def rotate_refresh_token(self, refresh_token: str) -> tuple[str, str]:
        """原子消费旧 refresh token，并签发一对新令牌。"""
        with self._state_lock:
            # Holding the lock across decode and persistence prevents two
            # concurrent refreshes from successfully consuming the same JTI.
            payload = self.decode(refresh_token, expected_type="refresh")
            new_access = self.create_access_token(
                user_id=payload.user_id,
                username=payload.username,
                role=payload.role,
            )
            new_refresh = self.create_refresh_token(
                user_id=payload.user_id,
                username=payload.username,
                role=payload.role,
            )
            self._revoked[payload.jti] = payload.exp
            self._persist_revocations()
        return new_access, new_refresh

    def revoke(self, token: str) -> bool:
        """撤销Token（登出）"""
        try:
            payload = self.decode(token, expected_type=None)
            with self._state_lock:
                self._revoked[payload.jti] = payload.exp
                self._persist_revocations()
            return True
        except ValueError:
            return False

    def verify(self, token: str) -> Optional[TokenPayload]:
        """验证Token，返回payload或None"""
        try:
            return self.decode(token)
        except ValueError:
            return None
