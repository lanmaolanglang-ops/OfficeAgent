import { useEffect } from 'react';
import Workspace from '../../components/Workspace';
import { useBackendStore, useTaskStore } from '../../stores';

export default function Dashboard() {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title="工作台" subtitle="智能办公助手" />;
}
