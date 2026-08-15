"""
Local Auth - 本地认证模块
"""
from .identity import LocalUserProfile, LocalIdentityManager
from .adapter import (
    AuthMode, AuthResult, AuthProvider,
    LocalAuthProvider, CloudAuthProvider, AuthAdapter,
    get_auth_adapter, reset_auth_adapter,
)

__all__ = [
    "LocalUserProfile", "LocalIdentityManager",
    "AuthMode", "AuthResult", "AuthProvider",
    "LocalAuthProvider", "CloudAuthProvider", "AuthAdapter",
    "get_auth_adapter", "reset_auth_adapter",
]
