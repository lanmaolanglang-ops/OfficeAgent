"""One API-entry prompt policy with bounded, metadata-only auditing."""
from __future__ import annotations

from fastapi import HTTPException, Request

from ...security.prompt import PromptAction, PromptSecurityScanner


def _build_scanner() -> PromptSecurityScanner:
    """Scanner policy follows SecurityConfig (prompt_strict_mode / enable_prompt_scan)."""
    try:
        from ...security.config import get_security_config
        cfg = get_security_config()
        return PromptSecurityScanner(strict_mode=bool(cfg.prompt_strict_mode))
    except Exception:
        return PromptSecurityScanner()


_prompt_scanner = _build_scanner()


def enforce_user_prompt(message: str, request: Request | None = None):
    """Apply the shared user-input policy before task state or queue writes."""
    try:
        from ...security.config import get_security_config
        if not get_security_config().enable_prompt_scan:
            return None
    except Exception:
        pass
    result = _prompt_scanner.scan(message, source="user")
    if result.action != PromptAction.REJECT:
        return result

    from ...security.audit import get_audit_logger

    user_id = "anonymous"
    ip = None
    if request is not None:
        from ...security.client_identity import resolve_request_client

        user_id = getattr(request.state, "user_id", "anonymous")
        ip = resolve_request_client(request)
    get_audit_logger().log_prompt_injection(
        user_id=user_id,
        matches=result.matches,
        ip=ip,
    )
    raise HTTPException(
        status_code=400,
        detail={
            "code": "PROMPT_POLICY_REJECTED",
            "message": "输入包含高风险指令操纵或模型控制标记",
            "risk_level": result.risk_level,
        },
    )
