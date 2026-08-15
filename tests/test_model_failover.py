from office_agent.model_gateway.failover import FailoverManager
from office_agent.models.model_schemas import AITaskType, ModelResponse


class _Config:
    def __init__(self, model_id):
        self.id = model_id
        self.enabled = True
        self.api_key = "key"


class _Client:
    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    def next(self):
        self.calls += 1
        return self.responses[min(self.calls - 1, len(self.responses) - 1)]


class _Manager:
    def __init__(self):
        self.clients = {
            "primary": _Client([ModelResponse(False, error="temporary")] * 3),
            "backup": _Client([ModelResponse(True, content="ok", model_used="backup")]),
        }

    def get_routing(self, _task_type):
        return ["primary", "backup"]

    def get_model(self, model_id):
        return _Config(model_id) if model_id in self.clients else None

    def get_client(self, model_id):
        client = self.clients[model_id]
        return client


def test_failover_retries_before_switching_model():
    manager = _Manager()
    gateway = FailoverManager(manager, max_retries=3, retry_delay=0)
    result = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT,
        lambda client: client.next(),
    )
    assert result.success is True
    assert manager.clients["primary"].calls == 3
    assert manager.clients["backup"].calls == 1
