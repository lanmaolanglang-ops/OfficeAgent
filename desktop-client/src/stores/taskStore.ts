import { create } from 'zustand';
import type { Task } from '../types';
import { listTasks } from '../services/api';

interface TaskState {
  tasks: Task[];
  loading: boolean;
  loadError: boolean;
  loadTasks: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
}

// 任务提交/轮询/取消/通知已全部收敛到 chatStore 的对话轮询里，
// 这里保留「任务列表加载 + 全局共享轮询」（refcount，多个订阅者共用一个定时器）。
let pollTimer: ReturnType<typeof setInterval> | null = null;
let pollRefs = 0;

export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  loading: false,
  loadError: false,

  startPolling: () => {
    pollRefs += 1;
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      useTaskStore.getState().loadTasks();
    }, 2000);
  },

  stopPolling: () => {
    pollRefs = Math.max(0, pollRefs - 1);
    if (pollRefs === 0 && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
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
