"""
Token Manager - 令牌生命周期管理
"""
from __future__ import annotations

import time
import secrets
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from .jwt import JWTManager, TokenPayload


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    TEMPORARY = "temporary"


@dataclass
class TokenInfo:
    """令牌信息"""
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


class TokenManager:
    """令牌管理器"""

    def __init__(self, jwt_manager: JWTManager | None = None):
        self.jwt = jwt_manager or JWTManager()
        self._api_keys: dict[str, TokenInfo] = {}  # api_key -> info
        self._temp_tokens: dict[str, TokenInfo] = {}  # temp token -> info

    def login(self, user_id: str, username: str, role: str = "user",
              extra: dict | None = None) -> dict:
        """用户登录，返回access和refresh token"""
        access = self.jwt.create_access_token(user_id, username, role, extra)
        refresh = self.jwt.create_refresh_token(user_id, username, role)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": self.jwt.access_token_expire,
        }

    def refresh(self, refresh_token: str) -> dict:
        """刷新access token"""
        new_access = self.jwt.refresh_access_token(refresh_token)
        return {
            "access_token": new_access,
            "token_type": "bearer",
            "expires_in": self.jwt.access_token_expire,
        }

    def logout(self, token: str) -> bool:
        """登出，撤销token"""
        return self.jwt.revoke(token)

    def authenticate(self, token: str) -> TokenPayload | None:
        """认证请求，返回用户信息"""
        return self.jwt.verify(token)

    def create_api_key(self, user_id: str, role: str = "user",
                       description: str = "", expires_days: int = 365) -> str:
        """创建API Key"""
        api_key = f"oak_{secrets.token_urlsafe(32)}"
        now = time.time()
        info = TokenInfo(
            token=api_key,
            token_type=TokenType.API_KEY,
            user_id=user_id,
            role=role,
            created_at=now,
            expires_at=now + expires_days * 86400,
            metadata={"description": description},
        )
        self._api_keys[api_key] = info
        return api_key

    def verify_api_key(self, api_key: str) -> TokenInfo | None:
        """验证API Key"""
        info = self._api_keys.get(api_key)
        if info and info.is_valid:
            return info
        return None

    def revoke_api_key(self, api_key: str) -> bool:
        """撤销API Key"""
        if api_key in self._api_keys:
            self._api_keys[api_key].is_active = False
            self._api_keys[api_key].revoked_at = time.time()
            return True
        return False

    def create_temp_token(self, user_id: str, purpose: str,
                          expires_seconds: int = 300) -> str:
        """创建临时令牌（用于文件下载等）"""
        token = f"tmp_{secrets.token_urlsafe(24)}"
        now = time.time()
        self._temp_tokens[token] = TokenInfo(
            token=token,
            token_type=TokenType.TEMPORARY,
            user_id=user_id,
            role="user",
            created_at=now,
            expires_at=now + expires_seconds,
            metadata={"purpose": purpose},
        )
        return token

    def verify_temp_token(self, token: str, purpose: str | None = None) -> TokenInfo | None:
        """验证临时令牌"""
        info = self._temp_tokens.get(token)
        if info and info.is_valid:
            if purpose and info.metadata.get("purpose") != purpose:
                return None
            return info
        return None

    def cleanup_expired(self):
        """清理过期令牌"""
        now = time.time()
        self._api_keys = {k: v for k, v in self._api_keys.items()
                         if v.expires_at > now or v.is_active}
        self._temp_tokens = {k: v for k, v in self._temp_tokens.items()
                            if v.expires_at > now}
