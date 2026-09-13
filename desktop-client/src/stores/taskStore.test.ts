/**
 * P5-6（审计 M37②）：任务列表分页状态回归。
 *
 * 修复前：`loadTasks` 固定 `listTasks({ page_size: 50 })`，没有分页状态、
 * 没有 total、也没有并发保护——翻页时慢的旧响应会覆盖新页，删除最后一页的
 * 最后一条后会停在越界页显示空列表。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../services/api', () => ({
  listTasksPage: vi.fn(),
}));

import { listTasksPage } from '../services/api';
import { useTaskStore, TASKS_PAGE_SIZE } from './taskStore';
import type { Task } from '../types';

function makeTask(id: string): Task {
  return {
    id, type: 'word_task', agent: 'auto', status: 'completed', progress: 100,
    created_at: '2026-01-01T00:00:00.000Z',
  } as Task;
}

function page(tasks: Task[], total: number, p: number) {
  return { tasks, total, page: p, page_size: TASKS_PAGE_SIZE };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

describe('taskStore 分页', () => {
  beforeEach(() => {
    vi.mocked(listTasksPage).mockReset();
    useTaskStore.setState({
      tasks: [], total: 0, page: 1, pageSize: TASKS_PAGE_SIZE,
      loading: false, loadError: false,
    });
  });

  it('默认加载第 1 页并记录 total / page', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([makeTask('t1')], 42, 1));
    await useTaskStore.getState().loadTasks();
    expect(vi.mocked(listTasksPage)).toHaveBeenCalledWith({ page: 1, page_size: TASKS_PAGE_SIZE });
    const state = useTaskStore.getState();
    expect(state.page).toBe(1);
    expect(state.total).toBe(42);
    expect(state.tasks.map((t) => t.id)).toEqual(['t1']);
  });

  it('翻到中间页与最后一页', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([makeTask('t21')], 42, 3));
    await useTaskStore.getState().loadTasks(3);
    expect(useTaskStore.getState().page).toBe(3);
    expect(useTaskStore.getState().tasks.map((t) => t.id)).toEqual(['t21']);
  });

  it('越界页回退到最后一页（删除最后一页最后一条后不显示空列表）', async () => {
    vi.mocked(listTasksPage)
      .mockResolvedValueOnce(page([], 20, 2))       // 请求第 2 页，但现在只有 20 条 → 1 页
      .mockResolvedValueOnce(page([makeTask('t1')], 20, 1));
    await useTaskStore.getState().loadTasks(2);
    expect(vi.mocked(listTasksPage)).toHaveBeenLastCalledWith({ page: 1, page_size: TASKS_PAGE_SIZE });
    expect(useTaskStore.getState().page).toBe(1);
    expect(useTaskStore.getState().tasks).toHaveLength(1);
  });

  it('过期响应不覆盖当前页（stale async response）', async () => {
    const slow = deferred<ReturnType<typeof page>>();
    const fast = deferred<ReturnType<typeof page>>();
    vi.mocked(listTasksPage)
      .mockReturnValueOnce(slow.promise)
      .mockReturnValueOnce(fast.promise);

    const first = useTaskStore.getState().loadTasks(1);
    const second = useTaskStore.getState().loadTasks(2);

    fast.resolve(page([makeTask('b')], 42, 2));
    await second;
    slow.resolve(page([makeTask('a')], 42, 1));
    await first;

    const state = useTaskStore.getState();
    expect(state.page).toBe(2);
    expect(state.tasks.map((t) => t.id)).toEqual(['b']);
  });

  it('加载失败保留旧列表并把失败暴露给 UI', async () => {
    vi.mocked(listTasksPage).mockResolvedValueOnce(page([makeTask('t1')], 1, 1));
    await useTaskStore.getState().loadTasks();
    vi.mocked(listTasksPage).mockRejectedValueOnce(new Error('backend down'));
    await useTaskStore.getState().loadTasks();
    const state = useTaskStore.getState();
    expect(state.loadError).toBe(true);
    expect(state.loading).toBe(false);
    expect(state.tasks.map((t) => t.id)).toEqual(['t1']);
  });

  it('空列表', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([], 0, 1));
    await useTaskStore.getState().loadTasks();
    expect(useTaskStore.getState().tasks).toEqual([]);
    expect(useTaskStore.getState().total).toBe(0);
  });
});
