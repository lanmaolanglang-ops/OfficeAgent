"""P2-17：任务步骤终态闭合 + file_manager 死代码清理回归。

修复前（task_manager.py）：
- ``update(step=...)`` 每次只**追加**新的 running 步骤，从不闭合前一步；
  ``complete()``/``fail()`` 又只处理最后一步——任务到达终态后，中间
  步骤永久悬挂在 running；
- ``cancel_memory_task`` 只改任务状态，步骤完全不动。
- file_manager 的 ``TYPE_MAP`` 全树零引用（生产/测试/导出/动态调用
  均无），属确死代码。

终态闭合规则：任务成功 → 所有步骤 completed；任务失败 → 最后一个
未终态步骤 failed、其余悬挂步骤 skipped；任务取消 → 未终态步骤
cancelled；已终态的步骤在任何闭合中不被覆盖，重复闭合幂等。
"""
from office_agent.api.core.task_manager import Task, TaskManager


def _task_with_steps(*step_names):
    task = Task("word_format", "demo")
    task.start()
    for name in step_names:
        task.update(step=name)
    return task


class TestStepTransitionClosure:
    def test_step_switch_closes_previous_step(self):
        """步骤切换即闭合上一步（旧实现前一步永久 running）。"""
        task = _task_with_steps("解析文档", "生成向量", "写入索引")
        statuses = [step["status"] for step in task.steps]
        assert statuses == ["completed", "completed", "running"]
        assert task.steps[0]["completed_at"] is not None

    def test_success_closes_every_step(self):
        task = _task_with_steps("解析文档", "生成向量", "写入索引")
        task.complete({"ok": True})
        assert [step["status"] for step in task.steps] == \
            ["completed", "completed", "completed"]
        assert all(step["completed_at"] for step in task.steps)

    def test_failure_marks_last_failed_and_never_leaves_running(self):
        task = _task_with_steps("解析文档", "生成向量")
        task.fail("engine error")
        statuses = [step["status"] for step in task.steps]
        assert statuses == ["completed", "failed"], statuses
        assert all(step["status"] != "running" for step in task.steps)


class TestTerminalStepNotOverwritten:
    def test_fail_after_success_keeps_completed_steps(self):
        task = _task_with_steps("解析文档", "写入索引")
        task.complete({"ok": True})
        before = [dict(step) for step in task.steps]
        task.fail("late error")
        assert [dict(step) for step in task.steps] == before, \
            "已终态的步骤不得被后续闭合覆盖"

    def test_repeated_finalize_is_idempotent(self):
        task = _task_with_steps("解析文档", "生成向量")
        task.complete({"ok": True})
        snapshot = [dict(step) for step in task.steps]
        task.complete({"ok": True})
        task.complete({"ok": True})
        assert [dict(step) for step in task.steps] == snapshot

    def test_single_step_failure_keeps_existing_contract(self):
        """与既有单步契约一致：最后一步 failed（test_task_api_lifecycle）。"""
        task = _task_with_steps("plan")
        task.fail("model unavailable")
        assert task.steps[-1]["status"] == "failed"
        assert task.steps[-1]["completed_at"] is not None


class TestCancelClosesSteps:
    def test_cancel_memory_task_closes_running_steps(self):
        manager = TaskManager()
        task = _task_with_steps("解析文档", "生成向量")
        manager.tasks[task.task_id] = task

        assert manager.cancel_memory_task(task.task_id) is True

        assert task.status == "cancelled"
        assert [step["status"] for step in task.steps] == \
            ["completed", "cancelled"], \
            "任务取消后不允许任何步骤停留在 running"

    def test_cancel_idempotent_on_steps(self):
        manager = TaskManager()
        task = _task_with_steps("解析文档")
        manager.tasks[task.task_id] = task
        manager.cancel_memory_task(task.task_id)
        snapshot = [dict(step) for step in task.steps]
        manager.cancel_memory_task(task.task_id)
        assert [dict(step) for step in task.steps] == snapshot

    def test_to_dict_steps_match_object(self):
        task = _task_with_steps("解析文档", "生成向量")
        task.complete({})
        assert task.to_dict()["steps"] == task.steps


class TestFileManagerDeadCode:
    def test_type_map_is_removed(self):
        """确死代码：TYPE_MAP 全树零引用，不得再保留第二套类型来源。"""
        import importlib

        module = importlib.import_module("office_agent.api.core.file_manager")
        # 包 __init__ 的 file_manager 实例会遮蔽子模块属性名，
        # 必须经 sys.modules 取真实模块对象。
        assert not hasattr(module.FileManager, "TYPE_MAP")

    def test_normal_read_write_still_works(self, tmp_path):
        """删除死代码后主路径（注册/持久化/重载/注销）不受影响。"""
        import json

        from office_agent.api.core.file_manager import FileInfo, FileManager

        upload = tmp_path / "uploads"
        output = tmp_path / "outputs"
        upload.mkdir()
        stored = upload / "file_1.txt"
        stored.write_text("hello", encoding="utf-8")

        manager = FileManager(str(upload), str(output))
        manager.register(FileInfo("file_1", "hello.txt", str(stored),
                                  "text", ".txt", stored.stat().st_size))
        reloaded = FileManager(str(upload), str(output))
        assert reloaded.files["file_1"].original_name == "hello.txt"
        assert reloaded.unregister("file_1") is True
        assert FileManager(str(upload), str(output)).files == {}
        assert json.loads(
            (upload / "file_metadata.json").read_text(encoding="utf-8")) == []
