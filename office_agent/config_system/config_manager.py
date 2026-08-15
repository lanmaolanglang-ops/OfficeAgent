"""
配置管理核心

统一管理所有配置，支持：
- 多来源加载（env > YAML > 数据库 > 默认值）
- 内存缓存
- 热更新
- 运行时查询
"""
import os
import json
import threading
import time
from typing import Dict, List, Optional, Any, Callable

from .schemas import (
    GlobalConfig, ModelConfigSchema, AgentConfigSchema,
    PromptConfigSchema, SkillConfigSchema, WorkflowConfigSchema,
)
from .loaders import EnvLoader, YamlLoader, DatabaseLoader, deep_merge
from .validators import validate_all, Severity
from ..logging_system import get_logger

logger = get_logger("config.manager")


class ConfigCache:
    """线程安全的配置缓存"""

    def __init__(self):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._loaded_at: float = 0

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value):
        with self._lock:
            self._data[key] = value

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def clear(self):
        with self._lock:
            self._data.clear()
            self._loaded_at = 0

    @property
    def loaded_at(self) -> float:
        return self._loaded_at

    @loaded_at.setter
    def loaded_at(self, v):
        self._loaded_at = v


class ConfigManager:
    """
    配置管理器（单例）

    用法：
        from office_agent.config_system import get_config

        config = get_config()
        model = config.get_model("gpt-4o")
        prompt = config.get_prompt("word_format")
        agent_cfg = config.get_agent("WordAgent")
    """

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, session_factory=None, config_dir: str = None):
        if hasattr(self, "_initialized") and self._initialized:
            # 允许更新 session_factory
            if session_factory is not None:
                self._session_factory = session_factory
            return
        self._initialized = True

        self._cache = ConfigCache()
        self._session_factory = session_factory
        self._yaml_loader = YamlLoader(config_dir)
        self._change_listeners: List[Callable] = []

        # 全局配置
        self._global_config: Optional[GlobalConfig] = None

        # 各类配置缓存（dict by id/name）
        self._models: Dict[str, Dict] = {}
        self._agents: Dict[str, Dict] = {}
        self._prompts: Dict[str, List[Dict]] = {}  # name -> [versions]
        self._skills: Dict[str, Dict] = {}
        self._workflows: Dict[str, Dict] = {}

        self._load_defaults()

    def _load_defaults(self):
        """加载内置默认配置"""
        self._default_models = _get_default_models()
        self._default_agents = _get_default_agents()
        self._default_prompts = _get_default_prompts()
        self._default_skills = _get_default_skills()
        self._default_workflows = _get_default_workflows()

    def initialize(self, session_factory=None, strict: bool = False) -> bool:
        """
        初始化配置（启动时调用）

        加载顺序：默认值 < YAML < 数据库 < 环境变量
        """
        if session_factory:
            self._session_factory = session_factory

        logger.info("开始加载配置...")

        # 1. 全局配置
        global_dict = {}
        # YAML 全局
        yaml_global = self._yaml_loader.load("config.yaml")
        if yaml_global:
            global_dict = deep_merge(global_dict, yaml_global.get("global", {}))
        # 环境变量
        env_config = EnvLoader.load()
        global_dict = deep_merge(global_dict, env_config)

        try:
            self._global_config = GlobalConfig(**global_dict)
        except Exception as e:
            logger.error(f"全局配置无效: {e}")
            self._global_config = GlobalConfig()

        # 2. 加载各类配置
        models = list(self._default_models)
        agents = list(self._default_agents)
        prompts = list(self._default_prompts)
        skills = list(self._default_skills)
        workflows = list(self._default_workflows)

        # YAML 覆盖
        yaml_models = self._yaml_loader.load_models()
        if yaml_models:
            models = _merge_list(models, yaml_models, "model_id")
        yaml_agents = self._yaml_loader.load_agents()
        if yaml_agents:
            agents = _merge_list(agents, yaml_agents, "agent_name")
        yaml_prompts = self._yaml_loader.load_prompts()
        if yaml_prompts:
            prompts = _merge_list(prompts, yaml_prompts, "name")
        yaml_skills = self._yaml_loader.load_skills()
        if yaml_skills:
            skills = _merge_list(skills, yaml_skills, "skill_name")
        yaml_workflows = self._yaml_loader.load_workflows()
        if yaml_workflows:
            workflows = _merge_list(workflows, yaml_workflows, "workflow_name")

        # 数据库覆盖
        if self._session_factory:
            try:
                db_loader = DatabaseLoader(self._session_factory)
                db_models = db_loader.load_models(only_enabled=False)
                if db_models:
                    models = _merge_list(models, db_models, "model_id")
                db_agents = db_loader.load_agents(only_enabled=False)
                if db_agents:
                    agents = _merge_list(agents, db_agents, "agent_name")
                db_prompts = db_loader.load_prompts(only_active=False)
                if db_prompts:
                    prompts = _merge_list(prompts, db_prompts, "name")
                db_skills = db_loader.load_skills(only_enabled=False)
                if db_skills:
                    skills = _merge_list(skills, db_skills, "skill_name")
                db_workflows = db_loader.load_workflows(only_enabled=False)
                if db_workflows:
                    workflows = _merge_list(workflows, db_workflows, "workflow_name")
                logger.info("从数据库加载配置完成")
            except Exception as e:
                logger.warning(f"从数据库加载配置失败: {e}")

        # 3. 构建索引
        self._rebuild_cache(models, agents, prompts, skills, workflows)

        # 4. 校验
        passed, issues = validate_all(
            self._global_config,
            [m for m in models if m.get("enabled", True)],
            [a for a in agents if a.get("enabled", True)],
            [p for p in prompts if p.get("status") == "active"],
            skills, workflows,
            strict=strict,
        )

        self._cache.loaded_at = time.time()
        logger.info(f"配置加载完成: {len(self._models)} 模型, {len(self._agents)} Agent, "
                    f"{len(self._prompts)} Prompt组, {len(self._skills)} Skill, {len(self._workflows)} 工作流")

        return passed

    def _rebuild_cache(self, models, agents, prompts, skills, workflows):
        """重建缓存索引"""
        self._models = {m["model_id"]: m for m in models if m.get("model_id")}
        self._agents = {a["agent_name"]: a for a in agents if a.get("agent_name")}

        # Prompts 按 name 分组
        self._prompts = {}
        for p in prompts:
            name = p.get("name")
            if name:
                self._prompts.setdefault(name, []).append(p)

        self._skills = {s["skill_name"]: s for s in skills if s.get("skill_name")}
        self._workflows = {w["workflow_name"]: w for w in workflows if w.get("workflow_name")}

    # ============================================================
    # 查询接口
    # ============================================================

    @property
    def global_config(self) -> GlobalConfig:
        return self._global_config or GlobalConfig()

    def get_model(self, model_id: str) -> Optional[Dict]:
        """获取模型配置"""
        return self._models.get(model_id)

    def get_enabled_models(self) -> List[Dict]:
        """获取所有启用的模型，按优先级排序"""
        models = [m for m in self._models.values() if m.get("enabled", True)]
        return sorted(models, key=lambda m: m.get("priority", 0), reverse=True)

    def get_models_by_provider(self, provider: str) -> List[Dict]:
        return [m for m in self._models.values()
                if m.get("provider") == provider and m.get("enabled", True)]

    def get_agent(self, agent_name: str) -> Optional[Dict]:
        """获取 Agent 配置"""
        return self._agents.get(agent_name)

    def get_enabled_agents(self) -> List[Dict]:
        return [a for a in self._agents.values() if a.get("enabled", True)]

    def get_agent_prompt(self, agent_name: str) -> str:
        """获取 Agent 的 system prompt"""
        agent = self._agents.get(agent_name)
        if agent and agent.get("system_prompt"):
            return agent["system_prompt"]
        # 尝试从 prompts 中查找
        for name, versions in self._prompts.items():
            if agent_name.lower() in name.lower():
                default = next((v for v in versions if v.get("is_default")), None)
                if default:
                    return default["content"]
                if versions:
                    return versions[0]["content"]
        return ""

    def get_prompt(self, name: str, version: str = None) -> Optional[Dict]:
        """获取 Prompt"""
        versions = self._prompts.get(name, [])
        if not versions:
            return None
        if version:
            return next((v for v in versions if v.get("version") == version), None)
        # 默认版本
        default = next((v for v in versions if v.get("is_default")), None)
        return default or versions[0]

    def get_prompt_content(self, name: str, version: str = None,
                           variables: Dict[str, str] = None) -> Optional[str]:
        """获取 Prompt 内容并渲染变量"""
        prompt = self.get_prompt(name, version)
        if not prompt:
            return None
        content = prompt["content"]
        if variables:
            for k, v in variables.items():
                content = content.replace("{" + k + "}", str(v))
        return content

    def list_prompt_versions(self, name: str) -> List[Dict]:
        return self._prompts.get(name, [])

    def get_skill(self, skill_name: str) -> Optional[Dict]:
        return self._skills.get(skill_name)

    def get_enabled_skills(self) -> List[Dict]:
        return [s for s in self._skills.values() if s.get("enabled", True)]

    def get_workflow(self, name: str) -> Optional[Dict]:
        return self._workflows.get(name)

    def get_enabled_workflows(self) -> List[Dict]:
        return [w for w in self._workflows.values() if w.get("enabled", True)]

    def get_model_for_agent(self, agent_name: str) -> Optional[Dict]:
        """获取 Agent 应该使用的模型"""
        agent = self._agents.get(agent_name)
        if not agent:
            return self.get_model(self.global_config.default_model)

        for model_id in agent.get("model_priority", []):
            model = self._models.get(model_id)
            if model and model.get("enabled", True):
                return model

        return self.get_model(self.global_config.default_model)

    # ============================================================
    # 更新接口（热更新）
    # ============================================================

    def update_model(self, model_id: str, data: Dict) -> Dict:
        """更新模型配置并持久化"""
        self._models[model_id] = {**self._models.get(model_id, {}), **data, "model_id": model_id}
        self._persist_model(model_id, self._models[model_id])
        self._notify_listeners("model", model_id)
        return self._models[model_id]

    def update_agent(self, agent_name: str, data: Dict) -> Dict:
        self._agents[agent_name] = {**self._agents.get(agent_name, {}), **data, "agent_name": agent_name}
        self._persist_agent(agent_name, self._agents[agent_name])
        self._notify_listeners("agent", agent_name)
        return self._agents[agent_name]

    def update_prompt(self, name: str, content: str, version: str = None,
                      set_default: bool = False) -> Dict:
        """更新/新增 Prompt 版本"""
        import uuid
        new_version = version or f"1.{int(time.time()) % 10000}"
        prompt = {
            "id": f"prm_{uuid.uuid4().hex[:12]}",
            "name": name,
            "version": new_version,
            "content": content,
            "status": "active",
            "is_default": set_default,
        }
        self._prompts.setdefault(name, []).append(prompt)
        if set_default:
            for v in self._prompts[name]:
                v["is_default"] = (v is prompt)
        self._persist_prompt(prompt)
        self._notify_listeners("prompt", name)
        return prompt

    def update_skill(self, skill_name: str, data: Dict) -> Dict:
        self._skills[skill_name] = {**self._skills.get(skill_name, {}), **data, "skill_name": skill_name}
        self._persist_skill(skill_name, self._skills[skill_name])
        self._notify_listeners("skill", skill_name)
        return self._skills[skill_name]

    def update_workflow(self, name: str, data: Dict) -> Dict:
        self._workflows[name] = {**self._workflows.get(name, {}), **data, "workflow_name": name}
        self._persist_workflow(name, self._workflows[name])
        self._notify_listeners("workflow", name)
        return self._workflows[name]

    def reload(self):
        """重新加载所有配置（热更新）"""
        logger.info("重新加载配置...")
        self.initialize(self._session_factory, strict=False)

    def add_listener(self, callback: Callable):
        """添加配置变更监听器"""
        self._change_listeners.append(callback)

    def _notify_listeners(self, config_type: str, key: str):
        for cb in self._change_listeners:
            try:
                cb(config_type, key)
            except Exception as e:
                logger.debug(f"配置监听器执行失败: {e}")

    # ============================================================
    # 持久化
    # ============================================================

    def _persist_model(self, model_id: str, data: Dict):
        if not self._session_factory:
            return
        try:
            from ..database.repository import ModelConfigRepository
            session = self._session_factory()
            try:
                repo = ModelConfigRepository(session)
                repo.upsert(model_id, _model_to_db(data))
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"持久化模型配置失败: {e}")

    def _persist_agent(self, name: str, data: Dict):
        if not self._session_factory:
            return
        try:
            from ..database.repository import AgentConfigRepository
            session = self._session_factory()
            try:
                repo = AgentConfigRepository(session)
                repo.upsert(name, _agent_to_db(data))
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"持久化 Agent 配置失败: {e}")

    def _persist_prompt(self, data: Dict):
        if not self._session_factory:
            return
        try:
            from ..database.repository import PromptConfigRepository
            session = self._session_factory()
            try:
                repo = PromptConfigRepository(session)
                from ..database.models.config import PromptConfig
                db_obj = PromptConfig(
                    id=data.get("id"),
                    name=data["name"],
                    version=data["version"],
                    content=data["content"],
                    status=data.get("status", "active"),
                    is_default=data.get("is_default", False),
                    variables=json.dumps(data.get("variables", [])),
                    agent=data.get("agent"),
                )
                session.add(db_obj)
                session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"持久化 Prompt 失败: {e}")

    def _persist_skill(self, name: str, data: Dict):
        if not self._session_factory:
            return
        try:
            from ..database.repository import SkillConfigRepository
            session = self._session_factory()
            try:
                repo = SkillConfigRepository(session)
                existing = repo.get_by_name(name)
                if existing:
                    existing.description = data.get("description", "")
                    existing.workflow = json.dumps(data.get("workflow", []), ensure_ascii=False)
                    existing.tools = json.dumps(data.get("tools", []))
                    existing.enabled = data.get("enabled", True)
                    existing.parameters = json.dumps(data.get("parameters", {}), ensure_ascii=False)
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"持久化 Skill 配置失败: {e}")

    def _persist_workflow(self, name: str, data: Dict):
        if not self._session_factory:
            return
        try:
            from ..database.repository import WorkflowConfigRepository
            session = self._session_factory()
            try:
                repo = WorkflowConfigRepository(session)
                existing = repo.get_by_name(name)
                if existing:
                    existing.steps = json.dumps(data.get("steps", []), ensure_ascii=False)
                    existing.description = data.get("description", "")
                    existing.enabled = data.get("enabled", True)
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"持久化工作流配置失败: {e}")

    def export_all(self) -> Dict:
        """导出所有配置"""
        return {
            "global": self.global_config.model_dump(),
            "models": list(self._models.values()),
            "agents": list(self._agents.values()),
            "prompts": [v for vs in self._prompts.values() for v in vs],
            "skills": list(self._skills.values()),
            "workflows": list(self._workflows.values()),
            "loaded_at": self._cache.loaded_at,
        }


# ============================================================
# 单例获取
# ============================================================

_config_manager: Optional[ConfigManager] = None
_config_lock = threading.Lock()


def get_config(session_factory=None) -> ConfigManager:
    """获取全局配置管理器"""
    global _config_manager
    if _config_manager is None:
        with _config_lock:
            if _config_manager is None:
                _config_manager = ConfigManager(session_factory)
    elif session_factory is not None:
        _config_manager._session_factory = session_factory
    return _config_manager


# ============================================================
# 辅助函数
# ============================================================

def _merge_list(base: List[Dict], override: List[Dict], key: str) -> List[Dict]:
    """合并配置列表，override 覆盖 base"""
    result = {item[key]: dict(item) for item in base if item.get(key)}
    for item in override:
        k = item.get(key)
        if k:
            result[k] = {**result.get(k, {}), **item}
    return list(result.values())


def _model_to_db(data: Dict) -> Dict:
    import json as _json
    return {
        "model_name": data.get("model_name", data.get("model_id")),
        "provider": data.get("provider", "custom"),
        "api_endpoint": data.get("api_endpoint"),
        "api_key_env": data.get("api_key_env"),
        "temperature": data.get("temperature", 0.7),
        "top_p": data.get("top_p", 1.0),
        "max_tokens": data.get("max_tokens", 4096),
        "context_length": data.get("context_length", 8192),
        "supports_vision": data.get("supports_vision", False),
        "supports_streaming": data.get("supports_streaming", True),
        "supports_function_calling": data.get("supports_function_calling", False),
        "cost_input_per_1k": data.get("cost_input_per_1k", 0),
        "cost_output_per_1k": data.get("cost_output_per_1k", 0),
        "enabled": data.get("enabled", True),
        "priority": data.get("priority", 0),
        "tags": _json.dumps(data.get("tags", [])),
        "description": data.get("description"),
        "config_json": _json.dumps(data.get("extra", {}), ensure_ascii=False),
    }


def _agent_to_db(data: Dict) -> Dict:
    import json as _json
    return {
        "description": data.get("description", ""),
        "version": data.get("version", "1.0.0"),
        "system_prompt": data.get("system_prompt", ""),
        "model_priority": _json.dumps(data.get("model_priority", [])),
        "fallback_models": _json.dumps(data.get("fallback_models", [])),
        "available_tools": _json.dumps(data.get("available_tools", []), ensure_ascii=False),
        "timeout": data.get("timeout", 120),
        "max_retries": data.get("max_retries", 3),
        "max_input_length": data.get("max_input_length", 100000),
        "max_output_length": data.get("max_output_length", 50000),
        "enable_quality_check": data.get("enable_quality_check", True),
        "quality_threshold": data.get("quality_threshold", 0.7),
        "enabled": data.get("enabled", True),
        "tags": _json.dumps(data.get("tags", [])),
        "config_json": _json.dumps(data.get("extra", {}), ensure_ascii=False),
    }


# ============================================================
# 默认配置
# ============================================================

def _get_default_models() -> List[Dict]:
    return [
        {
            "model_id": "doubao-pro",
            "model_name": "豆包 Pro",
            "provider": "doubao",
            "api_key_env": "DOUBAO_API_KEY",
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_length": 128000,
            "supports_streaming": True,
            "cost_input_per_1k": 0.0008,
            "cost_output_per_1k": 0.002,
            "enabled": True,
            "priority": 100,
            "tags": ["default", "chinese"],
            "description": "豆包大模型 Pro 版本，中文优化",
        },
        {
            "model_id": "gpt-4o",
            "model_name": "GPT-4o",
            "provider": "openai",
            "api_key_env": "OPENAI_API_KEY",
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_length": 128000,
            "supports_vision": True,
            "supports_streaming": True,
            "supports_function_calling": True,
            "cost_input_per_1k": 0.0025,
            "cost_output_per_1k": 0.01,
            "enabled": True,
            "priority": 90,
            "tags": ["vision", "multimodal"],
        },
        {
            "model_id": "gpt-4o-mini",
            "model_name": "GPT-4o Mini",
            "provider": "openai",
            "api_key_env": "OPENAI_API_KEY",
            "temperature": 0.7,
            "max_tokens": 16384,
            "context_length": 128000,
            "supports_vision": True,
            "supports_streaming": True,
            "cost_input_per_1k": 0.00015,
            "cost_output_per_1k": 0.0006,
            "enabled": True,
            "priority": 80,
            "tags": ["fast", "cheap"],
        },
        {
            "model_id": "deepseek-chat",
            "model_name": "DeepSeek Chat",
            "provider": "deepseek",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_length": 64000,
            "supports_streaming": True,
            "cost_input_per_1k": 0.00014,
            "cost_output_per_1k": 0.00028,
            "enabled": True,
            "priority": 70,
            "tags": ["cheap", "chinese"],
        },
        {
            "model_id": "claude-3-5-sonnet",
            "model_name": "Claude 3.5 Sonnet",
            "provider": "anthropic",
            "api_key_env": "ANTHROPIC_API_KEY",
            "temperature": 0.7,
            "max_tokens": 4096,
            "context_length": 200000,
            "supports_vision": True,
            "supports_streaming": True,
            "cost_input_per_1k": 0.003,
            "cost_output_per_1k": 0.015,
            "enabled": True,
            "priority": 85,
            "tags": ["long-context", "vision"],
        },
        {
            "model_id": "qwen-max",
            "model_name": "通义千问 Max",
            "provider": "qwen",
            "api_key_env": "DASHSCOPE_API_KEY",
            "temperature": 0.7,
            "max_tokens": 8192,
            "context_length": 32000,
            "supports_streaming": True,
            "enabled": True,
            "priority": 75,
            "tags": ["chinese"],
        },
    ]


def _get_default_agents() -> List[Dict]:
    return [
        {
            "agent_name": "WordAgent",
            "description": "Word 文档处理 Agent，负责排版、格式化、模板应用",
            "version": "1.0.0",
            "system_prompt": (
                "你是专业的 Word 文档处理助手。你可以：\n"
                "1. 分析文档结构（标题、正文、表格、图片）\n"
                "2. 应用规范格式（字体、字号、行距、段落）\n"
                "3. 套用模板样式\n"
                "4. 生成目录、页眉页脚\n"
                "5. 检查和修正格式问题\n\n"
                "请根据用户需求，精确执行文档处理任务。"
            ),
            "model_priority": ["doubao-pro", "gpt-4o", "deepseek-chat"],
            "available_tools": [
                {"name": "docx_parser", "enabled": True},
                {"name": "format_engine", "enabled": True},
                {"name": "template_engine", "enabled": True},
                {"name": "style_checker", "enabled": True},
            ],
            "timeout": 120,
            "max_retries": 3,
            "enable_quality_check": True,
            "quality_threshold": 0.8,
            "enabled": True,
            "tags": ["document", "formatting"],
        },
        {
            "agent_name": "PPTAgent",
            "description": "PPT 演示文稿 Agent，负责生成、美化、设计幻灯片",
            "version": "1.0.0",
            "system_prompt": (
                "你是专业的 PPT 设计助手。你可以：\n"
                "1. 根据大纲生成完整演示文稿\n"
                "2. 设计专业版式和配色\n"
                "3. 插入图表、图片、表格\n"
                "4. 应用主题和模板\n"
                "5. 优化内容结构和视觉效果\n\n"
                "请确保设计专业、内容清晰、视觉美观。"
            ),
            "model_priority": ["gpt-4o", "doubao-pro", "claude-3-5-sonnet"],
            "available_tools": [
                {"name": "ppt_generator", "enabled": True},
                {"name": "design_engine", "enabled": True},
                {"name": "chart_maker", "enabled": True},
                {"name": "template_engine", "enabled": True},
            ],
            "timeout": 180,
            "max_retries": 3,
            "enable_quality_check": True,
            "quality_threshold": 0.75,
            "enabled": True,
            "tags": ["presentation", "design"],
        },
        {
            "agent_name": "ExcelAgent",
            "description": "Excel 表格处理 Agent，负责数据分析、公式、图表",
            "version": "1.0.0",
            "system_prompt": (
                "你是专业的 Excel 数据分析助手。你可以：\n"
                "1. 清洗和整理数据\n"
                "2. 生成公式和函数\n"
                "3. 创建数据透视表\n"
                "4. 生成图表\n"
                "5. 统计分析和预测\n\n"
                "请确保公式正确、数据准确、分析专业。"
            ),
            "model_priority": ["doubao-pro", "gpt-4o", "deepseek-chat"],
            "available_tools": [
                {"name": "excel_parser", "enabled": True},
                {"name": "formula_engine", "enabled": True},
                {"name": "chart_engine", "enabled": True},
                {"name": "data_analyzer", "enabled": True},
            ],
            "timeout": 120,
            "max_retries": 3,
            "enable_quality_check": True,
            "quality_threshold": 0.8,
            "enabled": True,
            "tags": ["spreadsheet", "analysis"],
        },
    ]


def _get_default_prompts() -> List[Dict]:
    return [
        {
            "name": "word_format",
            "version": "1.0.0",
            "content": (
                "请对以下 Word 文档进行规范化排版：\n\n"
                "文档内容：\n{content}\n\n"
                "排版要求：\n"
                "- 正文：宋体/Times New Roman，小四，1.25倍行距，首行缩进2字符\n"
                "- 一级标题：黑体，小三，加粗，段前段后6磅\n"
                "- 二级标题：黑体，四号，加粗\n"
                "- 三级标题：黑体，小四，加粗\n"
                "- 表格：三线表样式\n\n"
                "请输出排版后的文档。"
            ),
            "description": "Word 文档标准排版",
            "agent": "WordAgent",
            "variables": ["content"],
            "status": "active",
            "is_default": True,
            "tags": ["formatting", "standard"],
        },
        {
            "name": "word_academic",
            "version": "1.0.0",
            "content": (
                "请按学术论文格式排版以下文档：\n\n"
                "文档内容：\n{content}\n\n"
                "学术格式要求：\n"
                "- 题目：黑体，二号，居中\n"
                "- 摘要/关键词：宋体，五号\n"
                "- 正文：宋体，小四，1.5倍行距\n"
                "- 一级标题：黑体，四号\n"
                "- 参考文献：宋体，五号，悬挂缩进\n\n"
                "请输出排版后的文档。"
            ),
            "description": "学术论文排版",
            "agent": "WordAgent",
            "variables": ["content"],
            "status": "active",
            "is_default": False,
            "tags": ["academic", "paper"],
        },
        {
            "name": "ppt_generate",
            "version": "1.0.0",
            "content": (
                "请根据以下内容生成专业 PPT：\n\n"
                "主题：{topic}\n"
                "内容大纲：\n{outline}\n\n"
                "要求：\n"
                "- 风格：{style}\n"
                "- 页数：约 {slides} 页\n"
                "- 包含封面、目录、内容页、总结页\n"
                "- 配色专业，版式清晰\n"
                "- 适当使用图表和图示"
            ),
            "description": "PPT 生成",
            "agent": "PPTAgent",
            "variables": ["topic", "outline", "style", "slides"],
            "status": "active",
            "is_default": True,
            "tags": ["generation"],
        },
        {
            "name": "excel_analyze",
            "version": "1.0.0",
            "content": (
                "请分析以下 Excel 数据：\n\n"
                "数据：\n{data}\n\n"
                "分析需求：{requirement}\n\n"
                "请提供：\n"
                "1. 数据概览\n"
                "2. 关键指标计算\n"
                "3. 趋势分析\n"
                "4. 异常发现\n"
                "5. 建议"
            ),
            "description": "Excel 数据分析",
            "agent": "ExcelAgent",
            "variables": ["data", "requirement"],
            "status": "active",
            "is_default": True,
            "tags": ["analysis"],
        },
    ]


def _get_default_skills() -> List[Dict]:
    return [
        {
            "skill_name": "论文排版",
            "description": "一键按学术规范排版论文",
            "version": "1.0.0",
            "workflow": [
                {"step": "parse", "agent": "WordAgent", "action": "parse_document"},
                {"step": "analyze", "agent": "WordAgent", "action": "analyze_structure"},
                {"step": "format", "agent": "WordAgent", "action": "apply_academic_format"},
                {"step": "check", "agent": "WordAgent", "action": "quality_check"},
            ],
            "tools": ["docx_parser", "format_engine", "style_checker"],
            "prompt": "word_academic",
            "trigger_keywords": ["论文", "学术", "排版", "毕业论文"],
            "enabled": True,
            "tags": ["academic", "word"],
        },
        {
            "skill_name": "PPT设计",
            "description": "根据内容生成专业 PPT",
            "version": "1.0.0",
            "workflow": [
                {"step": "outline", "agent": "PPTAgent", "action": "generate_outline"},
                {"step": "design", "agent": "PPTAgent", "action": "design_slides"},
                {"step": "visual", "agent": "PPTAgent", "action": "add_visuals"},
                {"step": "check", "agent": "PPTAgent", "action": "quality_check"},
            ],
            "tools": ["ppt_generator", "design_engine", "chart_maker"],
            "prompt": "ppt_generate",
            "trigger_keywords": ["PPT", "演示", "幻灯片", "汇报"],
            "enabled": True,
            "tags": ["presentation"],
        },
        {
            "skill_name": "数据分析",
            "description": "Excel 数据清洗与分析",
            "version": "1.0.0",
            "workflow": [
                {"step": "parse", "agent": "ExcelAgent", "action": "parse_data"},
                {"step": "clean", "agent": "ExcelAgent", "action": "clean_data"},
                {"step": "analyze", "agent": "ExcelAgent", "action": "analyze_data"},
                {"step": "visualize", "agent": "ExcelAgent", "action": "create_charts"},
            ],
            "tools": ["excel_parser", "formula_engine", "chart_engine", "data_analyzer"],
            "prompt": "excel_analyze",
            "trigger_keywords": ["分析", "数据", "统计", "图表", "Excel"],
            "enabled": True,
            "tags": ["analysis", "excel"],
        },
    ]


def _get_default_workflows() -> List[Dict]:
    return [
        {
            "workflow_name": "word_processing",
            "description": "Word 文档标准处理流程",
            "version": "1.0.0",
            "steps": [
                {"step_id": "parse", "name": "解析文档", "agent": "WordAgent", "action": "parse"},
                {"step_id": "analyze", "name": "分析结构", "agent": "WordAgent", "action": "analyze"},
                {"step_id": "format", "name": "应用格式", "agent": "WordAgent", "action": "format"},
                {"step_id": "generate", "name": "生成文件", "agent": "WordAgent", "action": "generate"},
                {"step_id": "check", "name": "质量检查", "agent": "WordAgent", "action": "check"},
            ],
            "timeout": 300,
            "enabled": True,
            "tags": ["word"],
        },
        {
            "workflow_name": "ppt_generation",
            "description": "PPT 生成流程",
            "version": "1.0.0",
            "steps": [
                {"step_id": "outline", "name": "生成大纲", "agent": "PPTAgent", "action": "outline"},
                {"step_id": "content", "name": "生成内容", "agent": "PPTAgent", "action": "content"},
                {"step_id": "design", "name": "设计版式", "agent": "PPTAgent", "action": "design"},
                {"step_id": "visual", "name": "添加图表", "agent": "PPTAgent", "action": "visual"},
                {"step_id": "check", "name": "质量检查", "agent": "PPTAgent", "action": "check"},
            ],
            "timeout": 300,
            "enabled": True,
            "tags": ["ppt"],
        },
        {
            "workflow_name": "excel_processing",
            "description": "Excel 数据处理流程",
            "version": "1.0.0",
            "steps": [
                {"step_id": "parse", "name": "解析数据", "agent": "ExcelAgent", "action": "parse"},
                {"step_id": "clean", "name": "清洗数据", "agent": "ExcelAgent", "action": "clean"},
                {"step_id": "analyze", "name": "分析数据", "agent": "ExcelAgent", "action": "analyze"},
                {"step_id": "formula", "name": "应用公式", "agent": "ExcelAgent", "action": "formula"},
                {"step_id": "chart", "name": "生成图表", "agent": "ExcelAgent", "action": "chart"},
            ],
            "timeout": 300,
            "enabled": True,
            "tags": ["excel"],
        },
    ]
