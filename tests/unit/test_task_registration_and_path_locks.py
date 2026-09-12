"""P2-16：锁 key 规范化与任务注册幂等回归。

修复前：
- ``persistence._lock_for`` 直接 ``str(path)`` 作 key：`D:\\data\\a`、
  `D:\\data\\.\\a`、`d:\\DATA\\a`、相对路径写法指向同一文件却拿到不同
  锁，进程内互斥形同虚设。
- ``LocalWorker.register`` 对重复注册一律静默覆盖；``init_worker`` 与
  ``submit_task`` 的全量注册路径每次提交都重写全部 handler。
"""
import sys
import threading

import pytest

from office_agent.persistence import _lock_for
from office_agent.task_queue.worker import LocalWorker


class TestCanonicalLockKey:
    def test_same_object_and_string_share_lock(self):
        path = r"D:\oa_p2_16_demo\data\a.json"
        assert _lock_for(path) is _lock_for(path)

    def test_dot_segments_share_lock(self):
        a = _lock_for(r"D:\oa_p2_16_demo\data\a.json")
        b = _lock_for(r"D:\oa_p2_16_demo\data\.\a.json")
        c = _lock_for(r"D:\oa_p2_16_demo\data\sub\..\a.json")
        assert a is b is c

    def test_slash_direction_share_lock(self):
        a = _lock_for("D:/oa_p2_16_demo/data/a.json")
        b = _lock_for("D:\\oa_p2_16_demo\\data\\a.json")
        assert a is b

    @pytest.mark.skipif(sys.platform != "win32",
                        reason="normcase 大小写折叠仅 Windows 语义")
    def test_case_insensitive_share_lock(self):
        a = _lock_for(r"D:\oa_p2_16_demo\DATA\a.json")
        b = _lock_for(r"d:\oa_p2_16_demo\data\A.JSON")
        assert a is b

    def test_relative_and_absolute_share_lock(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        relative = _lock_for("cfg/x.json")
        absolute = _lock_for(str(tmp_path / "cfg" / "x.json"))
        assert relative is absolute

    def test_nonexistent_path_does_not_raise(self):
        lock = _lock_for(r"D:\oa_p2_16_definitely_missing\file.json")
        assert lock is not None

    def test_different_files_keep_different_locks(self):
        a = _lock_for(r"D:\oa_p2_16_demo\data\a.json")
        b = _lock_for(r"D:\oa_p2_16_demo\data\b.json")
        assert a is not b

    def test_two_path_spellings_expose_one_mutex(self):
        """同一资源的两种拼法拿到的是同一把锁：持有期间另一拼法进不去。"""
        path_a = r"D:\oa_p2_16_demo\data\shared.json"
        path_b = r"D:\oa_p2_16_demo\data\.\shared.json"
        lock_a = _lock_for(path_a)
        lock_b = _lock_for(path_b)
        release = threading.Event()
        entered = threading.Event()

        def holder():
            with lock_a:
                entered.set()
                release.wait(timeout=5)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            assert entered.wait(timeout=5)
            assert lock_b.acquire(timeout=0.2) is False, \
                "两种拼法必须落到同一把互斥锁上"
        finally:
            release.set()
            thread.join(timeout=5)


class TestRegisterIdempotency:
    def test_same_handler_re_registration_is_noop(self):
        worker = LocalWorker()
        handler = lambda **kwargs: {"status": "success"}  # noqa: E731
        worker.register("t.a", handler)
        worker.register("t.a", handler)
        worker.register("t.a", handler)
        assert worker._tasks == {"t.a": handler}

    def test_different_handler_same_name_is_conflict(self):
        worker = LocalWorker()
        original = lambda **kwargs: {}  # noqa: E731
        worker.register("t.a", original)
        with pytest.raises(ValueError):
            worker.register("t.a", lambda **kwargs: {"other": 1})
        assert worker._tasks["t.a"] is original, "冲突时不得静默覆盖"

    def test_replace_flag_overwrites_explicitly(self):
        worker = LocalWorker()
        first = lambda **kwargs: {}  # noqa: E731
        second = lambda **kwargs: {}  # noqa: E731
        worker.register("t.a", first)
        worker.register("t.a", second, replace=True)
        assert worker._tasks["t.a"] is second

    def test_bulk_registration_loop_is_idempotent(self):
        """init_worker/submit_task 的全量注册反复执行不会翻倍或冲突。"""
        worker = LocalWorker()
        registry = {
            "t.x": lambda **kwargs: {},
            "t.y": lambda **kwargs: {},
            "t.z": lambda **kwargs: {},
        }
        for _ in range(3):
            for name, func in registry.items():
                worker.register(name, func)
        assert worker._tasks == registry

    def test_init_worker_twice_keeps_single_instance_and_handlers(self, monkeypatch):
        import office_agent.task_queue as task_queue
        import office_agent.task_queue.worker as worker_module

        handler_a = lambda **kwargs: {}  # noqa: E731
        handler_b = lambda **kwargs: {}  # noqa: E731
        monkeypatch.setattr(task_queue, "TASK_REGISTRY",
                            {"reg.a": handler_a, "reg.b": handler_b})
        monkeypatch.setattr(worker_module, "_worker_instance", None)

        first = task_queue.init_worker()
        second = task_queue.init_worker()
        assert first is second
        assert first._tasks == {"reg.a": handler_a, "reg.b": handler_b}
        assert task_queue.get_initialized_worker() is first
