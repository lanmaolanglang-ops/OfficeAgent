import { Outlet } from 'react-router-dom';
import { Bell, CircleHelp } from 'lucide-react';
import Sidebar from './Sidebar';
import StatusPanel from './StatusPanel';
import { useBackendStore } from '../../stores';

export default function MainLayout() {
  const { connected, degraded } = useBackendStore();
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#f4f6fa] text-[#182033]">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="app-topbar h-[58px] bg-white border-b border-[#e8ebf2] flex items-center justify-between px-6 flex-shrink-0">
          <div className="flex items-center gap-3 text-xs text-[#7d879b]">
            <span>系统状态</span>
            <span className={`w-2 h-2 rounded-full ${connected ? 'bg-[#22b573]' : degraded ? 'bg-[#f5a623]' : 'bg-[#ef5b5b]'}`} />
            <span className="font-medium text-[#47516a]">{connected ? '服务运行中' : degraded ? '服务降级' : '服务未连接'}</span>
            <span className="text-[#c0c6d2]">127.0.0.1:8765</span>
          </div>
          <div className="flex items-center gap-1">
            <button className="icon-button" title="帮助"><CircleHelp className="w-[17px] h-[17px]" /></button>
            <button className="icon-button" title="通知"><Bell className="w-[17px] h-[17px]" /></button>
          </div>
        </header>
        <div className="flex min-h-0 flex-1">
          <main className="flex-1 flex flex-col min-w-0 overflow-hidden"><Outlet /></main>
          <StatusPanel />
        </div>
      </div>
    </div>
  );
}
