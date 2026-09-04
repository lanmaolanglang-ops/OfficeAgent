"""
认证中间件（预留，未来对接用户系统）
"""
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.config import settings
from ...security.auth.jwt import JWTManager
from ...security.auth.token import TokenManager
from ...security.audit import AuditAction, get_audit_logger
from ...security.permission.database_rbac import (
    DatabasePermissionResolver,
    PermissionDecision,
)


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件"""

    # 不需要认证的路径
    PUBLIC_PATHS = {
        "/docs", "/redoc", "/openapi.json",
        "/api/health", "/health", "/",
    }

    def __init__(self, app):
        super().__init__(app)
        self._jwt: JWTManager | None = None
        self._tokens: TokenManager | None = None
        self._permissions: DatabasePermissionResolver | None = None

    def _get_jwt(self) -> JWTManager:
        if self._jwt is None:
            self._jwt = JWTManager(secret_key=settings.jwt_secret or None)
        return self._jwt

    def _get_tokens(self) -> TokenManager:
        if self._tokens is None:
            self._tokens = TokenManager(jwt_manager=self._get_jwt())
        return self._tokens

    def _get_permissions(self) -> DatabasePermissionResolver:
        if self._permissions is None:
            self._permissions = DatabasePermissionResolver()
        return self._permissions

    @staticmethod
    def _required_permissions(request: Request) -> tuple[str, ...]:
        path = request.url.path
        if path == "/metrics" or path.startswith((
            "/api/logs", "/api/trace", "/api/metrics",
        )):
            return ("admin:audit",)
        if path.startswith(("/api/config", "/api/settings")):
            return ("admin:config",)
        if path.startswith("/api/security"):
            return ("admin:user",)
        if path == "/api/chat":
            return ("task:create", "model:call")
        if path.startswith("/api/file"):
            if path.startswith("/api/file/stats"):
                return ("admin:audit",)
            if path == "/api/file/cleanup":
                return ("admin:config",)
            if request.method == "DELETE":
                return ("file:delete",)
            if "/download/" in path:
                return ("file:download",)
            if request.method == "GET":
                return ("file:read",)
            return ("file:write",)
        if path.startswith("/api/task"):
            if path == "/api/task/create":
                return ("task:create",)
            if path.endswith("/cancel"):
                return ("task:cancel",)
            return ("task:view",)
        return ()

    @staticmethod
    def _resource_target(path: str) -> tuple[str, str] | None:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3 and parts[:2] == ["api", "file"]:
            if parts[2] == "download" and len(parts) >= 4:
                return "file", parts[3]
            if (parts[2] == "upload" and len(parts) >= 5
                    and parts[3] == "multipart"):
                return "file", parts[4]
            if parts[2] not in {"upload", "trash", "stats", "cleanup"}:
                return "file", parts[2]
        if len(parts) >= 3 and parts[:2] == ["api", "task"]:
            if parts[2] != "create":
                return "task", parts[2]
        return None

    def _authorize(self, request: Request, user_id: str) -> JSONResponse | None:
        required = self._required_permissions(request)
        if not required:
            return None
        try:
            decision = self._get_permissions().check(user_id, required)
        except Exception as exc:
            decision = PermissionDecision(
                False, reason=f"权限服务不可用: {type(exc).__name__}"
            )
        if decision.allowed:
            request.state.user_role = decision.role
            target = self._resource_target(request.url.path)
            if target is None:
                return None
            decision = self._get_permissions().check_ownership(user_id, *target)
            if decision.allowed:
                return None
        message = decision.reason or "权限不足"
        self._audit(request, AuditAction.ACCESS_DENIED, "denied", user_id, message)
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "error_code": "PERMISSION_DENIED",
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def _audit(request: Request, action: AuditAction, status: str,
               user_id: str | None = None, reason: str = "") -> None:
        from ...security.client_identity import resolve_request_client
        get_audit_logger().log(
            action, status, user_id=user_id,
            resource=request.url.path,
            ip_address=resolve_request_client(request),
            details={"method": request.method, "reason": reason} if reason else {
                "method": request.method
            },
            risk_level="warning" if status == "denied" else "info",
        )

    @classmethod
    def _audit_business_operation(cls, request: Request, status_code: int) -> None:
        path = request.url.path
        action = None
        if path.startswith("/api/file"):
            if request.method == "POST" and "upload" in path:
                action = AuditAction.FILE_UPLOAD
            elif request.method == "DELETE":
                action = AuditAction.FILE_DELETE
            elif "download" in path:
                action = AuditAction.FILE_DOWNLOAD
        elif request.method == "POST" and path in {"/api/chat", "/api/task/create"}:
            action = AuditAction.WORKFLOW_CREATE
        elif request.method == "POST" and path.startswith("/api/task/") \
                and path.endswith("/cancel"):
            action = AuditAction.WORKFLOW_CANCEL
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"} and path.startswith(
            ("/api/settings", "/api/config", "/api/security")
        ):
            action = AuditAction.CONFIG_CHANGE
        if action is not None:
            cls._audit(
                request, action, "success" if status_code < 400 else "error",
                getattr(request.state, "user_id", None),
            )

    @classmethod
    async def _call_and_audit(cls, request: Request, call_next):
        response = await call_next(request)
        cls._audit_business_operation(request, response.status_code)
        return response

    async def dispatch(self, request: Request, call_next):
        # 认证未开启时直接放行
        if not settings.auth_enabled:
            request.state.user_id = "anonymous"
            request.state.request_id = str(uuid.uuid4())
            return await self._call_and_audit(request, call_next)

        path = request.url.path

        # 公开路径放行
        if path in self.PUBLIC_PATHS:
            request.state.user_id = "anonymous"
            request.state.request_id = str(uuid.uuid4())
            return await self._call_and_audit(request, call_next)

        authorization = request.headers.get("Authorization", "")
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return self._unauthorized(request, "Authorization 必须使用 Bearer token")
            payload = self._get_jwt().verify(token.strip())
            if payload is None:
                return self._unauthorized(request, "无效或已过期的 Bearer token")
            request.state.user_id = payload.user_id
            request.state.user_role = payload.role
            request.state.request_id = str(uuid.uuid4())
            self._audit(request, AuditAction.ACCESS_GRANTED, "success", payload.user_id)
            denied = self._authorize(request, payload.user_id)
            if denied is not None:
                return denied
            return await self._call_and_audit(request, call_next)

        # 检查 API Key
        # Accept API keys only in a header; URLs are persisted by browsers,
        # proxies, analytics systems and request-history logs.
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return self._unauthorized(request, "缺少认证信息，请提供 Bearer token 或 X-API-Key")

        try:
            manager = self._get_tokens()
            info = manager.verify_api_key(api_key)
            if info is None and settings.api_keys:
                manager.import_legacy_api_keys(settings.api_keys)
                info = manager.verify_api_key(api_key)
        except Exception:
            return self._unauthorized(request, "认证服务暂时不可用")
        if info is None:
            return self._unauthorized(request, "无效的API Key")

        request.state.user_id = info.user_id
        request.state.user_role = info.role
        request.state.request_id = str(uuid.uuid4())
        self._audit(request, AuditAction.ACCESS_GRANTED, "success", info.user_id)

        denied = self._authorize(request, info.user_id)
        if denied is not None:
            return denied

        return await self._call_and_audit(request, call_next)

    def _unauthorized(self, request: Request, message: str) -> JSONResponse:
        self._audit(request, AuditAction.ACCESS_DENIED, "denied", reason=message)
        return JSONResponse(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            content={
                "success": False,
                "error_code": "AUTH_ERROR",
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
