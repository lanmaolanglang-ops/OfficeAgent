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
from typing import Any, Optional
from dataclasses import dataclass


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
                 refresh_token_expire: int = 86400 * 7):
        """
        Args:
            secret_key: 签名密钥，默认随机生成（生产环境必须从配置读取）
            access_token_expire: Access Token过期秒数（默认1小时）
            refresh_token_expire: Refresh Token过期秒数（默认7天）
        """
        self.secret_key = (secret_key or secrets.token_hex(32)).encode("utf-8")
        self.access_token_expire = access_token_expire
        self.refresh_token_expire = refresh_token_expire
        # 已撤销的token列表（生产环境用Redis）
        self._revoked: set[str] = set()

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

    def decode(self, token: str) -> TokenPayload:
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

            # 解码payload
            payload_data = json.loads(_b64decode(payload_b64))
            payload = TokenPayload.from_dict(payload_data)

            # 检查过期
            if payload.is_expired():
                raise ValueError("Token已过期")

            # 检查是否已撤销
            if payload.jti in self._revoked:
                raise ValueError("Token已被撤销")

            return payload
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Token无效: {e}")

    def refresh_access_token(self, refresh_token: str) -> str:
        """使用Refresh Token获取新的Access Token"""
        payload = self.decode(refresh_token)
        return self.create_access_token(
            user_id=payload.user_id,
            username=payload.username,
            role=payload.role,
        )

    def revoke(self, token: str) -> bool:
        """撤销Token（登出）"""
        try:
            payload = self.decode(token)
            self._revoked.add(payload.jti)
            return True
        except ValueError:
            return False

    def verify(self, token: str) -> Optional[TokenPayload]:
        """验证Token，返回payload或None"""
        try:
            return self.decode(token)
        except ValueError:
            return None
