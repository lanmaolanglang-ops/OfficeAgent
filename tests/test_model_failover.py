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
    assert result.raw_response["_office_agent"]["fallback_used"] is True
    assert result.raw_response["_office_agent"]["attempts"] == 4


def test_failover_does_not_retry_authentication_errors():
    manager = _Manager()
    manager.clients["primary"] = _Client([
        ModelResponse(False, error="HTTP 401: Authentication failed")
    ])
    gateway = FailoverManager(manager, max_retries=3, retry_delay=0)

    result = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT,
        lambda client: client.next(),
    )

    assert result.success is True
    assert manager.clients["primary"].calls == 1
    assert manager.clients["backup"].calls == 1


def test_each_failed_attempt_counts_toward_cooldown():
    manager = _Manager()
    manager.clients = {
        "primary": _Client([ModelResponse(False, error="timeout")] * 3),
    }
    gateway = FailoverManager(manager, max_retries=3, retry_delay=0)
    result = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT, lambda client: client.next(),
    )
    assert not result.success
    assert gateway._is_in_cooldown("primary")
    history = list(gateway._failure_history["primary"])
    second = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT, lambda client: client.next(),
    )
    assert "冷却" in second.error
    assert gateway._failure_history["primary"] == history


def test_permanent_exception_is_not_retried():
    manager = _Manager()
    calls = 0
    def action(_client):
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP 400 invalid_request")
    gateway = FailoverManager(manager, max_retries=3, retry_delay=0)
    gateway.execute_with_failover(AITaskType.SIMPLE_TEXT, action, model_ids=["primary"])
    assert calls == 1
