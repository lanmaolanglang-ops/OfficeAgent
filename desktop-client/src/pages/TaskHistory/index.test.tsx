/**
 * P5-6（审计 M37②）：任务历史页的列表 identity / 分页 / 时间兜底回归。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  listTasksPage: vi.fn(async () => ({ tasks: [], total: 0, page: 1, page_size: 20 })),
  getFileUrl: (id: string) => `http://127.0.0.1:8765/api/file/download/${id}`,
}));

import { listTasksPage } from '../../services/api';
import TaskHistory from './index';
import { useTaskStore, TASKS_PAGE_SIZE } from '../../stores/taskStore';
import type { Task } from '../../types';

function makeTask(over: Partial<Task> & { id: string }): Task {
  return {
    type: 'word_task', agent: 'auto', status: 'completed', progress: 100,
    created_at: '2026-01-01T00:00:00.000Z', ...over,
  } as Task;
}

function page(tasks: Task[], total: number, p: number) {
  return { tasks, total, page: p, page_size: TASKS_PAGE_SIZE };
}

describe('TaskHistory', () => {
  // vitest 未开启 globals，RTL 不会自动 cleanup，需显式清理避免 DOM 跨用例累积
  afterEach(() => { cleanup(); });

  beforeEach(() => {
    vi.mocked(listTasksPage).mockReset();
    useTaskStore.setState({
      tasks: [], total: 0, page: 1, pageSize: TASKS_PAGE_SIZE,
      loading: false, loadError: false,
    });
  });

  it('空列表显示占位而不是错误', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([], 0, 1));
    render(<TaskHistory />);
    expect(await screen.findByText('暂无任务记录')).toBeTruthy();
  });

  it('非法/缺失时间显式兜底，不渲染 Invalid Date', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([
      makeTask({ id: 'a', created_at: 'not-a-date' }),
      makeTask({ id: 'b', created_at: undefined as unknown as string }),
    ], 2, 1));
    render(<TaskHistory />);
    await waitFor(() => expect(screen.queryAllByText('—').length).toBe(2));
    expect(document.body.textContent).not.toContain('Invalid Date');
  });

  it('显示总数与页码，首页禁用"上一页"', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([makeTask({ id: 'a' })], 42, 1));
    render(<TaskHistory />);
    expect(await screen.findByText(/共 42 条 · 第 1 \/ 3 页/)).toBeTruthy();
    expect((screen.getByLabelText('上一页') as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('下一页') as HTMLButtonElement).disabled).toBe(false);
  });

  it('点击"下一页"请求第 2 页', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([makeTask({ id: 'a' })], 42, 1));
    render(<TaskHistory />);
    await screen.findByText(/第 1 \/ 3 页/);

    vi.mocked(listTasksPage).mockResolvedValue(page([makeTask({ id: 'b' })], 42, 2));
    fireEvent.click(screen.getByLabelText('下一页'));

    await waitFor(() => expect(vi.mocked(listTasksPage)).toHaveBeenLastCalledWith({
      page: 2, page_size: TASKS_PAGE_SIZE,
    }));
    expect(await screen.findByText(/第 2 \/ 3 页/)).toBeTruthy();
  });

  it('重复显示名 / 缺失 id 时不产生重复 key', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => { /* 断言用 */ });
    try {
      vi.mocked(listTasksPage).mockResolvedValue(page([
        makeTask({ id: '', type: 'word_task', created_at: '2026-01-01T00:00:00.000Z' }),
        makeTask({ id: '', type: 'word_task', created_at: '2026-01-01T00:00:00.000Z' }),
      ], 2, 1));
      render(<TaskHistory />);
      await waitFor(() => expect(screen.getAllByText('word task').length).toBe(2));
      const keyWarnings = errorSpy.mock.calls
        .map((args) => String(args[0]))
        .filter((msg) => msg.includes('same key') || msg.includes('unique "key"'));
      expect(keyWarnings).toEqual([]);
    } finally {
      errorSpy.mockRestore();
    }
  });

  it('输出文件条目以 file_id 为 identity', async () => {
    vi.mocked(listTasksPage).mockResolvedValue(page([
      makeTask({
        id: 'a',
        output_files: [
          { file_id: 'f1', filename: '同名.docx' },
          { file_id: 'f2', filename: '同名.docx' },
        ],
      } as Partial<Task> & { id: string }),
    ], 1, 1));
    render(<TaskHistory />);
    const links = await screen.findAllByText('同名.docx');
    expect(links).toHaveLength(2);
    expect((links[0] as HTMLAnchorElement).getAttribute('href')).toContain('f1');
    expect((links[1] as HTMLAnchorElement).getAttribute('href')).toContain('f2');
  });
});
