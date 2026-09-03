import json
from types import SimpleNamespace

from office_agent.api.router.chat import _classify_follow_up, _recover_conversation_context


class FakeRepo:
    def __init__(self, tasks):
        self.tasks = tasks

    def get_recent(self, limit=20):
        return self.tasks[:limit]


class FakeStorage:
    def __init__(self, paths):
        self.paths = paths

    def get_file_path(self, file_id):
        return self.paths.get(file_id)


def test_recover_latest_output_for_follow_up(tmp_path):
    output = tmp_path / "formatted.docx"
    output.write_bytes(b"docx")
    task = SimpleNamespace(
        id="task_previous",
        agent_name="word_agent",
        task_type="word_process",
        instruction="Format the document with a clear title.",
        options_json=json.dumps({"conversation_id": "conv-1", "history": []}),
        output_file_ids=json.dumps(["file-output"]),
    )

    recovered = _recover_conversation_context(
        "conv-1", FakeRepo([task]), FakeStorage({"file-output": str(output)})
    )

    assert recovered["task"].id == "task_previous"
    assert recovered["input_paths"] == [str(output)]
    assert recovered["previous_instruction"] == task.instruction


def test_recovery_ignores_other_conversations(tmp_path):
    output = tmp_path / "formatted.docx"
    output.write_bytes(b"docx")
    task = SimpleNamespace(
        id="task-other",
        agent_name="word_agent",
        task_type="word_process",
        instruction="Other conversation task",
        options_json=json.dumps({"conversation_id": "other"}),
        output_file_ids=json.dumps(["file-output"]),
    )

    assert _recover_conversation_context(
        "conv-1", FakeRepo([task]), FakeStorage({"file-output": str(output)})
    ) is None


def test_recovery_ignores_another_users_conversation(tmp_path):
    output = tmp_path / "formatted.docx"
    output.write_bytes(b"docx")
    task = SimpleNamespace(
        id="task-other-user",
        user_id="user-2",
        agent_name="word_agent",
        task_type="word_process",
        instruction="Other user's task",
        options_json=json.dumps({"conversation_id": "conv-shared"}),
        output_file_ids=json.dumps(["file-output"]),
    )

    assert _recover_conversation_context(
        "conv-shared",
        FakeRepo([task]),
        FakeStorage({"file-output": str(output)}),
        owner_id="user-1",
    ) is None


def test_revision_mode_classification():
    assert _classify_follow_up("把标题改小一点", True) == "modify"
    assert _classify_follow_up("删除最后一页", True) == "remove"
    assert _classify_follow_up("恢复之前版本", True) == "revert"
    assert _classify_follow_up("再增加一个总结", True) == "add"
    assert _classify_follow_up("修改标题", False) == "new_task"


class PagedRepo:
    """模拟支持 offset 分页的仓储（与 TaskRepository.get_recent 签名一致）。"""

    def __init__(self, tasks):
        self.tasks = tasks

    def get_recent(self, limit=20, offset=0):
        return self.tasks[offset:offset + limit]


def _make_task(task_id, conversation_id, output_ids):
    return SimpleNamespace(
        id=task_id,
        agent_name="word_agent",
        task_type="word_process",
        instruction=f"instruction-{task_id}",
        options_json=json.dumps(
            {"conversation_id": conversation_id, "history": []}),
        output_file_ids=json.dumps(output_ids),
    )


def test_recovery_paginates_beyond_first_page(tmp_path):
    """清单 292：目标会话位于第一页之后时仍能恢复（不再只扫最近 100 条）。"""
    output = tmp_path / "formatted.docx"
    output.write_bytes(b"docx")
    target = _make_task("task-target", "conv-deep", ["file-output"])
    # 250 条无关任务把目标挤出第一页（每页 200）
    noise = [_make_task(f"task-noise-{i}", f"conv-other-{i}", [])
             for i in range(250)]
    repo = PagedRepo(noise + [target])

    recovered = _recover_conversation_context(
        "conv-deep", repo, FakeStorage({"file-output": str(output)}))

    assert recovered is not None
    assert recovered["task"].id == "task-target"
    assert recovered["input_paths"] == [str(output)]


def test_recovery_returns_all_output_artifacts(tmp_path):
    """清单 293：修订链的输入是上一任务的全部产物，而非只取第一个。"""
    out1 = tmp_path / "part1.docx"
    out2 = tmp_path / "part2.docx"
    out1.write_bytes(b"one")
    out2.write_bytes(b"two")
    task = _make_task("task-multi", "conv-multi",
                      ["file-out-1", "file-out-2"])

    recovered = _recover_conversation_context(
        "conv-multi", FakeRepo([task]),
        FakeStorage({"file-out-1": str(out1), "file-out-2": str(out2)}))

    assert recovered["input_paths"] == [str(out1), str(out2)]
