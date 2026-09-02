import { useEffect, useRef } from 'react';
import { getBackendUrl } from '../../services/api';
import { useSettingsStore } from '../../stores';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import StatusPanel from './StatusPanel';
import { useBackendStore } from '../../stores';

export default function MainLayout() {
  const location = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const backendUrlRaw = useSettingsStore((s) => s.settings.backend_url) || getBackendUrl();
  const backendUrlLabel = (() => { try { return backendUrlRaw.replace(/^https?:\/\//, ''); } catch { return backendUrlRaw; } })();
  const { connected, degraded } = useBackendStore();
  useEffect(() => { mainRef.current?.focus({ preventScroll: true }); }, [location.pathname]);
  return (
    <div className="app-shell flex h-screen w-screen overflow-hidden bg-bg text-fg">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="app-header flex h-16 flex-shrink-0 items-center justify-between px-7">
          <div className="app-context-label">本地办公执行中心</div>
          <div className="flex items-center gap-3 text-xs text-fg-muted">
            <span className={`service-badge ${connected ? 'service-badge-ok' : degraded ? 'service-badge-warn' : 'service-badge-danger'}`}>
              <span className="service-pulse" aria-hidden="true" />
              {connected ? '服务运行中' : degraded ? '服务降级' : '服务未连接'}
            </span>
            <span className="font-mono text-[11px] text-fg-faint">{backendUrlLabel}</span>
          </div>
        </header>
        <div className="flex min-h-0 flex-1">
          <main ref={mainRef} id="main-content" tabIndex={-1} className="flex min-w-0 flex-1 flex-col overflow-hidden"><Outlet /></main>
          <StatusPanel />
        </div>
      </div>
    </div>
  );
}
