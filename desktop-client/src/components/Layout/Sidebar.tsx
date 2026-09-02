import { useSettingsStore, useBackendStore } from '../../stores';
import { getBackendUrl } from '../../services/api';
import { NavLink } from 'react-router-dom';
import {
  House, FileText, Presentation, Sheet,
  FolderOpen, History, Settings,
} from 'lucide-react';

const navGroups = [
  { label: '工作区', items: [
    { to: '/', icon: House, label: '工作台' },
  ] },
  { label: '专业执行', items: [
    { to: '/word', icon: FileText, label: 'Word Agent' },
    { to: '/excel', icon: Sheet, label: 'Excel Agent' },
    { to: '/ppt', icon: Presentation, label: 'PPT Agent' },
  ] },
  { label: '资产与系统', items: [
    { to: '/files', icon: FolderOpen, label: '文件管理' },
    { to: '/history', icon: History, label: '任务记录' },
    { to: '/settings', icon: Settings, label: '设置' },
  ] },
];

function useBackendUrlLabel(): string {
  const url = useSettingsStore((s) => s.settings.backend_url) || getBackendUrl();
  try { return url.replace(/^https?:\/\//, ''); } catch { return url; }
}

export default function Sidebar() {
  const backendUrlLabel = useBackendUrlLabel();
  const { connected, degraded } = useBackendStore();
  return (
    <aside className="app-sidebar flex h-full w-[236px] flex-shrink-0 flex-col">
      <div className="sidebar-brand flex h-[88px] items-center gap-3 px-5">
        <div className="brand-mark" aria-hidden="true"><span>OA</span></div>
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-tight text-sidebar-strong">OfficeAgent</p>
          <p className="mt-1 text-[10px] tracking-[.12em] text-sidebar-muted">本地执行台</p>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-5" aria-label="主要导航">
        {navGroups.map((group) => (
          <div key={group.label} className="sidebar-group">
            <p className="sidebar-group-label">{group.label}</p>
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  title={item.label}
                  className={({ isActive }) => `sidebar-link ${isActive ? 'sidebar-link-active' : ''}`}
                >
                  <Icon className="h-[17px] w-[17px] flex-shrink-0" aria-hidden="true" />
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebar-service m-3 p-3.5">
        <div className={`flex items-center gap-2 text-xs font-medium ${connected ? 'text-sidebar-ok' : degraded ? 'text-sidebar-warn' : 'text-sidebar-danger'}`}>
          <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
          {connected ? '本地服务运行中' : degraded ? '服务降级' : '服务未连接'}
        </div>
        <p className="mt-2 truncate font-mono text-[10px] text-sidebar-muted">v{__APP_VERSION__} · {backendUrlLabel}</p>
      </div>
    </aside>
  );
}
