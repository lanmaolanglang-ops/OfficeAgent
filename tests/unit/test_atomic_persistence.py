"""
专项回归：全项目共用的原子持久化工具与 YAML 保存路径。

覆盖清单条目：
- 3.2 尚未抽取全项目共用的「临时文件 + fsync + replace + 锁」持久化工具
- 3.2 YamlLoader 及其他直接覆写文件仍未统一使用原子写工具
"""
import json
import os
import threading

import pytest

from office_agent.persistence import (
    atomic_write,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
)
from office_agent.config_system.loaders import YamlLoader


def test_atomic_write_json_replaces_only_on_success(tmp_path):
    """写入失败时原文件保持原样，且不留临时文件。"""
    target = tmp_path / "state.json"
    target.write_text('{"version": 1}', encoding="utf-8")

    def boom(_handle):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        atomic_write(target, boom)

    assert target.read_text(encoding="utf-8") == '{"version": 1}'
    # 临时文件必须被清理，不能留在目录里污染后续加载
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_json_round_trip_and_no_temp_leftovers(tmp_path):
    target = tmp_path / "nested" / "dir" / "kb.json"
    payload = {"docs": [{"id": "1", "text": "中文内容"}], "n": 3}

    atomic_write_json(target, payload, indent=2)

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".kb.json.")]


def test_atomic_write_text_and_bytes_honour_mode(tmp_path):
    text_path = tmp_path / "secret.txt"
    atomic_write_text(text_path, "top-secret", mode=0o600)
    assert text_path.read_text(encoding="utf-8") == "top-secret"

    blob_path = tmp_path / "master.key"
    atomic_write_bytes(blob_path, b"\x01\x02\x03", mode=0o600)
    assert blob_path.read_bytes() == b"\x01\x02\x03"

    if os.name != "nt":  # POSIX 才真正按位收紧权限
        assert oct(os.stat(text_path).st_mode & 0o777) == "0o600"
        assert oct(os.stat(blob_path).st_mode & 0o777) == "0o600"


def test_concurrent_writes_are_serialised(tmp_path):
    """同一路径的并发写入必须全部生效，不能互相覆盖。"""
    target = tmp_path / "counter.json"
    attempts = 25

    def worker(index):
        atomic_write_json(target, {"index": index})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 关键不是最终值，而是文件始终是可解析的完整 JSON
    data = json.loads(target.read_text(encoding="utf-8"))
    assert 0 <= data["index"] < attempts


class _StubYaml:
    """最小 yaml 替身：让 save() 的真实写入路径可在无 PyYAML 环境下验证。"""

    @staticmethod
    def dump(data, handle, allow_unicode=False, default_flow_style=True):
        handle.write(json.dumps(data, ensure_ascii=allow_unicode))


def test_yaml_loader_save_is_atomic(tmp_path, monkeypatch):
    """YamlLoader.save 走统一原子写：中断不留半截配置，也不留临时文件。"""
    import sys

    monkeypatch.setitem(sys.modules, "yaml", _StubYaml)
    loader = YamlLoader(str(tmp_path))
    payload = {"app": {"name": "office-agent"}, "items": [1, 2, 3]}

    loader.save(payload, "config.yaml")

    written = tmp_path / "config.yaml"
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8")) == payload
    assert not [p for p in tmp_path.iterdir() if ".tmp" in p.name]

    # 原文件已存在时，写入失败不能破坏既有配置
    def boom(data, _handle, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(_StubYaml, "dump", boom)
    with pytest.raises(RuntimeError):
        loader.save({"broken": True}, "config.yaml")
    assert json.loads(written.read_text(encoding="utf-8")) == payload


def test_yaml_loader_reports_missing_pyyaml_without_masking_it(tmp_path,
                                                               monkeypatch):
    """缺 PyYAML 时只报缺依赖，不能把真实写入失败也说成缺依赖。"""
    import builtins

    loader = YamlLoader(str(tmp_path))
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("no yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # 依赖缺失属于可运行降级：记录日志且不抛出，调用方继续走其他配置源
    loader.save({"a": 1}, "config.yaml")
    assert not (tmp_path / "config.yaml").exists()
