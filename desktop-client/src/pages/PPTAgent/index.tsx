import { useEffect } from 'react';
import Workspace from '../../components/Workspace';
import { useBackendStore, useTaskStore } from '../../stores';

export default function PPTAgentPage() {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title="PPT Agent" subtitle="智能演示文稿 · 生成 · 美化 · 模板" agent="ppt" />;
}
