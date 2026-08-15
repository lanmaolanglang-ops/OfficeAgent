import { create } from 'zustand';
import type { ChatMessage, AgentType, ChatResponse } from '../types';
import { sendChatMessage, getTask, getFileUrl } from '../services/api';
import { useFileStore } from './fileStore';

interface ChatState {
  messages: ChatMessage[];
  sending: boolean;
  currentAgent: AgentType;
  conversationId?: string;
  lastOutputFileId?: string;
  sendMessage: (content: string) => Promise<void>;
  setAgent: (agent: AgentType) => void;
  clearChat: () => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
}

function generateId(): string {
  return Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  sending: false,
  currentAgent: 'auto',

  updateMessage: (id: string, updates: Partial<ChatMessage>) => {
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === id ? { ...m, ...updates } : m
      ),
    }));
  },

  sendMessage: async (content: string) => {
    // 在插入占位消息之前快照历史，避免把“正在理解…”占位内容发给后端
    const history = get().messages
      .filter((m) => m.role === 'user' || (m.role === 'assistant' && m.task_status !== 'pending'))
      .slice(-10)
      .map((m) => ({ role: m.role, content: m.content }));
    const explicitFileIds = [...useFileStore.getState().attachedFileIds];
    const attachedFileIds = explicitFileIds.length > 0
      ? explicitFileIds
      : (get().lastOutputFileId ? [get().lastOutputFileId!] : []);
    const userMessage: ChatMessage = {
      id: generateId(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      agent: get().currentAgent,
    };

    const assistantId = generateId();
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '🤔 正在理解您的需求...',
      timestamp: new Date().toISOString(),
      task_status: 'pending',
      task_progress: 0,
    };

    set((state) => ({
      messages: [...state.messages, userMessage, assistantMessage],
      sending: true,
    }));

    let taskId: string | undefined;

    try {
      // 模型由后端“默认模型”机制决定，前端不再传 model_config
      const response: ChatResponse = await sendChatMessage({
        message: content,
        agent: get().currentAgent,
        file_ids: attachedFileIds,
        conversation_id: get().conversationId,
        history,
      });

      // The Backend accepted the task, so these attachments no longer belong
      // to the next message. Request failures leave the snapshot attached.
      useFileStore.getState().clearAttachments();

      taskId = response.task_id;
      if (response.conversation_id) {
        set({ conversationId: response.conversation_id });
      }
      const agentName = response.agent || '智能助手';

      get().updateMessage(assistantId, {
        content: `✅ 已为您分配 ${agentName} 处理，正在执行中...`,
        task_id: taskId,
        is_follow_up: response.is_follow_up,
        parent_task_id: response.parent_task_id,
        revision_mode: response.revision_mode,
        revision_number: response.revision_number,
        task_status: 'processing',
        task_progress: 5,
      });

      // 如果没有taskId，直接结束
      if (!taskId) {
        get().updateMessage(assistantId, {
          content: response.response || '任务已提交',
          task_status: 'completed',
          task_progress: 100,
        });
        set({ sending: false });
        return;
      }

      // 轮询任务状态
      let pollCount = 0;
      const maxPolls = 120; // 最多轮询 2 分钟
      const pollInterval = 1500;

      const pollTask = () => {
        const poll = async () => {
          try {
            pollCount++;
            const task = await getTask(taskId!);

            // 更新进度
            const updates: Partial<ChatMessage> = {
              task_status: task.status,
              task_progress: task.progress,
              task_step: task.current_step,
            };

            if (task.status === 'processing' || task.status === 'pending') {
              const stepText = task.current_step ? ` - ${task.current_step}` : '';
              updates.content = `🔧 正在处理中... ${task.progress}%${stepText}`;
              get().updateMessage(assistantId, updates);

              if (pollCount < maxPolls) {
                setTimeout(poll, pollInterval);
              } else {
                get().updateMessage(assistantId, {
                  content: '⏰ 任务处理时间较长，请稍后在任务历史中查看结果',
                  task_status: 'processing',
                });
                set({ sending: false });
              }
            } else if (task.status === 'completed' || task.status === 'failed') {
              const result = task.result as Record<string, unknown> | null;

              const autoRevisionTaskId = typeof result?.auto_revision_task_id === 'string'
                ? result.auto_revision_task_id
                : undefined;
              if (autoRevisionTaskId) {
                taskId = autoRevisionTaskId;
                pollCount = 0;
                get().updateMessage(assistantId, {
                  content: '🔄 质量检查未通过，正在自动修订并重新检查...',
                  task_id: autoRevisionTaskId,
                  task_status: 'processing',
                  task_progress: 5,
                });
                setTimeout(poll, pollInterval);
                return;
              }

              if (task.status === 'completed') {
                // 任务完成
                let resultText = '✅ 任务已完成！';

                if (result) {
                  if (result.message) {
                    resultText = `✅ ${result.message}`;
                  }
                  if (result.model_call && typeof result.model_call === 'object') {
                    const call = result.model_call as Record<string, unknown>;
                    const label = typeof call.model === 'string'
                      ? call.model
                      : (typeof call.provider === 'string' ? call.provider : '底层模型');
                    resultText += call.success
                      ? `\n\n模型解析：${label} 已调用`
                      : '\n\n模型解析失败，已使用规则回退';
                  }
                  if (result.model_call && typeof result.model_call === 'object') {
                    const call = result.model_call as Record<string, unknown>;
                    if (call.success === false) {
                      if (typeof call.attempts === 'number') {
                        resultText += `\n重试次数：${call.attempts}`;
                      }
                      if (typeof call.error === 'string' && call.error.trim()) {
                        resultText += `\n失败原因：${call.error.slice(0, 300)}`;
                      }
                    }
                  }
                  if (task.output_files && task.output_files.length > 0) {
                    resultText += '\n\n[输出文件]';
                    for (const f of task.output_files) {
                      resultText += `\n  • ${f.filename}\n     下载: ${getFileUrl(f.file_id)}`;
                    }
                  } else if (result.output_files && Array.isArray(result.output_files) && result.output_files.length > 0) {
                    resultText += '\n\n[生成的文件]';
                    for (const f of result.output_files) {
                      resultText += `\n  • ${f}`;
                    }
                  }
                  if (result.slides) {
                    resultText += `\n📊 共 ${result.slides} 页`;
                  }
                }

                get().updateMessage(assistantId, {
                  content: resultText,
                  task_status: 'completed',
                  task_progress: 100,
                  task_result: result || undefined,
                });
                const resultOutputId = typeof result?.output_file_id === 'string'
                  ? result.output_file_id
                  : undefined;
                const latestOutput = task.output_files?.[0]?.file_id || resultOutputId;
                if (latestOutput) {
                  set({ lastOutputFileId: latestOutput });
                }
                set({ sending: false });
              } else {
                // 任务失败
                const errorMsg = task.error || (result?.error as string) || '未知错误';
                get().updateMessage(assistantId, {
                  content: `❌ 任务失败：${errorMsg}`,
                  task_status: 'failed',
                  task_error: errorMsg,
                });
                set({ sending: false });
              }
            } else if (task.status === 'cancelled') {
              get().updateMessage(assistantId, {
                content: '🚫 任务已取消',
                task_status: 'cancelled',
              });
              set({ sending: false });
            }
          } catch (err) {
            console.error('Poll error:', err);
            if (pollCount < maxPolls) {
              setTimeout(poll, pollInterval * 2);
            } else {
              get().updateMessage(assistantId, {
                content: '⚠️ 无法获取任务状态，请在任务历史中查看',
              });
              set({ sending: false });
            }
          }
        };

        setTimeout(poll, pollInterval);
      };

      pollTask();

    } catch (error) {
      get().updateMessage(assistantId, {
        content: `❌ 错误：${error instanceof Error ? error.message : '未知错误'}`,
        task_status: 'failed',
      });
      set({ sending: false });
    }
  },

  setAgent: (agent: AgentType) => {
    set({ currentAgent: agent });
  },

  clearChat: () => {
    set({ messages: [], conversationId: undefined, lastOutputFileId: undefined });
  },
}));
