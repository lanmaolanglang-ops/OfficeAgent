"""One API-entry prompt policy with bounded, metadata-only auditing."""
from __future__ import annotations

from fastapi import HTTPException, Request

from ...security.prompt import PromptAction, PromptSecurityScanner


_prompt_scanner = PromptSecurityScanner()


def enforce_user_prompt(message: str, request: Request | None = None):
    """Apply the shared user-input policy before task state or queue writes."""
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
