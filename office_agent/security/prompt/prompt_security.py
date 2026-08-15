"""
Prompt Security Layer - Prompt注入防护
检测用户输入、文件内容、外部文本中的注入攻击
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class InjectionType(str, Enum):
    SYSTEM_OVERRIDE = "system_override"      # 忽略系统指令
    PROMPT_LEAK = "prompt_leak"              # 泄露系统提示
    ROLE_PLAY = "role_play"                  # 角色扮演绕过
    COMMAND_INJECTION = "command_injection"  # 命令注入
    DATA_EXFILTRATION = "data_exfiltration"  # 数据窃取
    JAILBREAK = "jailbreak"                  # 越狱
    INDIRECT = "indirect"                    # 间接注入（文件内容）


@dataclass
class InjectionMatch:
    """检测到的注入"""
    type: InjectionType
    pattern: str
    position: int
    severity: str  # low/medium/high/critical
    description: str


@dataclass
class SecurityScanResult:
    """安全扫描结果"""
    is_safe: bool
    risk_level: str  # safe/low/medium/high/critical
    matches: list[InjectionMatch] = field(default_factory=list)
    sanitized_text: str = ""
    recommendations: list[str] = field(default_factory=list)

    @property
    def has_injection(self) -> bool:
        return len(self.matches) > 0


# 注入模式库
INJECTION_PATTERNS: list[tuple[InjectionType, str, str, str]] = [
    # 系统指令覆盖
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)ignore\s+(all\s+)?(previous|above|prior|system)\s+(instructions?|prompts?|rules?)", "high", "尝试忽略系统指令"),
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?)", "high", "尝试忽略指令"),
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)forget\s+(all\s+)?(previous|above|prior|your)\s+(instructions?|prompts?|rules?)", "high", "尝试让AI忘记指令"),
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)you\s+are\s+now\s+(a|an)\s+", "medium", "尝试改变AI角色"),
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)new\s+instructions?:", "medium", "尝试设置新指令"),
    (InjectionType.SYSTEM_OVERRIDE, r"(?i)system\s*prompt\s*:", "medium", "尝试覆盖系统提示"),

    # Prompt泄露
    (InjectionType.PROMPT_LEAK, r"(?i)(reveal|show|print|output|display|echo)\s+(me\s+)?(your\s+)?(system\s+)?(prompt|instructions?|rules?)", "high", "尝试获取系统提示"),
    (InjectionType.PROMPT_LEAK, r"(?i)what\s+(are|were)\s+your\s+(original\s+)?instructions?", "medium", "尝试询问原始指令"),
    (InjectionType.PROMPT_LEAK, r"(?i)repeat\s+(the\s+)?(above|previous|system)\s+(text|prompt|instructions?)", "high", "尝试重复系统文本"),
    (InjectionType.PROMPT_LEAK, r"<\|system\|>|</s>|<\|im_start\|>|<\|im_end\|>", "critical", "特殊标记注入"),

    # 角色扮演绕过
    (InjectionType.ROLE_PLAY, r"(?i)pretend\s+(to\s+be|you\s+are)", "medium", "角色扮演绕过"),
    (InjectionType.ROLE_PLAY, r"(?i)act\s+as\s+(a|an|if)", "medium", "要求扮演其他角色"),
    (InjectionType.ROLE_PLAY, r"(?i)DAN\s+(mode|prompt)|do\s+anything\s+now", "high", "DAN越狱模式"),
    (InjectionType.ROLE_PLAY, r"(?i)jailbreak|developer\s+mode", "high", "越狱/开发者模式"),

    # 命令注入
    (InjectionType.COMMAND_INJECTION, r"(?i)(execute|run|eval|exec)\s*\(.*\)", "high", "代码执行"),
    (InjectionType.COMMAND_INJECTION, r"(?i)(os\.system|subprocess|shell_exec|child_process)", "critical", "系统命令调用"),
    (InjectionType.COMMAND_INJECTION, r"`[^`]+`", "low", "反引号命令（可能是Markdown）"),

    # 数据窃取
    (InjectionType.DATA_EXFILTRATION, r"(?i)(send|upload|post|fetch|http)\s+(the\s+)?(data|file|content|info)", "medium", "尝试外传数据"),
    (InjectionType.DATA_EXFILTRATION, r"(?i)curl\s+|wget\s+|requests?\.(get|post)", "high", "网络请求"),

    # 间接注入（文件内容中）
    (InjectionType.INDIRECT, r"(?i)IMPORTANT:\s*(IGNORE|OVERRIDE|NEW)", "medium", "文件中的注入指令"),
    (InjectionType.INDIRECT, r"(?i)\[SYSTEM\]|\[ADMIN\]|\[OVERRIDE\]", "medium", "伪造系统消息"),
]


class PromptSecurityScanner:
    """Prompt安全扫描器"""

    def __init__(self, strict_mode: bool = False):
        self.strict_mode = strict_mode
        self._patterns = INJECTION_PATTERNS

    def scan(self, text: str, source: str = "user") -> SecurityScanResult:
        """
        扫描文本中的注入攻击
        source: user | file | external
        """
        if not text:
            return SecurityScanResult(is_safe=True, risk_level="safe")

        matches: list[InjectionMatch] = []
        for inj_type, pattern, severity, desc in self._patterns:
            for m in re.finditer(pattern, text):
                # 文件来源降低严重度（可能是正常内容）
                actual_severity = severity
                if source == "file" and severity == "medium":
                    actual_severity = "low"
                matches.append(InjectionMatch(
                    type=inj_type,
                    pattern=m.group(),
                    position=m.start(),
                    severity=actual_severity,
                    description=desc,
                ))

        # 计算风险等级
        if not matches:
            return SecurityScanResult(
                is_safe=True, risk_level="safe",
                sanitized_text=text,
            )

        severities = [m.severity for m in matches]
        if "critical" in severities:
            risk = "critical"
        elif "high" in severities:
            risk = "high"
        elif "medium" in severities:
            risk = "medium"
        else:
            risk = "low"

        is_safe = risk in ("safe", "low") and not self.strict_mode

        # 生成建议
        recommendations = []
        if risk in ("critical", "high"):
            recommendations.append("检测到高风险注入，建议拒绝该输入")
            recommendations.append("移除或转义可疑内容后再处理")
        if risk == "medium":
            recommendations.append("检测到可疑内容，建议人工审查")
        if any(m.type == InjectionType.PROMPT_LEAK for m in matches):
            recommendations.append("不要泄露系统提示词或内部指令")

        # 简单清理（移除可疑行）
        sanitized = text
        if risk in ("high", "critical"):
            lines = text.split("\n")
            clean_lines = []
            for line in lines:
                line_safe = True
                for inj_type, pattern, sev, desc in self._patterns:
                    if sev in ("critical", "high") and re.search(pattern, line):
                        line_safe = False
                        break
                if line_safe:
                    clean_lines.append(line)
            sanitized = "\n".join(clean_lines)

        return SecurityScanResult(
            is_safe=is_safe,
            risk_level=risk,
            matches=matches,
            sanitized_text=sanitized,
            recommendations=recommendations,
        )

    def scan_file_content(self, content: str) -> SecurityScanResult:
        """扫描文件内容（间接注入）"""
        return self.scan(content, source="file")

    def sanitize(self, text: str) -> str:
        """清理文本中的注入内容"""
        result = self.scan(text)
        return result.sanitized_text or text

    def is_safe(self, text: str) -> bool:
        """快速检查是否安全"""
        return self.scan(text).is_safe


class ModelSecurityManager:
    """
    模型安全管理器
    管理API Key、调用权限、Key轮换
    """

    def __init__(self):
        self._api_keys: dict[str, dict] = {}  # provider -> {key, created, rotated}
        self._call_log: list[dict] = []
        self._rate_limits: dict[str, dict] = {}  # user_id -> {count, window}
        self.prompt_scanner = PromptSecurityScanner()

    def register_api_key(self, provider: str, key: str,
                         is_default: bool = False):
        """注册API Key（从配置读取，不硬编码）"""
        import time
        self._api_keys[provider] = {
            "key": key,
            "created_at": time.time(),
            "last_rotated": time.time(),
            "is_default": is_default,
        }

    def get_api_key(self, provider: str) -> str | None:
        """获取API Key"""
        info = self._api_keys.get(provider)
        return info["key"] if info else None

    def rotate_key(self, provider: str, new_key: str) -> bool:
        """轮换API Key"""
        import time
        if provider in self._api_keys:
            self._api_keys[provider]["key"] = new_key
            self._api_keys[provider]["last_rotated"] = time.time()
            return True
        return False

    def check_rate_limit(self, user_id: str, max_calls: int = 100,
                         window_seconds: int = 60) -> bool:
        """检查调用频率限制"""
        import time
        now = time.time()
        info = self._rate_limits.get(user_id, {"count": 0, "window_start": now})
        if now - info["window_start"] > window_seconds:
            info = {"count": 0, "window_start": now}
        info["count"] += 1
        self._rate_limits[user_id] = info
        return info["count"] <= max_calls

    def log_model_call(self, user_id: str, provider: str, model: str,
                       tokens: int = 0, has_injection: bool = False):
        """记录模型调用"""
        import time
        self._call_log.append({
            "user_id": user_id,
            "provider": provider,
            "model": model,
            "tokens": tokens,
            "has_injection": has_injection,
            "timestamp": time.time(),
        })

    def scan_prompt(self, prompt: str, source: str = "user") -> SecurityScanResult:
        """扫描Prompt安全性"""
        return self.prompt_scanner.scan(prompt, source)
