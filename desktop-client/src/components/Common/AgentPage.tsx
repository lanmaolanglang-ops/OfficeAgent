import { useEffect } from 'react';
import Workspace from '../Workspace';
import { useBackendStore, useTaskStore } from '../../stores';
import type { AgentType } from '../../types';

interface AgentPageProps {
  title: string;
  subtitle: string;
  agent?: AgentType;
}

/**
 * 统一的 Agent 工作页外壳。
 * 各 Agent 页（Word/PPT/Excel）与工作台共享「健康轮询 + 任务加载」逻辑，
 * 只需声明标题与归属 Agent，避免重复样板代码。
 */
export default function AgentPage({ title, subtitle, agent = 'auto' }: AgentPageProps) {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title={title} subtitle={subtitle} agent={agent} />;
}
