/**
 * P1-25：Workspace 的生命周期必须与它注册的轮询资源对称。
 *
 * Workspace 挂载时调用 taskStore.startPolling()，卸载时 stopPolling()；这里在
 * **真实组件**层面验证"mount 注册 / unmount 释放 / 重复切换不增长"。
 *
 * 计数方式：`vi.getTimerCount()` 会把 React 调度器自身的 pending timer 也数进
 * 来，因此在测试内部**只追踪 2000ms 的任务轮询 interval**（创建与清除成对统计），
 * 再叠加"卸载后 30 秒零请求"的行为证据。
 */

import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Workspace from './index';

const api = vi.hoisted(() => ({
  listTasks: vi.fn(async () => []),
  // P5-6：taskStore 改为读取分页元数据，mock 必须覆盖同一入口
  listTasksPage: vi.fn(async () => ({ tasks: [], total: 0, page: 1, page_size: 20 })),
  checkHealth: vi.fn(async () => ({ success: true, data: { status: 'healthy' } })),
  listFilesPage: vi.fn(async () => ({ files: [], total: 0 })),
  sendChatMessage: vi.fn(),
  getTask: vi.fn(),
}));

vi.mock('../../services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/api')>();
  return { ...actual, ...api };
});

const TASK_POLL_MS = 2000;

function trackTaskPollTimers() {
  const realSetInterval = globalThis.setInterval;
  const realClearInterval = globalThis.clearInterval;
  const alive = new Set<unknown>();

  const setSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation(
    ((handler: TimerHandler, timeout?: number, ...rest: unknown[]) => {
      const handle = realSetInterval(handler, timeout, ...rest);
      if (timeout === TASK_POLL_MS) alive.add(handle);
      return handle;
    }) as typeof globalThis.setInterval,
  );
  const clearSpy = vi.spyOn(globalThis, 'clearInterval').mockImplementation(
    ((handle?: Parameters<typeof globalThis.clearInterval>[0]) => {
      alive.delete(handle);
      return realClearInterval(handle as number);
    }) as typeof globalThis.clearInterval,
  );

  return {
    aliveCount: () => alive.size,
    createdTaskPolls: () => setSpy.mock.calls.filter(([, ms]) => ms === TASK_POLL_MS).length,
    clearedCount: () => clearSpy.mock.calls.length,
    restore: () => {
      setSpy.mockRestore();
      clearSpy.mockRestore();
    },
  };
}

async function poller() {
  return import('../../stores/refCountedPoller');
}

function renderWorkspace() {
  return render(
    <MemoryRouter>
      <Workspace />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  api.listTasksPage.mockClear();
});

afterEach(async () => {
  cleanup();
  const p = await poller();
  p.resetPolling('taskStore');
  p.resetPolling('backendStore');
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('Workspace 轮询资源生命周期', () => {
  it('mount 注册一个轮询器，unmount 对称释放', async () => {
    const p = await poller();
    const tracker = trackTaskPollTimers();
    try {
      const { unmount } = renderWorkspace();

      expect(p.pollingSubscribers('taskStore')).toBe(1);
      expect(p.pollingActive('taskStore')).toBe(true);
      expect(tracker.aliveCount()).toBe(1);
      expect(tracker.createdTaskPolls()).toBe(1);

      unmount();

      expect(p.pollingSubscribers('taskStore')).toBe(0);
      expect(p.pollingActive('taskStore')).toBe(false);
      expect(tracker.aliveCount()).toBe(0);
      expect(tracker.clearedCount()).toBe(1);
    } finally {
      tracker.restore();
    }
  });

  it('重复 mount/unmount 12 次：活跃轮询器数不增长且每次都被释放', async () => {
    const p = await poller();
    const tracker = trackTaskPollTimers();
    try {
      for (let i = 0; i < 12; i += 1) {
        const { unmount } = renderWorkspace();
        expect(p.pollingSubscribers('taskStore')).toBe(1);
        unmount();
        expect(p.pollingSubscribers('taskStore')).toBe(0);
        expect(tracker.aliveCount()).toBe(0);
      }

      expect(tracker.createdTaskPolls()).toBe(12);
      expect(tracker.aliveCount()).toBe(0);
    } finally {
      tracker.restore();
    }
  });

  it('工作台切换（卸载后重新挂载）不累积定时器', async () => {
    const p = await poller();
    const tracker = trackTaskPollTimers();
    try {
      const first = renderWorkspace();
      first.unmount();
      const second = renderWorkspace();

      expect(p.pollingSubscribers('taskStore')).toBe(1);
      expect(tracker.aliveCount()).toBe(1);

      second.unmount();
      expect(tracker.aliveCount()).toBe(0);
    } finally {
      tracker.restore();
    }
  });

  it('长期打开时同一订阅不会因重渲染重复绑定', async () => {
    const p = await poller();
    const tracker = trackTaskPollTimers();
    try {
      const { unmount } = renderWorkspace();
      const store = (await import('../../stores')).useTaskStore;

      for (let i = 0; i < 5; i += 1) {
        // 触发 Workspace 重渲染（它整订阅了 taskStore）
        store.setState({ tasks: [], loading: i % 2 === 0 });
        await vi.advanceTimersByTimeAsync(0);
      }

      expect(p.pollingSubscribers('taskStore')).toBe(1);
      expect(tracker.aliveCount()).toBe(1);
      expect(tracker.createdTaskPolls()).toBe(1);

      unmount();
      expect(tracker.aliveCount()).toBe(0);
    } finally {
      tracker.restore();
    }
  });

  it('卸载后不再发起任务列表请求（无孤儿 interval）', async () => {
    const { unmount } = renderWorkspace();
    unmount();

    api.listTasksPage.mockClear();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.listTasksPage).not.toHaveBeenCalled();
  });

  it('12 次挂载/卸载后，后续 30 秒仍零请求', async () => {
    for (let i = 0; i < 12; i += 1) {
      renderWorkspace().unmount();
    }

    api.listTasksPage.mockClear();
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.listTasksPage).not.toHaveBeenCalled();
  });

  it('挂载期间按 2 秒节奏轮询任务列表', async () => {
    const { unmount } = renderWorkspace();
    api.listTasksPage.mockClear();

    await vi.advanceTimersByTimeAsync(6000);
    expect(api.listTasksPage).toHaveBeenCalledTimes(3);

    unmount();
  });
});
