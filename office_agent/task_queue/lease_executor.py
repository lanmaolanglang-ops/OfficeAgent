"""
租约线程池（LeaseThreadPool）

任务执行器，Future/shutdown 语义对齐 ThreadPoolExecutor 中 Worker 用到
的子集，并增加“停靠”（park）能力：

- 准入规则：``active < base + parked``。base 为配置的并发数；工作线程
  调用 :meth:`park` 进入可中断等待（如 failover 的正常重试退避）时
  parked +1，排队任务立即获得准入。
- 替补线程：所有 base 线程都在停靠/运行且仍有排队任务时，临时补一名
  替补线程（上限 base 名，即总线程数瞬时不超 2×base）；额度回落且
  队列排空后，替补线程自行退役，线程数恒回落到 base。
- 取消、重试、冷却、备用模型等 failover 语义完全不变——park 只是在
  等待期间让出“并发额度”，等待仍在原线程上发生，唤醒后原线程继续
  后续重试；取消事件会立即唤醒停靠线程。
"""
import logging
import threading
import time
from collections import deque
from concurrent.futures import Future

from .. import thread_lease

logger = logging.getLogger("office_agent.lease_executor")

# shutdown 等待的模块级默认上限：卡住的工作线程不允许把应用退出流程
# 永久挂起。可通过构造参数 shutdown_timeout 覆盖。
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 30.0


class LeaseThreadPool:
    """支持停靠让出并发额度的执行器（base 常驻 + 有界替补）。"""

    def __init__(self, max_workers: int, thread_name_prefix: str = "lease",
                 shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS):
        self._base = max(1, int(max_workers))
        self._parked = 0
        self._active = 0
        self._live = 0  # 存活线程数（常驻 + 替补）
        self._queue: deque = deque()
        self._cond = threading.Condition()
        self._shutdown = False
        self._threads: list = []
        self._name_prefix = thread_name_prefix
        self._shutdown_timeout = max(0.0, float(shutdown_timeout))
        for _ in range(self._base):
            self._spawn_locked()

    # ------------------------------------------------------------
    # ThreadPoolExecutor 兼容子集
    # ------------------------------------------------------------

    def submit(self, fn, *args, **kwargs) -> Future:
        """提交任务；关闭后拒绝新任务（与 ThreadPoolExecutor 一致）。"""
        with self._cond:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future = Future()
            self._queue.append((future, fn, args, kwargs))
            # 存活线程少于准入上限（例如全部在停靠）：补一名替补线程，
            # 新提交的任务无需等待停靠线程唤醒。
            if self._live < self._base + self._parked:
                self._spawn_locked()
            self._cond.notify_all()
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False,
                 timeout: float | None = None) -> dict:
        """停止接收新任务，并在有限时间内等待工作线程退出。

        语义：
        1. 置停止标志并唤醒全部工作线程（此后 submit 一律拒绝）；
        2. ``cancel_futures=True`` 时丢弃尚未开始的任务并取消其 Future
           （对齐 ThreadPoolExecutor.shutdown 的同名语义——运行中的任务
           不受影响）；
        3. ``wait=True`` 时最多等待 ``timeout`` 秒（None 用模块默认），
           到期后放弃等待并返回可诊断状态，**永不无限阻塞**调用方的
           退出流程（工作线程均为 daemon，进程可以照常退出）。

        返回 ``{"timed_out": bool, "alive_threads": [线程名],
        "queued": 仍排队数}``；``timed_out=True`` 时记录 warning 日志。
        """
        with self._cond:
            self._shutdown = True
            if cancel_futures:
                while self._queue:
                    future, *_rest = self._queue.popleft()
                    future.cancel()
            self._cond.notify_all()
        if not wait:
            with self._cond:
                return {"timed_out": False, "alive_threads": [],
                        "queued": len(self._queue)}

        effective_timeout = (self._shutdown_timeout
                             if timeout is None else max(0.0, float(timeout)))
        deadline = time.monotonic() + effective_timeout
        while True:
            with self._cond:
                threads = list(self._threads)
                queued = len(self._queue)
            alive = [thread for thread in threads if thread.is_alive()]
            if not alive:
                return {"timed_out": False, "alive_threads": [], "queued": queued}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                names = [thread.name for thread in alive]
                logger.warning(
                    "LeaseThreadPool 关闭等待超时（%.1fs）：卡住线程 %s，"
                    "仍有 %d 个任务排队", effective_timeout, names, queued,
                )
                return {"timed_out": True, "alive_threads": names,
                        "queued": queued}
            # 分片 join：让其它线程先退出，也保证整体不超过 deadline
            alive[0].join(timeout=min(0.1, remaining))

    # ------------------------------------------------------------
    # 停靠
    # ------------------------------------------------------------

    def park(self, delay: float, cancel_event=None) -> bool:
        """当前工作线程进入可中断等待，并让出一个并发额度。

        返回 True 表示等待被 cancel_event 打断（调用方按取消处理）。
        只能由本池工作线程调用（经 thread_lease 安装的租约）。
        """
        with self._cond:
            self._parked += 1
            # 有排队任务且所有存活线程都在忙（停靠/运行）：补一名替补线程，
            # 上限 2×base，避免线程数无界增长。
            if self._queue and self._live < self._base + self._parked:
                self._spawn_locked()
            self._cond.notify_all()
        try:
            if cancel_event is not None:
                return bool(cancel_event.wait(max(0.0, delay)))
            if delay > 0:
                time.sleep(delay)
            return False
        finally:
            with self._cond:
                self._parked -= 1
                # 唤醒等待中的线程重新评估准入/退役
                self._cond.notify_all()

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _spawn_locked(self):
        """在 self._cond 持锁状态下启动一名线程。"""
        thread = threading.Thread(
            target=self._worker_loop,
            name=f"{self._name_prefix}-{len(self._threads)}",
            daemon=True,
        )
        self._threads.append(thread)
        self._live += 1
        thread.start()

    def _worker_loop(self):
        thread_lease.set_lease(self)
        try:
            while True:
                with self._cond:
                    while True:
                        if self._shutdown and not self._queue:
                            return
                        if self._queue and self._active < self._base + self._parked:
                            future, fn, args, kwargs = self._queue.popleft()
                            if not future.set_running_or_notify_cancel():
                                # 排队期间被取消：跳过，继续取下一个
                                continue
                            self._active += 1
                            break
                        if (not self._queue
                                and self._live > self._base + self._parked):
                            # 替补线程：额度回落且队列已排空，退役
                            return
                        self._cond.wait()
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - 与 stdlib 一致
                    future.set_exception(exc)
                else:
                    future.set_result(result)
                finally:
                    with self._cond:
                        self._active -= 1
                        self._cond.notify_all()
        finally:
            with self._cond:
                self._live -= 1
            thread_lease.clear_lease()
