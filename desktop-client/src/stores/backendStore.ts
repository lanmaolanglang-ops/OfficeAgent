import { create } from 'zustand';
import type { HealthResponse } from '../types';
import { checkHealth } from '../services/api';

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
let pollInterval: ReturnType<typeof setInterval> | null = null;
let pollSubscribers = 0;

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
    pollSubscribers += 1;
    if (pollInterval) return;
    useBackendStore.getState().check();
    pollInterval = setInterval(() => {
      useBackendStore.getState().check();
    }, 10000);
  },

  stopPolling: () => {
    pollSubscribers = Math.max(0, pollSubscribers - 1);
    if (pollSubscribers === 0 && pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  },
}));
