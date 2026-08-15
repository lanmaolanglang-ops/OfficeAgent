"""
Local Credential Manager - 本地凭据管理器
安全保存API Key，禁止明文存储
使用Fernet对称加密（基于PBKDF2派生密钥）
"""
import os
import json
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@dataclass
class CredentialInfo:
    """凭据信息"""
    provider: str
    key_type: str  # api_key, api_secret, token
    created_at: float
    updated_at: float
    masked_value: str  # 掩码显示
    is_set: bool


class SimpleFernet:
    """简单加密实现（当cryptography不可用时使用）"""

    def __init__(self, key: bytes):
        self._key = key[:32] if len(key) >= 32 else key.ljust(32, b'0')

    def encrypt(self, data: bytes) -> bytes:
        # XOR加密 + base64（仅用于基础保护，生产环境建议安装cryptography）
        iv = secrets.token_bytes(16)
        result = bytearray()
        for i, byte in enumerate(data):
            result.append(byte ^ self._key[i % len(self._key)] ^ iv[i % len(iv)])
        return base64.b64encode(iv + bytes(result))

    def decrypt(self, token: bytes) -> bytes:
        data = base64.b64decode(token)
        iv = data[:16]
        encrypted = data[16:]
        result = bytearray()
        for i, byte in enumerate(encrypted):
            result.append(byte ^ self._key[i % len(self._key)] ^ iv[i % len(iv)])
        return bytes(result)


class LocalCredentialManager:
    """
    本地凭据管理器
    - API Key加密存储
    - 支持增删改查
    - 基于机器信息派生加密密钥
    """

    SUPPORTED_PROVIDERS = {
        "openai": ["api_key"],
        "anthropic": ["api_key"],
        "deepseek": ["api_key"],
        "doubao": ["api_key", "secret_key"],
        "qwen": ["api_key"],
        "zhipu": ["api_key"],
        "moonshot": ["api_key"],
        "custom": ["api_key"],
    }

    def __init__(self, data_dir: str = None, master_password: str = None):
        self._data_dir = Path(data_dir or self._default_dir())
        self._cred_file = self._data_dir / "credentials.enc"
        self._salt_file = self._data_dir / "salt.bin"
        self._cache: dict[str, dict] = {}
        self._fernet = None
        self._master_password = master_password
        self._init_crypto()
        self._load()

    @staticmethod
    def _default_dir() -> str:
        if os.name == "nt":
            return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OfficeAgent", "config")
        elif os.path.exists("/Applications"):
            return os.path.expanduser("~/Library/Application Support/OfficeAgent/config")
        return os.path.expanduser("~/.local/share/OfficeAgent/config")

    def _get_machine_key(self) -> str:
        """基于机器信息生成唯一密钥材料"""
        info = []
        info.append(os.environ.get("COMPUTERNAME", os.environ.get("HOSTNAME", "unknown")))
        info.append(os.environ.get("USERNAME", os.environ.get("USER", "unknown")))
        info.append(str(Path.home()))
        try:
            if os.name == "nt":
                info.append(os.environ.get("PROCESSOR_IDENTIFIER", ""))
                info.append(os.environ.get("NUMBER_OF_PROCESSORS", ""))
            else:
                import uuid
                info.append(str(uuid.getnode()))
        except Exception:
            pass
        return "|".join(info)

    def _init_crypto(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # 加载或生成salt
        if self._salt_file.exists():
            with open(self._salt_file, "rb") as f:
                salt = f.read()
        else:
            salt = secrets.token_bytes(32)
            with open(self._salt_file, "wb") as f:
                f.write(salt)
            try:
                os.chmod(str(self._salt_file), 0o600)
            except Exception:
                pass
        # 派生密钥
        password = self._master_password or self._get_machine_key()
        if HAS_CRYPTO:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            self._fernet = Fernet(key)
        else:
            derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
            self._fernet = SimpleFernet(derived)

    def _encrypt(self, plaintext: str) -> str:
        encrypted = self._fernet.encrypt(plaintext.encode("utf-8"))
        return encrypted.decode("utf-8") if isinstance(encrypted, bytes) else encrypted

    def _decrypt(self, ciphertext: str) -> str:
        data = ciphertext.encode("utf-8") if isinstance(ciphertext, str) else ciphertext
        return self._fernet.decrypt(data).decode("utf-8")

    def _load(self) -> None:
        if self._cred_file.exists():
            try:
                with open(self._cred_file, "r", encoding="utf-8") as f:
                    encrypted_data = f.read()
                if encrypted_data:
                    decrypted = self._decrypt(encrypted_data)
                    self._cache = json.loads(decrypted)
            except Exception:
                self._cache = {}

    def _save(self) -> None:
        data = json.dumps(self._cache, ensure_ascii=False)
        encrypted = self._encrypt(data)
        with open(self._cred_file, "w", encoding="utf-8") as f:
            f.write(encrypted)
        try:
            os.chmod(str(self._cred_file), 0o600)
        except Exception:
            pass

    @staticmethod
    def _mask_value(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return value[:4] + "*" * (len(value) - 8) + value[-4:]

    def set_credential(self, provider: str, key_type: str, value: str) -> None:
        """设置凭据（加密存储）"""
        import time
        now = time.time()
        key = f"{provider}.{key_type}"
        existing = self._cache.get(key, {})
        self._cache[key] = {
            "provider": provider,
            "key_type": key_type,
            "encrypted_value": self._encrypt(value),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        self._save()

    def get_credential(self, provider: str, key_type: str = "api_key") -> Optional[str]:
        """获取凭据（解密）"""
        key = f"{provider}.{key_type}"
        entry = self._cache.get(key)
        if not entry:
            return None
        try:
            return self._decrypt(entry["encrypted_value"])
        except Exception:
            return None

    def delete_credential(self, provider: str, key_type: str = "api_key") -> bool:
        """删除凭据"""
        key = f"{provider}.{key_type}"
        if key in self._cache:
            del self._cache[key]
            self._save()
            return True
        return False

    def has_credential(self, provider: str, key_type: str = "api_key") -> bool:
        """检查凭据是否存在"""
        return f"{provider}.{key_type}" in self._cache

    def list_credentials(self) -> list[CredentialInfo]:
        """列出所有凭据（掩码显示）"""
        import time
        result = []
        for key, entry in self._cache.items():
            try:
                decrypted = self._decrypt(entry["encrypted_value"])
                masked = self._mask_value(decrypted)
            except Exception:
                masked = "***"
            result.append(CredentialInfo(
                provider=entry["provider"],
                key_type=entry["key_type"],
                created_at=entry.get("created_at", 0),
                updated_at=entry.get("updated_at", 0),
                masked_value=masked,
                is_set=True,
            ))
        return result

    def get_all_provider_keys(self) -> dict[str, str]:
        """获取所有已配置的provider和其api_key"""
        result = {}
        for provider in self.SUPPORTED_PROVIDERS:
            key = self.get_credential(provider, "api_key")
            if key:
                result[provider] = key
        return result

    def clear_all(self) -> None:
        """清除所有凭据"""
        self._cache = {}
        if self._cred_file.exists():
            self._cred_file.unlink()

    def verify_credential(self, provider: str, test_value: str) -> bool:
        """验证凭据是否匹配"""
        stored = self.get_credential(provider)
        return stored == test_value


# 全局实例
_manager: Optional[LocalCredentialManager] = None


def get_credential_manager() -> LocalCredentialManager:
    global _manager
    if _manager is None:
        _manager = LocalCredentialManager()
    return _manager
