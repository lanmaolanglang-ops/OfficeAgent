"""
Password Utilities - 密码哈希与验证
使用标准库 hashlib + hmac + secrets 实现，不依赖外部包
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import base64
from typing import Tuple

logger = logging.getLogger(__name__)


# PBKDF2 参数
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 32
_HASH_BYTES = 32


def generate_salt(length: int = _SALT_BYTES) -> bytes:
    """生成随机盐值"""
    return secrets.token_bytes(length)


def hash_password(password: str, salt: bytes | None = None) -> str:
    """
    哈希密码
    返回格式: pbkdf2_sha256$iterations$salt_b64$hash_b64
    """
    if salt is None:
        salt = generate_salt()
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO,
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_HASH_BYTES,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    try:
        parts = password_hash.split("$")
        if len(parts) != 4:
            return False
        algo, iterations_str, salt_b64, expected_b64 = parts
        iterations = int(iterations_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(expected_b64)
        dk = hashlib.pbkdf2_hmac(
            algo.replace("pbkdf2_", ""),
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError) as exc:
        # 仅容忍畸形的存储哈希（fail-closed 返回 False）；
        # AttributeError 等编程错误必须传播，不得伪装成"密码错误"。
        # 日志只记录异常类型，绝不包含密码或哈希内容。
        logger.warning("拒绝畸形的存储密码哈希: %s", type(exc).__name__)
        return False


def generate_password(length: int = 16) -> str:
    """生成随机密码"""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def is_password_strong(password: str) -> Tuple[bool, list[str]]:
    """检查密码强度，返回(是否强, 问题列表)"""
    issues = []
    if len(password) < 8:
        issues.append("密码长度至少8位")
    if not any(c.isupper() for c in password):
        issues.append("需要包含大写字母")
    if not any(c.islower() for c in password):
        issues.append("需要包含小写字母")
    if not any(c.isdigit() for c in password):
        issues.append("需要包含数字")
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        issues.append("建议包含特殊字符")
    return (len(issues) == 0, issues)
