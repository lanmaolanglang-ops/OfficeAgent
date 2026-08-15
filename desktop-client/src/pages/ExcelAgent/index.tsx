import { useEffect } from 'react';
import Workspace from '../../components/Workspace';
import { useBackendStore, useTaskStore } from '../../stores';

export default function ExcelAgentPage() {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title="Excel Agent" subtitle="智能表格处理 · 分析 · 公式 · 报表" />;
}
