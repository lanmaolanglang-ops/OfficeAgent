import { create } from 'zustand';
import type { HealthResponse } from '../types';
import { checkHealth } from '../services/api';
import { acquirePolling, releasePolling } from './refCountedPoller';

interface BackendState {
  health: HealthResponse['data'] | null;
  connected: boolean;
  degraded: boolean;
  checking: boolean;
  lastCheck: number | null;
  check: () => Promise<boolean>;
  startPolling: () => void;
  stopPolling: () => void;
}

// 引用计数轮询：多个页面同时 startPolling 时共享一个 interval，
// 只有最后一个页面卸载（计数归零）才真正停止，避免误杀共享轮询。
//
// 句柄放在进程级槽位（refCountedPoller）而不是模块作用域：模块级句柄在
// Vite HMR / 模块重复求值时会失联，留下无法回收的孤儿 interval，使健康轮询
// 成倍放大（P1-25，与 taskStore 同一根因）。
const BACKEND_POLL_KEY = 'backendStore';
const BACKEND_POLL_INTERVAL_MS = 10000;

export const useBackendStore = create<BackendState>((set) => ({
  health: null,
  connected: false,
  degraded: false,
  checking: false,
  lastCheck: null,

  check: async () => {
    set({ checking: true });
    try {
      const response = await checkHealth();
      const connected = response.success && response.data?.status === 'healthy';
      const degraded = !!response.data && response.data.status !== 'healthy';
      set({
        health: response.data || null,
        connected,
        degraded,
        checking: false,
        lastCheck: Date.now(),
      });
      return connected;
    } catch {
      set({
        health: null,
        connected: false,
        degraded: false,
        checking: false,
        lastCheck: Date.now(),
      });
      return false;
    }
  },

  startPolling: () => {
    const createdTimer = acquirePolling(
      BACKEND_POLL_KEY,
      BACKEND_POLL_INTERVAL_MS,
      () => {
        void useBackendStore.getState().check();
      },
    );
    // 仅在新建轮询器时做一次立即检测（保持原有"进入页面立刻探一次"的行为）
    if (createdTimer) {
      void useBackendStore.getState().check();
    }
  },

  stopPolling: () => {
    releasePolling(BACKEND_POLL_KEY);
  },
}));
