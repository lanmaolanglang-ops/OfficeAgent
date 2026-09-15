import { useState } from 'react';
import { Check, Download, FolderOpen, Loader2, X } from 'lucide-react';
import { downloadOutputFile } from '../../services/download';
import { openFilePath, showInFolder } from '../../services/tauri';

interface DownloadButtonProps {
  fileId: string;
  filename: string;
  className?: string;
  compact?: boolean;
}

export default function DownloadButton({ fileId, filename, className = '', compact = false }: DownloadButtonProps) {
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'cancelled' | 'error'>('idle');
  const [message, setMessage] = useState('');
  const [path, setPath] = useState<string | null>(null);

  const run = async () => {
    if (state === 'saving') return;
    setState('saving');
    setMessage('正在从 Backend 获取文件…');
    setPath(null);
    try {
      const result = await downloadOutputFile(fileId, filename);
      if (result.status === 'cancelled') {
        setState('cancelled');
        setMessage('已取消保存，原文件未更改');
      } else {
        setState('saved');
        setPath(result.path);
        setMessage(result.path ? '下载完成' : '浏览器下载已开始');
      }
    } catch (error) {
      setState('error');
      const detail = error instanceof Error ? error.message : String(error || '');
      setMessage(detail || '下载失败，请重试');
    }
  };

  const Icon = state === 'saving' ? Loader2 : state === 'saved' ? Check : state === 'error' ? X : Download;
  return (
    <span className={`inline-flex flex-wrap items-center gap-2 ${className}`}>
      <button
        type="button"
        onClick={() => void run()}
        disabled={state === 'saving'}
        aria-label={state === 'saving' ? `正在下载 ${filename}` : `下载 ${filename}`}
        className={compact
          ? 'inline-flex min-h-9 min-w-9 items-center justify-center text-fg-soft transition-colors hover:bg-muted hover:text-brand disabled:cursor-wait disabled:opacity-50'
          : 'inline-flex min-h-10 items-center gap-2 bg-brand px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-brand-hover disabled:cursor-wait disabled:opacity-60'}
      >
        <Icon aria-hidden="true" className={`h-4 w-4 ${state === 'saving' ? 'animate-spin' : ''}`} />
        {!compact && (state === 'saving' ? '正在下载' : state === 'saved' ? '已保存' : '下载')}
      </button>
      {state !== 'idle' && state !== 'saving' && (
        <span role={state === 'error' ? 'alert' : 'status'} className={`text-xs ${state === 'error' ? 'text-danger' : state === 'saved' ? 'text-ok' : 'text-fg-muted'}`}>
          {message}
        </span>
      )}
      {state === 'saved' && path && (
        <span className="inline-flex items-center gap-1">
          <button type="button" onClick={() => openFilePath(path)} className="min-h-9 px-2 text-xs font-semibold text-brand underline underline-offset-4">打开文件</button>
          <button type="button" onClick={() => showInFolder(path)} aria-label={`打开 ${filename} 所在文件夹`} className="inline-grid min-h-9 min-w-9 place-items-center text-brand hover:bg-brand-soft"><FolderOpen aria-hidden="true" className="h-4 w-4" /></button>
        </span>
      )}
    </span>
  );
}
