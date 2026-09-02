import { useEffect } from 'react';
import Workspace from '../Workspace';
import { useBackendStore } from '../../stores';
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

  // 任务列表轮询由 Workspace 内的 taskStore.startPolling 负责，这里只管健康轮询
  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  return <Workspace title={title} subtitle={subtitle} agent={agent} />;
}
