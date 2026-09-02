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
