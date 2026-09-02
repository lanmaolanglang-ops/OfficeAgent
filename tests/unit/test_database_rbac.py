"""Database RBAC truth-source and API enforcement regressions."""
from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _session_factory():
    from office_agent.database.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_database_permissions_change_authorization_immediately():
    from office_agent.database.models import PermissionModel, RoleModel, User
    from office_agent.security.permission.database_rbac import (
        DatabasePermissionResolver,
        seed_default_rbac,
    )

    factory = _session_factory()
    seed_default_rbac(factory)
    seed_default_rbac(factory)
    with factory() as session:
        session.add(User(id="user-1", username="alice", role="user", is_active=True))
        session.commit()

    resolver = DatabasePermissionResolver(factory)
    assert not resolver.check("user-1", "admin:config").allowed

    with factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "user"))
        role.permissions = [*role.permissions, "admin:config"]
        session.commit()
    assert resolver.check("user-1", "admin:config").allowed

    with factory() as session:
        permission = session.scalar(select(PermissionModel).where(
            PermissionModel.name == "admin:config"
        ))
        session.delete(permission)
        session.commit()
    assert not resolver.check("user-1", "admin:config").allowed
    assert not resolver.check("missing", "file:read").allowed


def test_database_ownership_is_enforced_for_files_and_tasks():
    from office_agent.database.models import File, Task, User
    from office_agent.security.permission.database_rbac import (
        DatabasePermissionResolver,
        seed_default_rbac,
    )

    factory = _session_factory()
    seed_default_rbac(factory)
    with factory() as session:
        session.add_all([
            User(id="user-1", username="alice", role="user", is_active=True),
            User(id="user-2", username="bob", role="user", is_active=True),
            User(id="admin-1", username="admin", role="admin", is_active=True),
        ])
        session.flush()
        session.add(File(
            id="file-1", filename="x.txt", original_name="x.txt",
            file_type="text", extension=".txt", storage_path="uploads/x.txt",
            owner_id="user-1",
        ))
        session.add(Task(
            id="task-1", task_type="general", instruction="test", user_id="user-1"
        ))
        session.commit()

    resolver = DatabasePermissionResolver(factory)
    assert resolver.check_ownership("user-1", "file", "file-1").allowed
    assert resolver.check_ownership("user-1", "task", "task-1").allowed
    assert not resolver.check_ownership("user-2", "file", "file-1").allowed
    assert not resolver.check_ownership("user-2", "task", "task-1").allowed
    assert resolver.check_ownership("admin-1", "file", "file-1").allowed


def test_legacy_static_rbac_fails_closed_for_unknown_roles_and_permissions():
    from office_agent.security.permission import has_permission

    assert not has_permission("unknown-role", "file:read")
    assert not has_permission("admin", "not:registered")


def test_auth_middleware_enforces_database_permission_on_sensitive_routes(
        monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from office_agent.api.core.config import settings
    from office_agent.api.middleware.auth import AuthMiddleware
    from office_agent.security.auth import JWTManager
    from office_agent.security.permission.database_rbac import PermissionDecision

    secret = "test-secret-that-is-at-least-thirty-two-bytes"
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", secret)
    token = JWTManager(secret_key=secret, state_dir=tmp_path).create_access_token(
        "user-1", "alice", role="admin"
    )

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.get("/api/settings/model")(lambda: {"ok": True})
    client = TestClient(app)
    middleware = next(item for item in app.user_middleware if item.cls is AuthMiddleware)
    assert middleware is not None

    class DenyResolver:
        def check(self, _user_id, required):
            assert required == ("admin:config",)
            return PermissionDecision(False, role="admin", reason="数据库已撤销权限")

    monkeypatch.setattr(
        "office_agent.api.middleware.auth.DatabasePermissionResolver",
        lambda: DenyResolver(),
    )
    response = client.get(
        "/api/settings/model", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


def test_auth_middleware_denies_cross_user_object_access(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from office_agent.api.core.config import settings
    from office_agent.api.middleware.auth import AuthMiddleware
    from office_agent.security.auth import JWTManager
    from office_agent.security.permission.database_rbac import PermissionDecision

    secret = "test-secret-that-is-at-least-thirty-two-bytes"
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", secret)
    token = JWTManager(secret_key=secret, state_dir=tmp_path).create_access_token(
        "user-2", "bob", role="user"
    )

    class Resolver:
        def check(self, _user_id, required):
            assert required == ("file:read",)
            return PermissionDecision(True, role="user")

        def check_ownership(self, user_id, resource, resource_id):
            assert (user_id, resource, resource_id) == ("user-2", "file", "file-1")
            return PermissionDecision(False, role="user", reason="资源不属于当前用户")

    monkeypatch.setattr(
        "office_agent.api.middleware.auth.DatabasePermissionResolver",
        lambda: Resolver(),
    )
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.get("/api/file/file-1")(lambda: {"ok": True})
    response = TestClient(app).get(
        "/api/file/file-1", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"


def test_role_management_validates_and_updates_atomically(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from office_agent.api.router import security as security_router_module
    from office_agent.database.models import RoleModel
    from office_agent.security.permission.database_rbac import seed_default_rbac

    factory = _session_factory()
    seed_default_rbac(factory)
    monkeypatch.setattr(security_router_module, "_get_session", factory)
    app = FastAPI()
    app.include_router(security_router_module.router)
    client = TestClient(app)

    assert len(client.get("/api/security/roles").json()["data"]) == 3
    response = client.put(
        "/api/security/roles/user/permissions",
        json={"permissions": ["file:read", "admin:config"]},
    )
    assert response.status_code == 200
    with factory() as session:
        role = session.scalar(select(RoleModel).where(RoleModel.name == "user"))
        assert role.permissions == ["admin:config", "file:read"]

    assert client.put(
        "/api/security/roles/user/permissions",
        json={"permissions": ["not:registered"]},
    ).status_code == 400
    assert client.put(
        "/api/security/roles/admin/permissions",
        json={"permissions": ["file:read"]},
    ).status_code == 400
