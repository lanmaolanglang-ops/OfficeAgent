/**
 * P1-25：Workspace 依赖的定时轮询资源必须"注册/释放对称"，且**模块重复求值
 * （Vite HMR / 二次模块实例）后不得留下无法回收的孤儿 interval**。
 *
 * 修复前实测（旧实现，模块级 pollTimer）：
 *   - 加载第二个模块实例后同时存在 2 个 interval；
 *   - 6 秒内任务列表请求从 3 次变 6 次；
 *   - 连调 10 次 stopPolling() 仍有 1 个 interval 永久存活。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listTasks: vi.fn(async () => []),
  checkHealth: vi.fn(async () => ({ success: true, data: { status: 'healthy' } })),
  listFilesPage: vi.fn(async () => ({ files: [], total: 0 })),
  sendChatMessage: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock('../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/api')>();
  return { ...actual, ...api };
});

const KEY = 'unitTestPoller';

async function freshPoller() {
  vi.resetModules();
  return import('./refCountedPoller');
}

beforeEach(() => {
  vi.useFakeTimers();
  api.listTasks.mockClear();
  api.checkHealth.mockClear();
});

afterEach(async () => {
  const poller = await import('./refCountedPoller');
  poller.resetPolling(KEY);
  poller.resetPolling('taskStore');
  poller.resetPolling('backendStore');
  vi.useRealTimers();
});

describe('refCountedPoller 资源对称', () => {
  it('acquire 只创建一个 interval，release 后归零', async () => {
    const poller = await freshPoller();
    const baseline = vi.getTimerCount();

    poller.acquirePolling(KEY, 1000, () => {});
    expect(vi.getTimerCount()).toBe(baseline + 1);
    expect(poller.pollingActive(KEY)).toBe(true);
    expect(poller.pollingSubscribers(KEY)).toBe(1);

    poller.releasePolling(KEY);
    expect(vi.getTimerCount()).toBe(baseline);
    expect(poller.pollingActive(KEY)).toBe(false);
    expect(poller.pollingSubscribers(KEY)).toBe(0);
  });

  it('重复 mount/unmount 30 次不累积定时器', async () => {
    const poller = await freshPoller();
    const baseline = vi.getTimerCount();

    for (let i = 0; i < 30; i += 1) {
      poller.acquirePolling(KEY, 1000, () => {});
      poller.releasePolling(KEY);
    }

    expect(vi.getTimerCount()).toBe(baseline);
    expect(poller.pollingSubscribers(KEY)).toBe(0);
  });

  it('多个订阅者共享一个 interval，最后一个释放才清除', async () => {
    const poller = await freshPoller();
    const baseline = vi.getTimerCount();

    poller.acquirePolling(KEY, 1000, () => {});
    poller.acquirePolling(KEY, 1000, () => {});
    expect(vi.getTimerCount()).toBe(baseline + 1);

    poller.releasePolling(KEY);
    expect(poller.pollingActive(KEY)).toBe(true);
    expect(vi.getTimerCount()).toBe(baseline + 1);

    poller.releasePolling(KEY);
    expect(vi.getTimerCount()).toBe(baseline);
  });

  it('多余 release 不会误杀其它订阅者的定时器', async () => {
    const poller = await freshPoller();

    poller.acquirePolling(KEY, 1000, () => {});
    poller.releasePolling(KEY);
    poller.releasePolling(KEY); // 过度清理（异常路径）
    poller.acquirePolling(KEY, 1000, () => {});
    expect(poller.pollingActive(KEY)).toBe(true);
    expect(poller.pollingSubscribers(KEY)).toBe(1);

    poller.releasePolling(KEY);
    expect(poller.pollingActive(KEY)).toBe(false);
  });

  it('tick 始终调用最新订阅者的回调', async () => {
    const poller = await freshPoller();
    const first = vi.fn();
    const second = vi.fn();

    poller.acquirePolling(KEY, 1000, first);
    poller.acquirePolling(KEY, 1000, second);
    await vi.advanceTimersByTimeAsync(1000);

    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it('按下发间隔稳定触发，不因订阅数放大', async () => {
    const poller = await freshPoller();
    const tick = vi.fn();

    poller.acquirePolling(KEY, 2000, tick);
    poller.acquirePolling(KEY, 2000, tick);
    poller.acquirePolling(KEY, 2000, tick);
    await vi.advanceTimersByTimeAsync(6000);

    expect(tick).toHaveBeenCalledTimes(3);
  });
});

describe('模块重复求值（HMR）不产生孤儿 interval', () => {
  it('第二个模块实例复用同一 interval，并可被任何实例回收', async () => {
    const first = await freshPoller();
    first.acquirePolling(KEY, 1000, () => {});
    expect(vi.getTimerCount()).toBe(1);

    const second = await freshPoller();
    second.acquirePolling(KEY, 1000, () => {});
    // 旧实现此处会变成 2（新模块的句柄为 null，旧 interval 无法回收）
    expect(vi.getTimerCount()).toBe(1);

    second.releasePolling(KEY);
    expect(vi.getTimerCount()).toBe(1);
    second.releasePolling(KEY);
    expect(vi.getTimerCount()).toBe(0);
    expect(first.pollingActive(KEY)).toBe(false);
  });

  it('taskStore 在模块重复求值后任务轮询不成倍放大', async () => {
    const baseline = vi.getTimerCount();

    vi.resetModules();
    const first = await import('./taskStore');
    first.useTaskStore.getState().startPolling();
    expect(vi.getTimerCount()).toBe(baseline + 1);

    vi.resetModules();
    const second = await import('./taskStore');
    second.useTaskStore.getState().startPolling();
    expect(vi.getTimerCount()).toBe(baseline + 1);

    api.listTasks.mockClear();
    await vi.advanceTimersByTimeAsync(6000);
    // 单一 interval（2000ms）→ 6 秒 3 次；旧实现是 2 个 interval → 6 次
    expect(api.listTasks).toHaveBeenCalledTimes(3);

    second.useTaskStore.getState().stopPolling();
    first.useTaskStore.getState().stopPolling();
    expect(vi.getTimerCount()).toBe(baseline);
  });

  it('backendStore 在模块重复求值后健康轮询不成倍放大', async () => {
    const baseline = vi.getTimerCount();

    vi.resetModules();
    const first = await import('./backendStore');
    first.useBackendStore.getState().startPolling();
    expect(vi.getTimerCount()).toBe(baseline + 1);

    vi.resetModules();
    const second = await import('./backendStore');
    second.useBackendStore.getState().startPolling();
    expect(vi.getTimerCount()).toBe(baseline + 1);

    api.checkHealth.mockClear();
    await vi.advanceTimersByTimeAsync(30000);
    // 单一 interval（10000ms）→ 30 秒 3 次
    expect(api.checkHealth).toHaveBeenCalledTimes(3);

    second.useBackendStore.getState().stopPolling();
    first.useBackendStore.getState().stopPolling();
    expect(vi.getTimerCount()).toBe(baseline);
  });
});

describe('store 轮询对外行为不回归', () => {
  it('taskStore 停止轮询后不再请求', async () => {
    vi.resetModules();
    const { useTaskStore } = await import('./taskStore');

    useTaskStore.getState().startPolling();
    api.listTasks.mockClear();
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.listTasks).toHaveBeenCalledTimes(1);

    useTaskStore.getState().stopPolling();
    api.listTasks.mockClear();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.listTasks).not.toHaveBeenCalled();
  });

  it('backendStore 首次订阅立即探测一次，之后按间隔探测', async () => {
    vi.resetModules();
    const { useBackendStore } = await import('./backendStore');

    api.checkHealth.mockClear();
    useBackendStore.getState().startPolling();
    expect(api.checkHealth).toHaveBeenCalledTimes(1); // 首次立即探测

    await vi.advanceTimersByTimeAsync(10000);
    expect(api.checkHealth).toHaveBeenCalledTimes(2);

    // 第二次订阅不应再触发"立即探测"
    useBackendStore.getState().startPolling();
    expect(api.checkHealth).toHaveBeenCalledTimes(2);

    useBackendStore.getState().stopPolling();
    useBackendStore.getState().stopPolling();
    api.checkHealth.mockClear();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.checkHealth).not.toHaveBeenCalled();
  });
});
