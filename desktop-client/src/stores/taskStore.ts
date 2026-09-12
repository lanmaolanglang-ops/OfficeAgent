import { create } from 'zustand';
import type { Task } from '../types';
import { listTasks } from '../services/api';
import { acquirePolling, releasePolling } from './refCountedPoller';

interface TaskState {
  tasks: Task[];
  loading: boolean;
  loadError: boolean;
  loadTasks: () => Promise<void>;
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

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
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

  loadTasks: async () => {
    set({ loading: true });
    try {
      const tasks = await listTasks({ page_size: 50 });
      set({ tasks, loading: false, loadError: false });
    } catch {
      // 保留旧任务列表，但把失败暴露给 UI，避免把"后端不可达"伪装成"没有任务"
      set({ loading: false, loadError: true });
    }
  },
}));
