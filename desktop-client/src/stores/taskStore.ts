import { create } from 'zustand';
import type { Task, CreateTaskRequest } from '../types';
import { createTask, getTask, listTasks, cancelTask } from '../services/api';
import { sendNotification } from '../services/tauri';

interface TaskState {
  tasks: Task[];
  currentTask: Task | null;
  loading: boolean;
  loadTasks: () => Promise<void>;
  submitTask: (req: CreateTaskRequest) => Promise<Task>;
  pollTask: (taskId: string) => Promise<void>;
  stopPolling: () => void;
  cancelTaskById: (taskId: string) => Promise<void>;
}

let pollTimer: ReturnType<typeof setTimeout> | null = null;
const notifiedTasks = new Set<string>();

// 任务类型中文名称
function getTaskTypeName(type: string): string {
  const names: Record<string, string> = {
    word_format: 'Word排版',
    word_process: 'Word处理',
    word_convert: 'Word转换',
    ppt_generate: 'PPT生成',
    ppt_design: 'PPT设计',
    excel_analyze: 'Excel分析',
    excel_chart: 'Excel图表',
    excel_process: 'Excel处理',
    file_convert: '文件转换',
  };
  return names[type] || type;
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  currentTask: null,
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

  submitTask: async (req: CreateTaskRequest) => {
    set({ loading: true });
    try {
      const task = await createTask(req);
      set((state) => ({
        currentTask: task,
        tasks: [task, ...state.tasks],
        loading: false,
      }));
      get().pollTask(task.id);
      return task;
    } catch (error) {
      set({ loading: false });
      throw error;
    }
  },

  pollTask: async (taskId: string) => {
    if (pollTimer) clearTimeout(pollTimer);

    const poll = async () => {
      try {
        const task = await getTask(taskId);

        set((state) => ({
          currentTask: state.currentTask?.id === taskId ? task : state.currentTask,
          tasks: state.tasks.map((t) => (t.id === taskId ? task : t)),
        }));

        // 任务完成时发送通知
        if (task.status === 'completed' && !notifiedTasks.has(taskId)) {
          notifiedTasks.add(taskId);
          const taskName = getTaskTypeName(task.type);
          sendNotification(
            `${taskName}任务已完成`,
            `任务"${taskName}"处理完成，可以查看结果了。`,
          ).catch(() => {});
        }

        // 任务失败时发送通知
        if (task.status === 'failed' && !notifiedTasks.has(taskId)) {
          notifiedTasks.add(taskId);
          const taskName = getTaskTypeName(task.type);
          sendNotification(
            `${taskName}任务失败`,
            task.error || '任务处理过程中出现错误，请重试。',
          ).catch(() => {});
        }

        if (task.status === 'processing' || task.status === 'pending') {
          pollTimer = setTimeout(poll, 2000);
        }
      } catch {
        pollTimer = setTimeout(poll, 5000);
      }
    };

    poll();
  },

  stopPolling: () => {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  },

  cancelTaskById: async (taskId: string) => {
    try {
      await cancelTask(taskId);
      get().stopPolling();
      set((state) => ({
        currentTask: state.currentTask?.id === taskId ? null : state.currentTask,
        tasks: state.tasks.map((t) =>
          t.id === taskId ? { ...t, status: 'cancelled' as const } : t
        ),
      }));
    } catch {
      // ignore
    }
  },
}));
