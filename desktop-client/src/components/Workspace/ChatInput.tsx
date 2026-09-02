import { useState, useRef, useEffect } from 'react';
import { Paperclip, ArrowUp } from 'lucide-react';
import { useChatStore } from '../../stores';
import type { AgentType } from '../../types';

interface ChatInputProps {
  onSend: (message: string) => void;
  onAttach?: () => void;
  placeholder?: string;
  disabled?: boolean;
}

const AGENT_OPTIONS: { value: AgentType; label: string }[] = [
  { value: 'auto', label: '自动识别' },
  { value: 'word', label: 'Word 排版' },
  { value: 'ppt', label: 'PPT 生成' },
  { value: 'excel', label: 'Excel 分析' },
];

export default function ChatInput({ onSend, onAttach, placeholder, disabled }: ChatInputProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { currentAgent, setAgent } = useChatStore();

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 140) + 'px';
    }
  }, [input]);

  const handleSubmit = () => {
    if (!input.trim() || disabled) return;
    onSend(input.trim());
    setInput('');
  };

  return (
    <div className="w-full">
      <div className="chat-composer">
        <textarea
          aria-label="任务说明"
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
          }}
          placeholder={placeholder || '描述你的需求，例如：帮我整理这份报告并统一格式'}
          rows={1}
          disabled={disabled}
          className="w-full max-h-36 resize-none border-0 bg-transparent px-1 py-1.5 text-sm leading-relaxed text-fg placeholder-fg-faint focus:outline-none"
        />
        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-1">
            <button onClick={onAttach} className="composer-tool" title="添加附件" aria-label="添加附件"><Paperclip className="w-4 h-4" /></button>
            <select
              value={currentAgent}
              onChange={(e) => setAgent(e.target.value as AgentType)}
              className="composer-agent-select"
              title="选择处理方式"
              aria-label="选择处理方式"
            >
              {AGENT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <button onClick={handleSubmit} disabled={!input.trim() || disabled} className="send-button" title="发送" aria-label="发送消息">
            <ArrowUp className="w-[17px] h-[17px]" />
          </button>
        </div>
      </div>
      <p className="composer-privacy-note mt-1.5 text-center text-[11px] text-fg-faint">文件存储在本机 · 模型请求按当前配置发送</p>
    </div>
  );
}
