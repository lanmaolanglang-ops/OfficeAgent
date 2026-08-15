import { useState, useRef, useEffect } from 'react';
import { Paperclip, SlidersHorizontal, ArrowUp } from 'lucide-react';

interface ChatInputProps {
  onSend: (message: string) => void;
  onAttach?: () => void;
  placeholder?: string;
  disabled?: boolean;
}

export default function ChatInput({ onSend, onAttach, placeholder, disabled }: ChatInputProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
          }}
          placeholder={placeholder || '描述你的需求，例如：帮我整理这份报告并统一格式'}
          rows={1}
          disabled={disabled}
          className="w-full bg-transparent text-[#29334b] text-sm placeholder-[#a6adba] resize-none border-0 focus:outline-none px-1 py-1.5 max-h-36 leading-relaxed"
        />
        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-1">
            <button onClick={onAttach} className="composer-tool" title="添加附件"><Paperclip className="w-4 h-4" /></button>
            <button className="composer-tool" title="任务选项"><SlidersHorizontal className="w-4 h-4" /></button>
          </div>
          <button onClick={handleSubmit} disabled={!input.trim() || disabled} className="send-button" title="发送">
            <ArrowUp className="w-[17px] h-[17px]" />
          </button>
        </div>
      </div>
      <p className="text-center text-[10px] text-[#a2a9b7] mt-2">本地运行 · 文件仅在你的设备上处理</p>
    </div>
  );
}
