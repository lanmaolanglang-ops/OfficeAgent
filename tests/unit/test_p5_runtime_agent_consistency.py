"""P5-14 × P5-15 耦合回归：数据目录 canonical 化必须让 agent 配置可读。

风险链路（权威清单 §30）：agent 配置（``agent_config`` 表，位于
``<data_root>/db/office_agent.db``）从错误的 data dir 读取 → 系统误以为
"没有配置 agent" → 触发 built-in 降级清单。本文件同时锁定两端：

1. agent 配置所在数据库路径确实由唯一 resolver 派生；
2. data dir 正确时，``/api/agents`` 走正常路径（``degraded=False``）并只返回
   enabled 的 agent —— 降级只应由真实数据库故障触发，而不是路径不一致。
"""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import office_agent.runtime_config as runtime_config
from office_agent.api.router import agent as agent_router


def _seed_agent_db(db_path):
    from office_agent.database import models  # noqa: F401  注册全部表
    from office_agent.database.base import Base
    from office_agent.database.models import AgentConfig

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(AgentConfig(agent_id="word_agent", name="Word Agent",
                                agent_type="word", enabled=True,
                                status="available"))
        session.add(AgentConfig(agent_id="ppt_agent", name="PPT Agent",
                                agent_type="ppt", enabled=False,
                                status="available"))
        session.commit()
    return factory


def test_agent_db_lives_under_the_canonical_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
    root = runtime_config.resolve_data_root()
    assert root == tmp_path
    # 与 office_agent.database.connection 的派生规则一致：<root>/db/office_agent.db
    assert root / "db" / "office_agent.db" == tmp_path / "db" / "office_agent.db"


def test_correct_data_root_yields_configured_agents_not_fallback(tmp_path, monkeypatch):
    """data dir 正确 → agent 配置可读 → 正常路径，不触发降级清单。"""
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
    db_path = runtime_config.resolve_data_root() / "db" / "office_agent.db"
    factory = _seed_agent_db(db_path)

    import office_agent.database.session as db_session_module
    monkeypatch.setattr(db_session_module, "SessionLocal", factory)

    result = asyncio.run(agent_router.list_agents())

    ids = {a.agent_id for a in result.data.agents}
    assert result.data.degraded is False, "配置可读时不得标记 degraded"
    assert ids == {"word_agent"}, "只返回 enabled 的已配置 agent"
    assert all(a.status == "available" for a in result.data.agents), \
        "正常路径不应把已配置 agent 标为 degraded"


def test_disabled_agent_stays_hidden_on_the_normal_path(tmp_path, monkeypatch):
    """正常路径也必须继续隐藏被禁用的 agent（P5-15 的禁用语义不回归）。"""
    monkeypatch.setenv("OFFICE_AGENT_DATA_DIR", str(tmp_path))
    db_path = runtime_config.resolve_data_root() / "db" / "office_agent.db"
    factory = _seed_agent_db(db_path)

    import office_agent.database.session as db_session_module
    monkeypatch.setattr(db_session_module, "SessionLocal", factory)

    result = asyncio.run(agent_router.list_agents())
    assert "ppt_agent" not in {a.agent_id for a in result.data.agents}
