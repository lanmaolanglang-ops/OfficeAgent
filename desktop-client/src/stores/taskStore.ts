import { create } from 'zustand';
import type { Task } from '../types';
import { listTasksPage } from '../services/api';
import { acquirePolling, releasePolling } from './refCountedPoller';

export const TASKS_PAGE_SIZE = 20;

interface TaskState {
  tasks: Task[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  loadError: boolean;
  loadTasks: (page?: number) => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

// 任务提交/轮询/取消/通知已全部收敛到 chatStore 的对话轮询里，
// 这里保留「任务列表加载 + 全局共享轮询」（引用计数，多个订阅者共用一个定时器）。
//
// 句柄放在进程级槽位（refCountedPoller）而不是模块作用域：模块级句柄在
// Vite HMR / 模块重复求值时会失联，留下无法回收的孤儿 interval（P1-25 实测
// 见 refCountedPoller 注释）。
const TASK_POLL_KEY = 'taskStore';
const TASK_POLL_INTERVAL_MS = 2000;

// P5-6：只有最后一次发起的请求可以写入状态，避免慢的旧响应覆盖用户刚切到的新页。
let requestSeq = 0;

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  total: 0,
  page: 1,
  pageSize: TASKS_PAGE_SIZE,
  loading: false,
  loadError: false,

  startPolling: () => {
    acquirePolling(TASK_POLL_KEY, TASK_POLL_INTERVAL_MS, () => {
      void useTaskStore.getState().loadTasks();
    });
  },

  stopPolling: () => {
    releasePolling(TASK_POLL_KEY);
  },

  loadTasks: async (page?: number) => {
    const target = Math.max(1, Math.floor(page ?? get().page));
    const seq = (requestSeq += 1);
    set({ loading: true });
    try {
      const result = await listTasksPage({ page: target, page_size: get().pageSize });
      // 过期响应直接丢弃（用户已经翻到别的页/又发起了一次加载）
      if (seq !== requestSeq) return;
      const size = result.page_size || get().pageSize;
      const maxPage = Math.max(1, Math.ceil(result.total / size));
      if (target > maxPage) {
        // 越界页（例如删掉了最后一页的最后一条）：回退到最后一页而不是显示空列表
        void get().loadTasks(maxPage);
        return;
      }
      set({
        tasks: result.tasks,
        total: result.total,
        page: target,
        loading: false,
        loadError: false,
      });
    } catch {
      if (seq !== requestSeq) return;
      // 保留旧任务列表，但把失败暴露给 UI，避免把"后端不可达"伪装成"没有任务"
      set({ loading: false, loadError: true });
    }
  },
}));
