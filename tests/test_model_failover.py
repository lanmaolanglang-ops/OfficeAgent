from office_agent.model_gateway.failover import FailoverManager
from office_agent.models.model_schemas import AITaskType, ModelResponse
import threading


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


def test_cancellation_interrupts_retry_wait_without_trying_backup():
    manager = _Manager()

    class CancelDuringWait:
        def __init__(self):
            self.cancelled = False
            self.waited = []

        def is_set(self):
            return self.cancelled

        def wait(self, timeout):
            self.waited.append(timeout)
            self.cancelled = True
            return True

    cancel_event = CancelDuringWait()
    gateway = FailoverManager(manager, max_retries=3, retry_delay=5)
    result = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT,
        lambda client: client.next(),
        cancel_event=cancel_event,
    )

    assert result.error == "任务已取消"
    assert result.raw_response["_office_agent"]["cancelled"] is True
    assert result.raw_response["_office_agent"]["attempts"] == 1
    assert cancel_event.waited == [5]
    assert manager.clients["primary"].calls == 1
    assert manager.clients["backup"].calls == 0


def test_cancellation_before_failover_makes_no_model_call():
    manager = _Manager()
    cancel_event = threading.Event()
    cancel_event.set()
    gateway = FailoverManager(manager, max_retries=3, retry_delay=5)

    result = gateway.execute_with_failover(
        AITaskType.SIMPLE_TEXT,
        lambda client: client.next(),
        cancel_event=cancel_event,
    )

    assert result.raw_response["_office_agent"] == {
        "attempts": 0,
        "attempted_models": [],
        "fallback_used": False,
        "cancelled": True,
    }
    assert manager.clients["primary"].calls == 0
