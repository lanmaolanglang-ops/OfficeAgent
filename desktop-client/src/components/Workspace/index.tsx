import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Bot, FileText, Presentation, Sheet, User } from 'lucide-react';
import FileUploader from './FileUploader';
import TaskTimeline, { type TimelineStep } from './TaskTimeline';
import ChatInput from './ChatInput';
import { useChatStore, useTaskStore, useSettingsStore } from '../../stores';
import DownloadButton from '../Common/DownloadButton';
import type { Task, AgentType } from '../../types';

interface WorkspaceProps { title?: string; subtitle?: string; agent?: AgentType; }

function taskToSteps(task: Task | undefined): TimelineStep[] {
  if (!task) return [];
  const steps: TimelineStep[] = [{ label: '文件读取完成', status: 'done' }, { label: '任务内容分析', status: 'done' }];
  if (task.status === 'processing') steps.push({ label: task.current_step || '正在处理...', status: 'running' }, { label: '输出结果文件', status: 'pending' });
  else if (task.status === 'completed') steps.push({ label: '处理完成', status: 'done' }, { label: '输出结果文件', status: 'done' });
  else if (task.status === 'failed') steps.push({ label: '处理失败', status: 'pending', description: task.error });
  else steps.push({ label: '等待处理', status: 'pending' }, { label: '输出结果文件', status: 'pending' });
  return steps;
}

const agents = [
  { path: '/word', index: '01', title: 'Word Agent', description: '整理结构、统一格式并交付可编辑文档', icon: FileText, tone: 'agent-word' },
  { path: '/ppt', index: '02', title: 'PPT Agent', description: '规划叙事、生成内容并完成演示设计', icon: Presentation, tone: 'agent-ppt' },
  { path: '/excel', index: '03', title: 'Excel Agent', description: '清洗数据、提炼结论并生成图表', icon: Sheet, tone: 'agent-excel' },
];

export default function Workspace({ title = '工作台', subtitle = '智能办公助手', agent = 'auto' }: WorkspaceProps) {
  const navigate = useNavigate();
  const { messages, sending, sendMessage, setAgent } = useChatStore();
  const { tasks, startPolling, stopPolling } = useTaskStore();
  const defaultAgent = useSettingsStore((s) => s.settings.default_agent);
  const [localSteps, setLocalSteps] = useState<TimelineStep[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // 回形针 → 触发常驻上传区的文件选择框（跟进消息也能附加文件）
  const openUploaderRef = useRef<(() => void) | null>(null);

  // 工作台（agent='auto'）使用用户设置的默认 Agent，其余页面用各自的 Agent
  const effectiveAgent: AgentType = agent === 'auto' ? (defaultAgent ?? 'auto') : agent;

  useEffect(() => { startPolling(); return () => stopPolling(); }, [startPolling, stopPolling]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  // 页面切换时同步当前 Agent 模式
  useEffect(() => { setAgent(effectiveAgent); }, [effectiveAgent, setAgent]);

  const currentTask = tasks.find(t => t.status === 'processing' || t.status === 'pending');
  const displaySteps = currentTask ? taskToSteps(currentTask) : localSteps;
  const isDashboard = agent === 'auto';

  const handleSend = async (message: string) => {
    setLocalSteps([{ label: '理解任务需求', status: 'running' }, { label: '选择合适的 Agent', status: 'pending' }, { label: '执行处理', status: 'pending' }, { label: '输出结果', status: 'pending' }]);
    await sendMessage(message);
    setLocalSteps([]);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-bg min-w-0">
      <div className="flex-1 overflow-y-auto px-7 pb-4 pt-7">
        <div className="mx-auto max-w-[1040px] space-y-5">
          {messages.length === 0 && !currentTask && (
            <>
              <section className="welcome-banner">
                <div className="welcome-copy">
                  <h1 className="display-face">{isDashboard ? '把一份文件，变成可交付的成果。' : title}</h1>
                  <p>{isDashboard ? '选择一个主文件，说明你想要的结果。系统会在本机完成解析、执行与版本留存。' : subtitle}</p>
                </div>
                <ol className="workflow-ledger" aria-label="任务流程">
                  <li><span>01</span><strong>输入</strong><small>上传主文件</small></li>
                  <li><span>02</span><strong>执行</strong><small>选择专业 Agent</small></li>
                  <li><span>03</span><strong>交付</strong><small>下载与版本留存</small></li>
                </ol>
              </section>

              {isDashboard && (
                <section className="agent-register" aria-labelledby="agent-register-title">
                  <div className="register-heading">
                    <div><h2 id="agent-register-title">专业执行席位</h2><p>按交付物类型进入专属工作流</p></div>
                    <span>3 个专业席位</span>
                  </div>
                  {agents.map((agent) => {
                    const Icon = agent.icon;
                    return (
                      <button key={agent.path} onClick={() => navigate(agent.path)} className="agent-row">
                        <span className="agent-index">{agent.index}</span>
                        <div className={`agent-icon ${agent.tone}`}><Icon className="w-5 h-5" /></div>
                        <div className="agent-copy min-w-0 text-left">
                          <p>{agent.title}</p>
                          <span>{agent.description}</span>
                        </div>
                        <ArrowRight className="agent-arrow" aria-hidden="true" />
                      </button>
                    );
                  })}
                </section>
              )}

            </>
          )}

          <section className="surface-panel p-4">
            <div className="section-heading mb-3">
              <span>01</span>
              <div><h2>添加任务文件</h2><p>文件上传成功后将自动附加到下一条任务</p></div>
            </div>
            <FileUploader registerOpen={(fn) => { openUploaderRef.current = fn; }} />
          </section>

          {messages.length > 0 && (
            <section className="surface-panel min-h-[420px] p-6">
              <div className="section-heading mb-6 border-b border-line pb-5">
                <span>02</span><div><h1>任务对话</h1><p>{subtitle}</p></div>
              </div>
              <div className="space-y-5">
                {messages.map(msg => (
                  <div key={msg.id} className={`flex gap-3 animate-fade-in ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                    <div className={`message-avatar ${msg.role === 'user' ? 'message-avatar-user' : 'message-avatar-ai'}`}>{msg.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}</div>
                    <div aria-live={msg.role === 'assistant' ? 'polite' : undefined} className={`message-bubble max-w-[78%] px-4 py-3 text-sm leading-relaxed ${msg.role === 'user' ? 'message-bubble-user' : 'message-bubble-ai'}`}>
                      <p className="whitespace-pre-wrap">{msg.content}</p>
                      {msg.role === 'assistant' && msg.output_files && msg.output_files.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {msg.output_files.map((file) => (
                            <DownloadButton key={file.file_id} fileId={file.file_id} filename={file.filename} />
                          ))}
                        </div>
                      )}
                      {msg.role === 'assistant' && msg.task_status === 'processing' && <div className="mt-3 h-1.5 bg-muted rounded-full overflow-hidden"><div className="h-full bg-brand rounded-full transition-all duration-500" style={{ width: `${msg.task_progress || 0}%` }} /></div>}
                    </div>
                  </div>
                ))}
                {sending && !messages.some(m => m.task_status === 'processing') && <div className="flex gap-3"><div className="message-avatar message-avatar-ai"><Bot className="w-4 h-4" /></div><div className="bg-muted border border-line rounded-lg px-4 py-3 flex gap-1"><span className="typing-dot" /><span className="typing-dot [animation-delay:150ms]" /><span className="typing-dot [animation-delay:300ms]" /></div></div>}
                <div ref={messagesEndRef} />
              </div>
            </section>
          )}

          {displaySteps.length > 0 && <TaskTimeline steps={displaySteps} title={currentTask ? '任务执行中' : '正在处理'} />}
        </div>
      </div>

      <div className="composer-dock flex-shrink-0 px-7 pb-3 pt-2">
        <div className="mx-auto max-w-[1040px]"><ChatInput onSend={handleSend} disabled={sending} onAttach={() => openUploaderRef.current?.()} /></div>
      </div>
    </div>
  );
}
