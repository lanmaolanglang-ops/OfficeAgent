import { NavLink } from 'react-router-dom';
import {
  House, FileText, Presentation, Sheet,
  FolderOpen, History, Settings, Sparkles,
} from 'lucide-react';

const navGroups = [
  [
    { to: '/', icon: House, label: '工作台' },
  ],
  [
    { to: '/word', icon: FileText, label: 'Word Agent' },
    { to: '/excel', icon: Sheet, label: 'Excel Agent' },
    { to: '/ppt', icon: Presentation, label: 'PPT Agent' },
  ],
  [
    { to: '/files', icon: FolderOpen, label: '文件管理' },
    { to: '/history', icon: History, label: '任务记录' },
    { to: '/settings', icon: Settings, label: '设置' },
  ],
];

export default function Sidebar() {
  return (
    <aside className="app-sidebar w-[224px] bg-white border-r border-[#e8ebf2] flex flex-col h-full flex-shrink-0">
      <div className="h-[76px] flex items-center gap-3 px-5 border-b border-[#edf0f5]">
        <div className="brand-mark"><Sparkles className="w-5 h-5 text-white" /></div>
        <div className="min-w-0">
          <p className="text-[15px] font-bold text-[#182033] leading-tight">OfficeAgent</p>
          <p className="text-[11px] text-[#8a94a8] mt-1">本地智能办公助手</p>
        </div>
      </div>

      <nav className="flex-1 px-3 py-5 overflow-y-auto">
        {navGroups.map((group, groupIndex) => (
          <div key={groupIndex} className={groupIndex ? 'mt-5 pt-5 border-t border-[#f0f2f6]' : ''}>
            {group.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  title={item.label}
                  className={({ isActive }) => `sidebar-link ${isActive ? 'sidebar-link-active' : ''}`}
                >
                  <Icon className="w-[18px] h-[18px] flex-shrink-0" />
                  <span>{item.label}</span>
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="m-3 p-3.5 rounded-lg bg-[#f7f9fc] border border-[#edf0f5]">
        <div className="flex items-center gap-2 text-xs font-medium text-[#35405a]">
          <span className="w-2 h-2 rounded-full bg-[#22b573] shadow-[0_0_0_3px_rgba(34,181,115,.12)]" />
          本地服务运行中
        </div>
        <p className="text-[10px] text-[#9aa3b5] mt-2">v0.49.0 · 127.0.0.1:8765</p>
      </div>
    </aside>
  );
}
