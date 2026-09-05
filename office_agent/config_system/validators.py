"""
配置校验器

启动时校验配置完整性，发现问题给出警告或阻止启动。
"""
from typing import List, Dict, Any, Tuple
from enum import Enum

from .schemas import (
    GlobalConfig, ModelConfigSchema, AgentConfigSchema,
)
from ..logging_system import get_logger
from ..models.model_schemas import normalize_model_id

logger = get_logger("config.validator")


class Severity(str, Enum):
    ERROR = "error"      # 阻止启动
    WARNING = "warning"  # 警告但继续
    INFO = "info"        # 仅提示，不影响启动


class ValidationIssue:
    def __init__(self, severity: Severity, category: str, message: str):
        self.severity = severity
        self.category = category
        self.message = message

    def __str__(self):
        return f"[{self.severity.value.upper()}] [{self.category}] {self.message}"


class ConfigValidator:
    """配置校验器"""

    def __init__(self):
        self.issues: List[ValidationIssue] = []

    def add(self, severity: Severity, category: str, message: str):
        self.issues.append(ValidationIssue(severity, category, message))

    def validate_global(self, config: GlobalConfig) -> List[ValidationIssue]:
        """校验全局配置"""
        # 环境
        if config.environment == "production":
            if config.debug:
                self.add(Severity.WARNING, "global",
                         "生产环境不应开启 debug 模式")
            if config.logging.level == "DEBUG":
                self.add(Severity.WARNING, "logging",
                         "生产环境日志级别不应为 DEBUG")

        # 存储路径
        import os
        storage_path = os.path.expanduser(config.storage.local_path)
        if not os.path.exists(storage_path):
            self.add(Severity.INFO if config.environment == "development" else Severity.WARNING,
                     "storage", f"存储路径不存在: {storage_path}（将自动创建）")

        # 默认模型
        if not config.default_model:
            self.add(Severity.WARNING, "model", "未设置默认模型")

        return self.issues

    def validate_models(self, models: List[Dict[str, Any]]) -> List[ValidationIssue]:
        """校验模型配置"""
        seen_ids = set()
        for m in models:
            mid = m.get("model_id", "")
            if not mid:
                self.add(Severity.ERROR, "model", "模型缺少 model_id")
                continue
            if mid in seen_ids:
                self.add(Severity.ERROR, "model", f"模型 ID 重复: {mid}")
            seen_ids.add(mid)

            # 校验 API Key
            key_env = m.get("api_key_env")
            if key_env and m.get("enabled"):
                import os
                if not os.environ.get(key_env):
                    self.add(Severity.WARNING, "model",
                             f"模型 {mid} 配置了 {key_env} 但环境变量未设置")

            # 校验参数
            try:
                ModelConfigSchema(**m)
            except Exception as e:
                self.add(Severity.ERROR, "model", f"模型 {mid} 配置无效: {e}")

        if not any(m.get("enabled") for m in models):
            self.add(Severity.WARNING, "model", "没有启用的模型")

        return self.issues

    def validate_agents(self, agents: List[Dict[str, Any]],
                        models: List[Dict[str, Any]] | None = None,
                        prompts: List[Dict[str, Any]] | None = None) -> List[ValidationIssue]:
        """校验 Agent 配置"""
        model_ids = {normalize_model_id(m.get("model_id")) for m in (models or [])}

        for a in agents:
            name = a.get("agent_name", "")
            if not name:
                self.add(Severity.ERROR, "agent", "Agent 缺少 agent_name")
                continue

            # 校验模型引用（历史 ID 经兼容层归一化后判定）
            for mid in a.get("model_priority", []):
                if model_ids and normalize_model_id(mid) not in model_ids:
                    self.add(Severity.WARNING, "agent",
                             f"Agent {name} 引用了不存在的模型: {mid}")

            # 校验 Prompt
            if a.get("system_prompt") is None and a.get("prompt_template") is None:
                self.add(Severity.WARNING, "agent",
                         f"Agent {name} 没有配置 system_prompt")

            # 校验参数
            try:
                AgentConfigSchema(**a)
            except Exception as e:
                self.add(Severity.ERROR, "agent", f"Agent {name} 配置无效: {e}")

        return self.issues

    def validate_prompts(self, prompts: List[Dict[str, Any]]) -> List[ValidationIssue]:
        """校验 Prompt 配置"""
        seen = set()
        for p in prompts:
            key = (p.get("name"), p.get("version"))
            if key in seen:
                self.add(Severity.ERROR, "prompt",
                         f"Prompt 重复: {p.get('name')} v{p.get('version')}")
            seen.add(key)

            if not p.get("content"):
                self.add(Severity.ERROR, "prompt",
                         f"Prompt {p.get('name')} 内容为空")

            # 检查变量声明
            import re
            content = p.get("content", "")
            used_vars = set(re.findall(r'\{(\w+)\}', content))
            declared_vars = set(p.get("variables", []))
            missing = used_vars - declared_vars
            if missing:
                self.add(Severity.WARNING, "prompt",
                         f"Prompt {p.get('name')} 使用了未声明的变量: {missing}")

        return self.issues

    def validate_workflows(self, workflows: List[Dict[str, Any]],
                           agents: List[Dict[str, Any]] | None = None) -> List[ValidationIssue]:
        """校验工作流配置"""
        agent_names = {a.get("agent_name") for a in (agents or [])}

        for wf in workflows:
            name = wf.get("workflow_name", "")
            steps = wf.get("steps", [])
            if not steps:
                self.add(Severity.WARNING, "workflow",
                         f"工作流 {name} 没有步骤")

            for i, step in enumerate(steps):
                sid = step.get("step_id", f"step_{i}")
                agent = step.get("agent")
                if agent and agent_names and agent not in agent_names:
                    self.add(Severity.WARNING, "workflow",
                             f"工作流 {name} 步骤 {sid} 引用了不存在的 Agent: {agent}")

                on_error = step.get("on_error", "stop")
                if on_error not in ("stop", "retry", "skip", "continue"):
                    self.add(Severity.ERROR, "workflow",
                             f"工作流 {name} 步骤 {sid} 的 on_error 无效: {on_error}")

        return self.issues

    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    def summary(self) -> str:
        errors = [i for i in self.issues if i.severity == Severity.ERROR]
        warnings = [i for i in self.issues if i.severity == Severity.WARNING]
        infos = [i for i in self.issues if i.severity == Severity.INFO]
        lines = [
            f"配置校验完成: {len(errors)} 错误, {len(warnings)} 警告, "
            f"{len(infos)} 提示"
        ]
        for i in self.issues:
            lines.append(f"  {i}")
        return "\n".join(lines)


def validate_all(global_config: GlobalConfig,
                 models: List[Dict],
                 agents: List[Dict],
                 prompts: List[Dict],
                 skills: List[Dict] | None = None,
                 workflows: List[Dict] | None = None,
                 strict: bool = False) -> Tuple[bool, List[ValidationIssue]]:
    """
    校验所有配置

    Returns:
        (passed, issues)
    """
    validator = ConfigValidator()
    validator.validate_global(global_config)
    validator.validate_models(models)
    validator.validate_agents(agents, models, prompts)
    validator.validate_prompts(prompts)
    if workflows:
        validator.validate_workflows(workflows, agents)

    issues = validator.issues
    passed = not validator.has_errors()

    if issues:
        logger.info(f"\n{validator.summary()}")

    if not passed and strict:
        logger.error("配置校验失败，阻止启动")
    elif not passed:
        logger.warning("配置存在错误，但将继续启动")

    return passed, issues
