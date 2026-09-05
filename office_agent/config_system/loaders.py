"""
配置加载器

支持从多种来源加载配置：
- 环境变量 (.env)
- YAML 文件
- 数据库
"""
import os
from typing import Dict, Any, List
from pathlib import Path

from ..logging_system import get_logger
from ..runtime_config import get_data_root

logger = get_logger("config.loader")


class EnvLoader:
    """从环境变量加载配置"""

    # 环境变量映射
    ENV_MAP = {
        # 基础
        "ENVIRONMENT": ("environment", str),
        "DEBUG": ("debug", lambda v: v.lower() in ("true", "1", "yes")),
        "SERVICE_NAME": ("service_name", str),

        # 数据库
        "DATABASE_URL": ("database_url", str),

        # Redis
        "REDIS_URL": ("redis_url", str),

        # 存储
        "STORAGE_TYPE": ("storage.storage_type", str),
        "LOCAL_STORAGE_PATH": ("storage.local_path", str),
        "MAX_FILE_SIZE": ("storage.max_file_size", int),
        "MAX_VERSIONS": ("storage.max_versions", int),

        # 队列
        "QUEUE_MODE": ("queue.mode", str),
        "CELERY_BROKER_URL": ("queue.broker_url", str),
        "LOCAL_MAX_WORKERS": ("queue.max_workers", int),
        "TASK_TIMEOUT": ("queue.task_timeout", int),

        # 日志
        "LOG_LEVEL": ("logging.level", str),
        "LOG_DIR": ("logging.dir", str),
        "ENABLE_DATABASE_LOG": ("logging.enable_db_logging", lambda v: v.lower() == "true"),
        "ENABLE_METRICS": ("logging.enable_metrics", lambda v: v.lower() == "true"),

        # 默认模型
        "DEFAULT_MODEL": ("default_model", str),
        "DEFAULT_VISION_MODEL": ("default_vision_model", str),

        # 功能开关
        "ENABLE_RAG": ("enable_rag", lambda v: v.lower() == "true"),
        "ENABLE_QUALITY_CHECK": ("enable_quality_check", lambda v: v.lower() == "true"),
        "ENABLE_MULTIMODAL": ("enable_multimodal", lambda v: v.lower() == "true"),

        # API Keys (不存配置，只记录是否存在)
        "OPENAI_API_KEY": ("_has_openai_key", lambda v: bool(v)),
        "ANTHROPIC_API_KEY": ("_has_anthropic_key", lambda v: bool(v)),
        "DEEPSEEK_API_KEY": ("_has_deepseek_key", lambda v: bool(v)),
        "DASHSCOPE_API_KEY": ("_has_qwen_key", lambda v: bool(v)),
        "DOUBAO_API_KEY": ("_has_doubao_key", lambda v: bool(v)),
    }

    @classmethod
    def load(cls) -> Dict[str, Any]:
        """从环境变量加载配置"""
        result: Dict[str, Any] = {}
        for env_key, (config_path, converter) in cls.ENV_MAP.items():
            value = os.environ.get(env_key)
            if value is not None:
                try:
                    converted = converter(value)
                    # 支持嵌套路径 a.b.c
                    parts = config_path.split(".")
                    d = result
                    for part in parts[:-1]:
                        d = d.setdefault(part, {})
                    d[parts[-1]] = converted
                except (ValueError, TypeError) as e:
                    logger.warning(f"环境变量 {env_key} 转换失败: {e}")
        return result


class YamlLoader:
    """从 YAML 文件加载配置"""

    def __init__(self, config_dir: str | None = None):
        self.config_dir = Path(config_dir or os.environ.get(
            "CONFIG_DIR",
            str(get_data_root() / "config"),
        ))

    def load(self, filename: str = "config.yaml") -> Dict[str, Any]:
        """加载 YAML 配置文件"""
        filepath = self.config_dir / filename
        if not filepath.exists():
            logger.debug(f"配置文件不存在: {filepath}")
            return {}

        try:
            import yaml
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.info(f"从 {filepath} 加载配置")
            return data
        except ImportError:
            logger.warning("PyYAML 未安装，无法加载 YAML 配置")
            return {}
        except (OSError, ValueError) as e:
            # 文件 IO / 编码错误 / JSON 系解析错误（UnicodeDecodeError、
            # JSONDecodeError 均为 ValueError 子类）的明确收窄
            logger.error(f"加载配置文件失败: {e}")
            return {}
        except yaml.YAMLError as e:
            # PyYAML 的 YAMLError 直接继承 Exception，需单列；
            # 此分支只在 import yaml 成功后才会被求值，不会 NameError
            logger.error(f"加载配置文件失败: {e}")
            return {}

    def load_models(self) -> List[Dict[str, Any]]:
        """加载模型配置"""
        data = self.load("models.yaml")
        return data.get("models", [])

    def load_agents(self) -> List[Dict[str, Any]]:
        """加载 Agent 配置"""
        data = self.load("agents.yaml")
        return data.get("agents", [])

    def load_prompts(self) -> List[Dict[str, Any]]:
        """加载 Prompt 配置"""
        data = self.load("prompts.yaml")
        return data.get("prompts", [])

    def load_skills(self) -> List[Dict[str, Any]]:
        """加载 Skill 配置"""
        data = self.load("skills.yaml")
        return data.get("skills", [])

    def load_workflows(self) -> List[Dict[str, Any]]:
        """加载工作流配置"""
        data = self.load("workflows.yaml")
        return data.get("workflows", [])

    def save(self, data: Dict[str, Any], filename: str = "config.yaml"):
        """保存配置到 YAML 文件（原子写入，中断不会留下半截配置）。"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.config_dir / filename
        try:
            import yaml
        except ImportError:
            logger.error("PyYAML 未安装，无法保存 YAML 配置")
            return
        # ImportError 已在上一步单独处理，此处异常就是真实写入失败，
        # 不能再被笼统地报成“PyYAML 未安装”。
        from ..persistence import atomic_write

        def _dump(handle):
            yaml.dump(data, handle, allow_unicode=True, default_flow_style=False)

        atomic_write(filepath, _dump, encoding="utf-8")
        logger.info(f"配置已保存到 {filepath}")


class DatabaseLoader:
    """从数据库加载配置"""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def load_models(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        from ..database.repository import ModelConfigRepository
        session = self.session_factory()
        try:
            repo = ModelConfigRepository(session)
            models = repo.get_enabled() if only_enabled else repo.find(limit=1000)
            return [repo.to_dict(m) for m in models]
        finally:
            session.close()

    def load_agents(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        from ..database.repository import AgentConfigRepository
        session = self.session_factory()
        try:
            repo = AgentConfigRepository(session)
            agents = repo.get_enabled() if only_enabled else repo.find(limit=100)
            return [repo.to_dict(a) for a in agents]
        finally:
            session.close()

    def load_prompts(self, only_active: bool = True) -> List[Dict[str, Any]]:
        from ..database.repository import PromptConfigRepository
        session = self.session_factory()
        try:
            repo = PromptConfigRepository(session)
            prompts = repo.get_active() if only_active else repo.find(limit=1000)
            return [repo.to_dict(p) for p in prompts]
        finally:
            session.close()

    def load_skills(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        from ..database.repository import SkillConfigRepository
        session = self.session_factory()
        try:
            repo = SkillConfigRepository(session)
            skills = repo.get_enabled() if only_enabled else repo.find(limit=100)
            return [repo.to_dict(s) for s in skills]
        finally:
            session.close()

    def load_workflows(self, only_enabled: bool = True) -> List[Dict[str, Any]]:
        from ..database.repository import WorkflowConfigRepository
        session = self.session_factory()
        try:
            repo = WorkflowConfigRepository(session)
            wfs = repo.get_enabled() if only_enabled else repo.find(limit=100)
            return [repo.to_dict(w) for w in wfs]
        finally:
            session.close()


def deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
