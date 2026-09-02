"""
Agent Permission System - Agent权限管理
控制每个Agent可以使用哪些工具和资源
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
import time

try:
    from office_agent.logging_system import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ToolInfo:
    """工具信息"""
    name: str
    description: str
    risk_level: RiskLevel
    enabled: bool = True
    allowed_agents: set[str] = field(default_factory=set)  # 空表示所有Agent可用
    requires_approval: bool = False  # 是否需要人工审批
    timeout_seconds: int = 30
    max_calls_per_minute: int = 60


# 预定义工具注册表
DEFAULT_TOOLS: dict[str, ToolInfo] = {
    # 低风险工具
    "docx_parser": ToolInfo(
        name="docx_parser",
        description="解析Word文档",
        risk_level=RiskLevel.LOW,
        allowed_agents={"word", "ppt", "excel", "research"},
    ),
    "pptx_parser": ToolInfo(
        name="pptx_parser",
        description="解析PPT文档",
        risk_level=RiskLevel.LOW,
        allowed_agents={"ppt", "word", "research"},
    ),
    "xlsx_parser": ToolInfo(
        name="xlsx_parser",
        description="解析Excel文档",
        risk_level=RiskLevel.LOW,
        allowed_agents={"excel", "research"},
    ),
    "pdf_parser": ToolInfo(
        name="pdf_parser",
        description="解析PDF文档",
        risk_level=RiskLevel.LOW,
        allowed_agents={"word", "ppt", "excel", "research"},
    ),
    "format_engine": ToolInfo(
        name="format_engine",
        description="文档格式化引擎",
        risk_level=RiskLevel.LOW,
        allowed_agents={"word", "ppt", "excel"},
    ),
    # 中风险工具
    "file_writer": ToolInfo(
        name="file_writer",
        description="文件写入",
        risk_level=RiskLevel.MEDIUM,
        allowed_agents={"word", "ppt", "excel", "pdf", "image"},
        timeout_seconds=10,
    ),
    "chart_generator": ToolInfo(
        name="chart_generator",
        description="图表生成",
        risk_level=RiskLevel.MEDIUM,
        allowed_agents={"excel", "ppt"},
    ),
    "browser": ToolInfo(
        name="browser",
        description="网页浏览器",
        risk_level=RiskLevel.MEDIUM,
        allowed_agents={"research"},
        timeout_seconds=60,
        max_calls_per_minute=20,
    ),
    # 高风险工具
    "python_executor": ToolInfo(
        name="python_executor",
        description="Python代码执行（沙箱）",
        risk_level=RiskLevel.HIGH,
        allowed_agents={"excel"},  # 只有Excel Agent可以执行Python
        requires_approval=True,
        timeout_seconds=30,
        max_calls_per_minute=10,
    ),
    "formula_engine": ToolInfo(
        name="formula_engine",
        description="Excel公式引擎",
        risk_level=RiskLevel.MEDIUM,
        allowed_agents={"excel"},
    ),
    # 关键风险工具
    "shell_command": ToolInfo(
        name="shell_command",
        description="系统Shell命令",
        risk_level=RiskLevel.CRITICAL,
        enabled=False,  # 默认禁用
        requires_approval=True,
        timeout_seconds=10,
        max_calls_per_minute=1,
    ),
    "filesystem_access": ToolInfo(
        name="filesystem_access",
        description="文件系统直接访问",
        risk_level=RiskLevel.HIGH,
        enabled=False,  # 默认禁用，使用文件安全管理器代替
    ),
}


def _agent_tool_permissions(tools: dict[str, ToolInfo]) -> dict[str, set[str]]:
    """Build the compatibility view from ToolInfo, the sole policy source."""
    permissions: dict[str, set[str]] = {}
    for tool in tools.values():
        for agent in tool.allowed_agents:
            permissions.setdefault(agent, set()).add(tool.name)
    return permissions


# Compatibility/export view. Authorization below consults ToolInfo directly so
# this derived dictionary cannot diverge from the actual decision policy.
AGENT_TOOL_PERMISSIONS: dict[str, set[str]] = _agent_tool_permissions(DEFAULT_TOOLS)


class ToolRegistry:
    """工具注册表"""

    def __init__(self, tools: dict[str, ToolInfo] | None = None):
        self._tools: dict[str, ToolInfo] = dict(tools or DEFAULT_TOOLS)

    def register(self, tool: ToolInfo):
        """注册工具"""
        self._tools[tool.name] = tool
        logger.info(f"Tool registered: {tool.name} ({tool.risk_level.value})")

    def get(self, name: str) -> ToolInfo | None:
        """获取工具信息"""
        return self._tools.get(name)

    def is_enabled(self, name: str) -> bool:
        """工具是否启用"""
        tool = self._tools.get(name)
        return tool is not None and tool.enabled

    def list_tools(self, agent: str | None = None) -> list[ToolInfo]:
        """列出工具"""
        tools = list(self._tools.values())
        if agent:
            tools = [t for t in tools if not t.allowed_agents or agent in t.allowed_agents]
        return [t for t in tools if t.enabled]

    def check_access(self, agent: str, tool_name: str) -> tuple[bool, str]:
        """
        检查Agent是否可以使用工具
        返回(是否允许, 原因)
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return False, f"工具不存在: {tool_name}"
        if not tool.enabled:
            return False, f"工具已禁用: {tool_name}"

        # ToolInfo.allowed_agents is the sole authorization source. An empty
        # set means all agents, as documented by ToolInfo.
        if tool.allowed_agents and agent not in tool.allowed_agents:
            return False, f"工具 '{tool_name}' 不允许Agent '{agent}' 使用"

        return True, "允许"

    def requires_approval(self, tool_name: str) -> bool:
        """工具是否需要审批"""
        tool = self._tools.get(tool_name)
        return tool is not None and tool.requires_approval


class AgentPermissionManager:
    """Agent权限管理器"""

    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry or ToolRegistry()
        self._agent_blacklist: dict[str, set[str]] = {}  # agent -> blocked tools
        self._call_counts: dict[tuple[str, str], list[float]] = {}  # (agent, tool) -> timestamps
        self._lock = threading.RLock()

    def can_use_tool(self, agent: str, tool_name: str) -> tuple[bool, str]:
        """检查Agent是否可以使用工具（含频率限制）"""
        # 基本权限检查
        allowed, reason = self.registry.check_access(agent, tool_name)
        if not allowed:
            return False, reason

        # 黑名单检查
        with self._lock:
            if tool_name in self._agent_blacklist.get(agent, set()):
                return False, f"工具 '{tool_name}' 已被Agent '{agent}' 禁用"

            tool = self.registry.get(tool_name)
            if tool:
                key = (agent, tool_name)
                now = time.time()
                self._call_counts[key] = [
                    timestamp for timestamp in self._call_counts.get(key, [])
                    if now - timestamp < 60
                ]
                if len(self._call_counts[key]) >= tool.max_calls_per_minute:
                    return False, f"调用频率超限: {tool.max_calls_per_minute}/分钟"
                self._call_counts[key].append(now)

        return True, "允许"

    def block_tool(self, agent: str, tool_name: str):
        """禁用Agent的某个工具"""
        with self._lock:
            if agent not in self._agent_blacklist:
                self._agent_blacklist[agent] = set()
            self._agent_blacklist[agent].add(tool_name)
        logger.warning(f"Tool blocked: agent={agent}, tool={tool_name}")

    def unblock_tool(self, agent: str, tool_name: str):
        """解禁Agent的工具"""
        with self._lock:
            if agent in self._agent_blacklist:
                self._agent_blacklist[agent].discard(tool_name)

    def get_agent_tools(self, agent: str) -> list[ToolInfo]:
        """获取Agent可用的工具列表"""
        return self.registry.list_tools(agent)

    def check_tool_risk(self, tool_name: str) -> RiskLevel:
        """获取工具风险等级"""
        tool = self.registry.get(tool_name)
        return tool.risk_level if tool else RiskLevel.CRITICAL
