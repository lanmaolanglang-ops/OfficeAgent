import { useEffect } from 'react';
import Workspace from '../../components/Workspace';
import { useBackendStore, useTaskStore } from '../../stores';

export default function WorkflowPage() {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title="Workflow" subtitle="多智能体协作 · 复杂任务自动处理" />;
}
