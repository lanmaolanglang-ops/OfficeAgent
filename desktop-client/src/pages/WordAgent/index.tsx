import { useEffect } from 'react';
import Workspace from '../../components/Workspace';
import { useBackendStore, useTaskStore } from '../../stores';

export default function WordAgentPage() {
  const { startPolling, stopPolling } = useBackendStore();
  const { loadTasks } = useTaskStore();

  useEffect(() => {
    startPolling();
    loadTasks();
    return () => stopPolling();
  }, [startPolling, stopPolling, loadTasks]);

  return <Workspace title="Word Agent" subtitle="智能文档处理 · 排版 · 翻译 · 润色" agent="word" />;
}
