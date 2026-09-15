"""Strict, dependency-free parser for OfficeAgent Markdown Skills."""
from __future__ import annotations

MAX_SKILL_FILE_BYTES = 256 * 1024


def parse_skill_markdown(content: bytes, filename: str) -> dict:
    if not str(filename or "").lower().endswith(".md"):
        raise ValueError("Skill 导入仅支持 .md 文件")
    if len(content) > MAX_SKILL_FILE_BYTES:
        raise ValueError("Skill 文件不能超过 256 KB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Skill 文件必须是 UTF-8 文本，不能是二进制文件") from exc
    if "\x00" in text:
        raise ValueError("Skill 文件包含二进制内容")
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ValueError("Skill 文件缺少 YAML frontmatter")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise ValueError("Skill frontmatter 未闭合")
    raw_meta = normalized[4:end]
    instructions = normalized[end + 5:].strip()
    if not instructions:
        raise ValueError("Skill Instructions 不能为空")

    meta: dict[str, object] = {}
    active_list: str | None = None
    for line_number, raw_line in enumerate(raw_meta.splitlines(), start=2):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if stripped.startswith("-"):
            if active_list != "agents":
                raise ValueError(f"frontmatter 第 {line_number} 行的列表位置无效")
            value = stripped[1:].strip().strip("'\"")
            casted = meta.setdefault("agents", [])
            assert isinstance(casted, list)
            casted.append(value)
            continue
        if ":" not in raw_line:
            raise ValueError(f"frontmatter 第 {line_number} 行格式无效")
        key, value = raw_line.split(":", 1)
        key = key.strip().lower()
        if key not in {"name", "description", "agents", "priority"}:
            # Compatible extensions may add metadata; ignoring unknown scalar
            # fields avoids coupling to another product's format.
            active_list = None
            continue
        value = value.strip()
        if key == "agents":
            active_list = "agents"
            if not value:
                meta[key] = []
            elif value.startswith("[") and value.endswith("]"):
                meta[key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
            else:
                raise ValueError("agents 必须使用 YAML 列表或 [ppt, all] 格式")
        elif key == "priority":
            active_list = None
            try:
                meta[key] = int(value)
            except ValueError as exc:
                raise ValueError("priority 必须是整数") from exc
        else:
            active_list = None
            meta[key] = value.strip("'\"")
    raw_priority = meta.get("priority", 100)
    priority = raw_priority if isinstance(raw_priority, int) else 100
    return {
        "name": str(meta.get("name") or "").strip(),
        "description": str(meta.get("description") or "").strip(),
        "target_agents": meta.get("agents") or ["all"],
        "priority": priority,
        "instructions": instructions,
        "source": "import",
    }
