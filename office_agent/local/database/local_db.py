"""
Local SQLite Database - 本地SQLite数据库
默认使用SQLite，保留数据库抽象层支持未来切换PostgreSQL
"""
import os
import json
import time
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class UserProfileRecord:
    """用户配置表"""
    id: str
    username: str
    display_name: str
    settings_json: str
    preferences_json: str
    created_at: float
    last_active_at: float
    cloud_sync: int = 0
    cloud_user_id: str = ""


@dataclass
class AppSettingsRecord:
    """应用设置表"""
    id: int
    key: str
    value: str
    value_type: str  # string/int/float/bool/json
    updated_at: float


@dataclass
class ModelConfigRecord:
    """模型配置表"""
    id: int
    provider: str
    model_name: str
    api_key_encrypted: str
    base_url: str
    enabled: int
    is_default: int
    is_fallback: int
    priority: int
    max_tokens: int
    temperature: float
    created_at: float
    updated_at: float


@dataclass
class TaskRecord:
    """任务记录表"""
    id: str
    task_type: str
    status: str
    input_summary: str
    output_summary: str
    file_id: str
    result_path: str
    error: str
    created_at: float
    started_at: float
    completed_at: float
    duration_ms: int


@dataclass
class AuditLogRecord:
    """审计日志表"""
    id: int
    timestamp: float
    action: str
    target: str
    details: str
    success: int


class LocalDatabase:
    """本地SQLite数据库"""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = None):
        if db_path:
            self._db_path = Path(db_path)
        else:
            base = self._default_base_dir()
            self._db_path = Path(base) / "database" / "officeagent.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    @staticmethod
    def _default_base_dir() -> str:
        if os.name == "nt":
            return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OfficeAgent")
        elif os.path.exists("/Applications"):
            return os.path.expanduser("~/Library/Application Support/OfficeAgent")
        return os.path.expanduser("~/.local/share/OfficeAgent")

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def get_connection(self):
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        with self.get_connection() as conn:
            # 版本表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
            """)
            # 用户配置表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    settings_json TEXT DEFAULT '{}',
                    preferences_json TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    cloud_sync INTEGER DEFAULT 0,
                    cloud_user_id TEXT DEFAULT ''
                )
            """)
            # 应用设置表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    value_type TEXT DEFAULT 'string',
                    updated_at REAL NOT NULL
                )
            """)
            # 模型配置表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    api_key_encrypted TEXT DEFAULT '',
                    base_url TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    is_default INTEGER DEFAULT 0,
                    is_fallback INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 0,
                    max_tokens INTEGER DEFAULT 4096,
                    temperature REAL DEFAULT 0.7,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # 任务记录表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    input_summary TEXT DEFAULT '',
                    output_summary TEXT DEFAULT '',
                    file_id TEXT DEFAULT '',
                    result_path TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    started_at REAL DEFAULT 0,
                    completed_at REAL DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)")
            # 审计日志表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT DEFAULT '',
                    details TEXT DEFAULT '',
                    success INTEGER DEFAULT 1
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)")
            # 记录版本
            cursor = conn.execute("SELECT COUNT(*) FROM schema_version")
            if cursor.fetchone()[0] == 0:
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (self.SCHEMA_VERSION, time.time())
                )

    # === User Profile ===
    def save_user(self, user_id: str, username: str, display_name: str = "",
                  settings: dict = None, preferences: dict = None) -> None:
        now = time.time()
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO user_profile (id, username, display_name, settings_json, preferences_json, created_at, last_active_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    settings_json=excluded.settings_json,
                    preferences_json=excluded.preferences_json,
                    last_active_at=excluded.last_active_at
            """, (user_id, username, display_name,
                  json.dumps(settings or {}), json.dumps(preferences or {}),
                  now, now))

    def get_user(self, user_id: str) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM user_profile WHERE id=?", (user_id,)).fetchone()
            if row:
                d = dict(row)
                d["settings"] = json.loads(d.pop("settings_json", "{}"))
                d["preferences"] = json.loads(d.pop("preferences_json", "{}"))
                return d
        return None

    # === App Settings ===
    def set_setting(self, key: str, value: Any, value_type: str = None) -> None:
        if value_type is None:
            value_type = type(value).__name__
        if value_type in ("dict", "list"):
            stored = json.dumps(value)
            value_type = "json"
        elif value_type == "bool":
            stored = "1" if value else "0"
        else:
            stored = str(value)
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO app_settings (key, value, value_type, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type, updated_at=excluded.updated_at
            """, (key, stored, value_type, time.time()))

    def get_setting(self, key: str, default=None) -> Any:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM app_settings WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            value = row["value"]
            vtype = row["value_type"]
            if vtype == "int":
                return int(value)
            if vtype == "float":
                return float(value)
            if vtype == "bool":
                return value == "1"
            if vtype == "json":
                return json.loads(value)
            return value

    def get_all_settings(self) -> dict:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM app_settings").fetchall()
            result = {}
            for row in rows:
                result[row["key"]] = self.get_setting(row["key"])
            return result

    # === Model Configs ===
    def save_model_config(self, config: dict) -> int:
        now = time.time()
        with self.get_connection() as conn:
            if config.get("id"):
                conn.execute("""
                    UPDATE model_configs SET provider=?, model_name=?, api_key_encrypted=?, base_url=?,
                        enabled=?, is_default=?, is_fallback=?, priority=?, max_tokens=?, temperature=?, updated_at=?
                    WHERE id=?
                """, (config["provider"], config["model_name"], config.get("api_key_encrypted", ""),
                      config.get("base_url", ""), config.get("enabled", 1), config.get("is_default", 0),
                      config.get("is_fallback", 0), config.get("priority", 0), config.get("max_tokens", 4096),
                      config.get("temperature", 0.7), now, config["id"]))
                return config["id"]
            else:
                cursor = conn.execute("""
                    INSERT INTO model_configs (provider, model_name, api_key_encrypted, base_url, enabled,
                        is_default, is_fallback, priority, max_tokens, temperature, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (config["provider"], config["model_name"], config.get("api_key_encrypted", ""),
                      config.get("base_url", ""), config.get("enabled", 1), config.get("is_default", 0),
                      config.get("is_fallback", 0), config.get("priority", 0), config.get("max_tokens", 4096),
                      config.get("temperature", 0.7), now, now))
                return cursor.lastrowid

    def get_model_configs(self, enabled_only: bool = False) -> list[dict]:
        with self.get_connection() as conn:
            sql = "SELECT * FROM model_configs"
            if enabled_only:
                sql += " WHERE enabled=1"
            sql += " ORDER BY priority DESC, id ASC"
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_model_config(self, model_id: int) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM model_configs WHERE id=?", (model_id,)).fetchone()
            return dict(row) if row else None

    def delete_model_config(self, model_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.execute("DELETE FROM model_configs WHERE id=?", (model_id,))
            return cursor.rowcount > 0

    # === Tasks ===
    def save_task(self, task: dict) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tasks (id, task_type, status, input_summary, output_summary,
                    file_id, result_path, error, created_at, started_at, completed_at, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (task.get("id"), task.get("task_type"), task.get("status", "pending"),
                  task.get("input_summary", ""), task.get("output_summary", ""),
                  task.get("file_id", ""), task.get("result_path", ""), task.get("error", ""),
                  task.get("created_at", time.time()), task.get("started_at", 0),
                  task.get("completed_at", 0), task.get("duration_ms", 0)))

    def get_task(self, task_id: str) -> Optional[dict]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, status: str = None, limit: int = 100) -> list[dict]:
        with self.get_connection() as conn:
            sql = "SELECT * FROM tasks"
            params = []
            if status:
                sql += " WHERE status=?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    # === Audit Logs ===
    def add_audit_log(self, action: str, target: str = "", details: str = "", success: bool = True) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO audit_logs (timestamp, action, target, details, success)
                VALUES (?, ?, ?, ?, ?)
            """, (time.time(), action, target, details, 1 if success else 0))

    def get_audit_logs(self, limit: int = 100, action: str = None) -> list[dict]:
        with self.get_connection() as conn:
            sql = "SELECT * FROM audit_logs"
            params = []
            if action:
                sql += " WHERE action=?"
                params.append(action)
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# 全局实例
_db: Optional[LocalDatabase] = None


def get_database(db_path: str = None) -> LocalDatabase:
    global _db
    if _db is None:
        _db = LocalDatabase(db_path)
    return _db
