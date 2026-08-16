import { create } from 'zustand';
import type { Task } from '../types';
import { listTasks } from '../services/api';

interface TaskState {
  tasks: Task[];
  loading: boolean;
  loadTasks: () => Promise<void>;
}

// 任务提交/轮询/取消/通知已全部收敛到 chatStore 的对话轮询里，
// 这里只保留「任务列表加载」这一处真正被 UI 使用的能力。
export const useTaskStore = create<TaskState>((set) => ({
  tasks: [],
  loading: false,

  loadTasks: async () => {
    set({ loading: true });
    try {
      const tasks = await listTasks();
      set({ tasks, loading: false });
    } catch {
      set({ loading: false });
    }
  },
}));
