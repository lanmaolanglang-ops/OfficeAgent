"""
线程租约（Thread Lease）

worker 线程在执行任务期间可能进入长时间、可中断的等待（典型场景是
failover 的正常重试退避）。同步等待期间线程无法归还线程池，排队任务
只能干等。

租约机制让执行器在**不改变的同步语义**下释放并发额度：等待方调用
``lease.park(delay, cancel_event)`` 时，执行器临时把准入上限提高 1，
排队任务立即获得线程；等待结束（或被取消事件唤醒）后额度自然回落。

本模块只保存"当前线程携带的租约"这一线程本地状态，不依赖任何业务
模块，供 task_queue（生产方）与 model_gateway（消费方）共用，避免
两层之间产生 import 环。
"""
import threading

_local = threading.local()


def current_lease():
    """返回当前线程携带的租约对象；非执行器线程返回 None。"""
    return getattr(_local, "lease", None)


def set_lease(lease):
    """由执行器工作线程在任务循环开始时安装租约。"""
    _local.lease = lease


def clear_lease():
    """执行器工作线程退出前清除租约。"""
    _local.lease = None
