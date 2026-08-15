"""
Auth Adapter - 认证适配器
支持 Local Mode（本地模式，无需登录）和 Cloud Mode（云端模式，JWT认证）
"""
import os
import time
from typing import Optional, Protocol
from dataclasses import dataclass
from enum import Enum


class AuthMode(Enum):
    LOCAL = "local"
    CLOUD = "cloud"


@dataclass
class AuthResult:
    """认证结果"""
    authenticated: bool
    user_id: str = ""
    username: str = ""
    mode: AuthMode = AuthMode.LOCAL
    token: str = ""
    expires_at: float = 0
    permissions: list[str] = None
    error: str = ""

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = ["*"]  # 本地模式默认全部权限


class AuthProvider(Protocol):
    """认证提供者接口"""
    def authenticate(self, credentials: dict = None) -> AuthResult: ...
    def validate_token(self, token: str) -> AuthResult: ...
    def get_current_user(self) -> Optional[dict]: ...
    def logout(self) -> bool: ...


class LocalAuthProvider:
    """本地认证提供者 - 无需登录，默认本地用户"""

    def __init__(self, identity_manager=None):
        self._identity = identity_manager
        self._user = None

    def authenticate(self, credentials: dict = None) -> AuthResult:
        """本地认证：总是成功，使用本地用户配置"""
        if self._identity:
            user = self._identity.get_current_user()
            self._user = user
            return AuthResult(
                authenticated=True,
                user_id=user.user_id,
                username=user.username,
                mode=AuthMode.LOCAL,
                token="local_token",
                expires_at=time.time() + 86400 * 365 * 10,  # 10年
                permissions=["*"],
            )
        return AuthResult(
            authenticated=True,
            user_id="local_default",
            username="local_user",
            mode=AuthMode.LOCAL,
            permissions=["*"],
        )

    def validate_token(self, token: str) -> AuthResult:
        """本地模式下所有token都有效"""
        return AuthResult(
            authenticated=True,
            user_id=self._user.user_id if self._user else "local_default",
            mode=AuthMode.LOCAL,
            permissions=["*"],
        )

    def get_current_user(self) -> Optional[dict]:
        if self._user:
            return self._user.to_dict()
        return {"user_id": "local_default", "username": "local_user"}

    def logout(self) -> bool:
        return True  # 本地模式无需登出


class CloudAuthProvider:
    """云端认证提供者 - JWT认证（保留未来扩展）"""

    def __init__(self, api_base: str = "", jwt_manager=None):
        self._api_base = api_base
        self._jwt_manager = jwt_manager
        self._current_token = ""
        self._current_user = None

    def authenticate(self, credentials: dict = None) -> AuthResult:
        """云端认证：需要用户名密码或API Key"""
        if not credentials:
            return AuthResult(authenticated=False, error="需要认证凭据")
        # 保留JWT认证逻辑
        if self._jwt_manager and "username" in credentials:
            try:
                token = self._jwt_manager.create_token(
                    user_id=credentials.get("user_id", ""),
                    username=credentials["username"],
                )
                self._current_token = token
                return AuthResult(
                    authenticated=True,
                    user_id=credentials.get("user_id", ""),
                    username=credentials["username"],
                    mode=AuthMode.CLOUD,
                    token=token,
                    expires_at=time.time() + 3600,
                )
            except Exception as e:
                return AuthResult(authenticated=False, error=str(e))
        return AuthResult(authenticated=False, error="云端认证未配置")

    def validate_token(self, token: str) -> AuthResult:
        if self._jwt_manager:
            try:
                payload = self._jwt_manager.verify_token(token)
                return AuthResult(
                    authenticated=True,
                    user_id=payload.get("user_id", ""),
                    username=payload.get("username", ""),
                    mode=AuthMode.CLOUD,
                    token=token,
                )
            except Exception as e:
                return AuthResult(authenticated=False, error=str(e))
        return AuthResult(authenticated=False, error="JWT管理器未配置")

    def get_current_user(self) -> Optional[dict]:
        return self._current_user

    def logout(self) -> bool:
        self._current_token = ""
        self._current_user = None
        return True


class AuthAdapter:
    """
    认证适配器 - 根据配置切换本地/云端模式
    默认本地模式，无需登录
    """

    def __init__(self, mode: AuthMode = AuthMode.LOCAL, **kwargs):
        self._mode = mode
        self._provider: AuthProvider
        if mode == AuthMode.LOCAL:
            self._provider = LocalAuthProvider(kwargs.get("identity_manager"))
        else:
            self._provider = CloudAuthProvider(
                api_base=kwargs.get("api_base", ""),
                jwt_manager=kwargs.get("jwt_manager"),
            )

    @property
    def mode(self) -> AuthMode:
        return self._mode

    @property
    def provider(self) -> AuthProvider:
        return self._provider

    def authenticate(self, credentials: dict = None) -> AuthResult:
        return self._provider.authenticate(credentials)

    def validate_token(self, token: str) -> AuthResult:
        return self._provider.validate_token(token)

    def get_current_user(self) -> Optional[dict]:
        return self._provider.get_current_user()

    def logout(self) -> bool:
        return self._provider.logout()

    def is_local_mode(self) -> bool:
        return self._mode == AuthMode.LOCAL

    def require_auth(self) -> bool:
        """本地模式不需要认证"""
        return self._mode == AuthMode.CLOUD

    def get_permissions(self) -> list[str]:
        """获取当前用户权限"""
        result = self._provider.authenticate()
        return result.permissions or []

    def has_permission(self, permission: str) -> bool:
        """检查权限（本地模式默认全部允许）"""
        perms = self.get_permissions()
        return "*" in perms or permission in perms


# 全局实例
_auth_adapter: Optional[AuthAdapter] = None


def get_auth_adapter(mode: AuthMode = None, **kwargs) -> AuthAdapter:
    """获取认证适配器单例"""
    global _auth_adapter
    if _auth_adapter is None:
        if mode is None:
            mode_str = os.environ.get("AUTH_MODE", "local").lower()
            mode = AuthMode.CLOUD if mode_str == "cloud" else AuthMode.LOCAL
        _auth_adapter = AuthAdapter(mode=mode, **kwargs)
    return _auth_adapter


def reset_auth_adapter() -> None:
    """重置（用于测试）"""
    global _auth_adapter
    _auth_adapter = None
