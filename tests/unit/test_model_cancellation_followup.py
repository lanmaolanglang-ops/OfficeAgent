from types import SimpleNamespace
import threading
import time

import pytest


@pytest.mark.parametrize(
    ("module_name", "helper_name"),
    [
        ("office_agent.task_queue.tasks.excel_tasks", "_understand_excel_request"),
        ("office_agent.task_queue.tasks.ppt_tasks", "_understand_ppt_request"),
    ],
)
def test_office_request_rewriters_pass_worker_cancellation(
        monkeypatch, module_name, helper_name):
    module = __import__(module_name, fromlist=[helper_name])
    model_gateway = __import__("office_agent.model_gateway", fromlist=["ModelGateway"])
    cancel_event = threading.Event()
    observed = []

    class Gateway:
        def __init__(self, cancel_event=None):
            observed.append(cancel_event)

        def chat(self, **_kwargs):
            return SimpleNamespace(
                success=True, content="normalized", model_used="test",
                provider="test", error="", raw_response={},
            )

    monkeypatch.setattr(model_gateway, "ModelGateway", Gateway)
    progress = SimpleNamespace(cancel_event=cancel_event)

    result = getattr(module, helper_name)("request", {}, progress)

    assert result == "normalized"
    assert observed == [cancel_event]


def test_model_gateway_passes_cancellation_to_failover():
    from office_agent.model_gateway.gateway import ModelGateway
    from office_agent.models.model_schemas import AITaskType, ModelResponse

    cancel_event = threading.Event()
    observed = {}
    gateway = ModelGateway.__new__(ModelGateway)
    gateway.cancel_event = cancel_event
    gateway.last_call = None
    gateway.manager = SimpleNamespace(get_default_model_id=lambda: None)
    gateway.router = SimpleNamespace(
        select_model=lambda *_args, **_kwargs: ["model"]
    )

    class Failover:
        def execute_with_failover(self, **kwargs):
            observed.update(kwargs)
            return ModelResponse(
                success=False, error="任务已取消",
                raw_response={"_office_agent": {"cancelled": True, "attempts": 0}},
            )

    gateway.failover = Failover()

    response = gateway.chat(
        user_message="test", task_type=AITaskType.SIMPLE_TEXT
    )

    assert response.error == "任务已取消"
    assert observed["cancel_event"] is cancel_event
    assert gateway.last_call["cancelled"] is True


def test_worker_cancel_interrupts_a_long_model_retry_wait():
    from office_agent.model_gateway.failover import FailoverManager
    from office_agent.models.model_schemas import AITaskType, ModelResponse
    from office_agent.task_queue.worker import LocalWorker

    started = threading.Event()
    finished = threading.Event()

    class Manager:
        def get_routing(self, _task_type):
            return ["model"]

        def get_model(self, _model_id):
            return SimpleNamespace(enabled=True, api_key="key")

        def get_client(self, _model_id):
            return object()

    def cancellable_task(progress=None, **_kwargs):
        try:
            failover = FailoverManager(Manager(), max_retries=3, retry_delay=30)

            def fail_once(_client):
                started.set()
                return ModelResponse(success=False, error="timeout")

            failover.execute_with_failover(
                AITaskType.SIMPLE_TEXT,
                fail_once,
                cancel_event=progress.cancel_event,
            )
            progress.check_cancelled()
            return {"status": "success"}
        finally:
            finished.set()

    worker = LocalWorker()
    worker._session_factory = None
    worker._update_status = lambda *_args, **_kwargs: None
    worker.register("test.cancel-model", cancellable_task)
    try:
        task_id = worker.submit("test.cancel-model")
        assert started.wait(2)
        cancel_started = time.monotonic()
        worker.revoke(task_id)
        assert finished.wait(2)
        assert time.monotonic() - cancel_started < 1
    finally:
        worker.shutdown()
