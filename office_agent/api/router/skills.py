"""CRUD and Markdown import API for instruction-only Agent Skills."""
from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..core.config import settings

router = APIRouter(prefix="/api/skills", tags=["Skills"])
VALID_AGENTS = {"word", "excel", "ppt", "chat", "all"}
MAX_INSTRUCTIONS_CHARS = 24000


class SkillPayload(BaseModel):
    name: str
    description: str = ""
    instructions: str
    target_agents: list[str] = Field(default_factory=lambda: ["all"])
    enabled: bool = True
    priority: int = 100


def _owner(request: Request) -> str | None:
    if not settings.auth_enabled:
        return None
    user_id = getattr(request.state, "user_id", None)
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="缺少已认证用户")
    return str(user_id)


def _validate(payload: SkillPayload) -> dict:
    name = payload.name.strip()
    description = payload.description.strip()
    instructions = payload.instructions.strip()
    targets = list(dict.fromkeys(str(value).strip().lower() for value in payload.target_agents))
    if not name or len(name) > 128:
        raise HTTPException(status_code=400, detail="Skill 名称不能为空且不能超过 128 个字符")
    if len(description) > 2000:
        raise HTTPException(status_code=400, detail="Skill 描述不能超过 2000 个字符")
    if not instructions or len(instructions) > MAX_INSTRUCTIONS_CHARS:
        raise HTTPException(status_code=400, detail="Instructions 不能为空且不能超过 24000 个字符")
    if not targets or any(target not in VALID_AGENTS for target in targets):
        raise HTTPException(status_code=400, detail="target_agents 仅支持 word/excel/ppt/chat/all")
    if not 0 <= payload.priority <= 1000:
        raise HTTPException(status_code=400, detail="priority 必须在 0 到 1000 之间")
    return {
        "name": name, "description": description, "prompt": instructions,
        "target_agents": json.dumps(targets, ensure_ascii=False),
        "enabled": payload.enabled, "priority": payload.priority,
    }


def _serialize(skill) -> dict:
    try:
        targets = json.loads(skill.target_agents or "[]")
    except (TypeError, ValueError):
        targets = []
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description or "",
        "instructions": skill.prompt or "",
        "target_agents": targets if isinstance(targets, list) else [],
        "enabled": bool(skill.enabled),
        "priority": int(skill.priority or 100),
        "source": skill.source or "ui",
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


@router.get("")
def list_skills(request: Request):
    from ...database.repository import SkillRepository
    from ...database.session import session_scope

    with session_scope() as session:
        return {"skills": [_serialize(skill) for skill in SkillRepository(session).list_for_owner(_owner(request))]}


@router.post("")
def create_skill(payload: SkillPayload, request: Request):
    from ...database.repository import SkillRepository
    from ...database.session import session_scope

    values = _validate(payload)
    owner_id = _owner(request)
    with session_scope() as session:
        repo = SkillRepository(session)
        if any(skill.name.casefold() == values["name"].casefold() for skill in repo.list_for_owner(owner_id)):
            raise HTTPException(status_code=409, detail="同名 Skill 已存在")
        skill = repo.create_skill(
            name=values["name"], description=values["description"],
            prompt=values["prompt"], target_agents=values["target_agents"],
            priority=values["priority"], source="ui", owner_id=owner_id,
        )
        skill.enabled = values["enabled"]
        session.flush()
        return _serialize(skill)


@router.put("/{skill_id}")
def update_skill(skill_id: str, payload: SkillPayload, request: Request):
    from ...database.repository import SkillRepository
    from ...database.session import session_scope

    values = _validate(payload)
    owner_id = _owner(request)
    with session_scope() as session:
        repo = SkillRepository(session)
        skill = repo.get_owned(skill_id, owner_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill 不存在")
        if any(item.id != skill_id and item.name.casefold() == values["name"].casefold()
               for item in repo.list_for_owner(owner_id)):
            raise HTTPException(status_code=409, detail="同名 Skill 已存在")
        for key, value in values.items():
            setattr(skill, key, value)
        session.flush()
        return _serialize(skill)


@router.delete("/{skill_id}")
def delete_skill(skill_id: str, request: Request):
    from ...database.repository import SkillRepository
    from ...database.session import session_scope

    with session_scope() as session:
        repo = SkillRepository(session)
        skill = repo.get_owned(skill_id, _owner(request))
        if not skill:
            raise HTTPException(status_code=404, detail="Skill 不存在")
        repo.delete(skill.id)
        return {"deleted": True, "id": skill_id}


@router.post("/import")
async def import_skill(request: Request, file: UploadFile = File(...)):
    from ...database.repository import SkillRepository
    from ...database.session import session_scope
    from ...skills.markdown import MAX_SKILL_FILE_BYTES, parse_skill_markdown

    data = await file.read(MAX_SKILL_FILE_BYTES + 1)
    try:
        parsed = parse_skill_markdown(data, file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = SkillPayload(**{key: value for key, value in parsed.items() if key != "source"})
    values = _validate(payload)
    owner_id = _owner(request)
    with session_scope() as session:
        repo = SkillRepository(session)
        if any(skill.name.casefold() == values["name"].casefold() for skill in repo.list_for_owner(owner_id)):
            raise HTTPException(status_code=409, detail="同名 Skill 已存在")
        skill = repo.create_skill(
            name=values["name"], description=values["description"],
            prompt=values["prompt"], target_agents=values["target_agents"],
            priority=values["priority"], source="import", owner_id=owner_id,
        )
        return _serialize(skill)
