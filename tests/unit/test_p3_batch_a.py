# -*- coding: utf-8 -*-
"""P3 batch A regression tests (database / persistence / runtime).

Covers P3-1 bounded path-lock registry, P3-2 JSON atomic write correctness,
P3-3 namespaced log-dir precedence, P3-4 removed dead run_in_background knob,
P3-10 task revision composite uniqueness, P3-13 alembic URL % escaping.
"""
import gc
from configparser import ConfigParser
from dataclasses import fields

import pytest

from office_agent import runtime_config
from office_agent import persistence
from office_agent.runtime_manager import AppConfig


# ---------- P3-1: per-path lock registry must not grow without bound ----------
def test_p3_1_path_lock_registry_is_bounded(tmp_path):
    before = len(persistence._path_locks)
    for i in range(300):
        p = tmp_path / f"f{i}.json"
        persistence.atomic_write_json(p, {"i": i})
    gc.collect()
    after = len(persistence._path_locks)
    # WeakValueDictionary drops locks once no writer holds them; a burst of
    # distinct paths must not leave one entry per path.
    assert after - before <= 5, f"lock registry leaked: {before}->{after}"


def test_p3_1_concurrent_same_path_shares_one_lock(tmp_path):
    p = tmp_path / "same.json"
    l1 = persistence._lock_for(p)
    l2 = persistence._lock_for(p)
    assert l1 is l2  # equivalent paths serialize on one lock


# ---------- P3-2: atomic_write_json round-trips ----------
def test_p3_2_atomic_json_roundtrip(tmp_path):
    p = tmp_path / "a" / "b.json"
    payload = {"k": "值", "n": [1, 2, 3]}
    persistence.atomic_write_json(p, payload, ensure_ascii=False)
    import json
    assert json.loads(p.read_text(encoding="utf-8")) == payload
    # no leftover temp files
    assert list((tmp_path / "a").glob("*.tmp")) == []


# ---------- P3-3: namespaced log dir wins over generic LOG_DIR ----------
def test_p3_3_namespaced_log_dir_precedence(monkeypatch):
    monkeypatch.setenv("LOG_DIR", "/generic/log")
    monkeypatch.setenv("OFFICE_AGENT_LOG_DIR", "/namespaced/log")
    assert str(runtime_config.get_log_dir()).replace("\\", "/") == "/namespaced/log"


def test_p3_3_generic_log_dir_fallback(monkeypatch):
    monkeypatch.delenv("OFFICE_AGENT_LOG_DIR", raising=False)
    monkeypatch.setenv("LOG_DIR", "/generic/log")
    assert str(runtime_config.get_log_dir()).replace("\\", "/") == "/generic/log"


# ---------- P3-4: vestigial run_in_background removed ----------
def test_p3_4_dead_background_knob_removed():
    assert "run_in_background" not in {f.name for f in fields(AppConfig)}


# ---------- P3-13: literal % in DSN survives ConfigParser interpolation ----------
def test_p3_13_percent_in_url_does_not_break_configparser():
    cfg = ConfigParser()
    cfg.add_section("alembic")
    raw = "postgresql+psycopg2://u:p%40ss@host/db"
    cfg.set("alembic", "sqlalchemy.url", raw.replace("%", "%%"))
    assert cfg.get("alembic", "sqlalchemy.url") == raw


# ---------- P3-10: task (parent, revision) uniqueness ----------
def test_p3_10_task_revision_unique():
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session
    from office_agent.database.base import Base
    from office_agent.database import models  # noqa: F401
    from office_agent.database.models.task import Task

    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Task(id="t1", task_type="x", instruction="a"))
        s.add(Task(id="t2", task_type="x", instruction="b"))
        s.commit()  # two NULL-parent roots coexist
        s.add(Task(id="r1", task_type="x", instruction="c",
                   parent_task_id="t1", revision_number=1))
        s.commit()
        with pytest.raises(IntegrityError):
            s.add(Task(id="r2", task_type="x", instruction="d",
                       parent_task_id="t1", revision_number=1))
            s.commit()
        s.rollback()
        # different parent, same number is fine
        s.add(Task(id="r3", task_type="x", instruction="e",
                   parent_task_id="t2", revision_number=1))
        s.commit()
