import { useState, useRef, useEffect } from 'react';
import { UploadCloud, FileText, X, CheckCircle2 } from 'lucide-react';
import { isTauri, onTauriDragDrop, readLocalFile } from '../../services/tauri';
import { isAllowedFile, MAX_FILE_SIZE } from '../../services/api';
import { useFileStore } from '../../stores';

interface UploadedFileInfo { key: string; fileId?: string; name: string; size: number; status: 'uploading' | 'done' | 'error'; progress: number; error?: string; }

interface FileUploaderProps {
  /** 注册"打开文件选择框"的触发器，供聊天输入框回形针按钮调用 */
  registerOpen?: (open: () => void) => void;
}

export default function FileUploader({ registerOpen }: FileUploaderProps) {
  const { uploadFiles, attachFile, detachFile, attachedFileIds, templateFileId, setTemplateFile } = useFileStore();
  const [dragOver, setDragOver] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const keyCounterRef = useRef(0);
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());

  useEffect(() => {
    registerOpen?.(() => fileInputRef.current?.click());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registerOpen]);

  // sendMessage 接受任务后会清空附件；同步移除本地的“已附加”芯片，
  // 避免界面显示已附加、实际请求却没有 file_id。
  useEffect(() => {
    setUploadedFiles((prev) => prev.filter((file) =>
      file.status !== 'done' || !file.fileId
      || attachedFileIds.includes(file.fileId) || templateFileId === file.fileId
    ));
  }, [attachedFileIds, templateFileId]);

  const handleUploadFile = async (file: File) => {
    // 客户端预校验：HTML5 accept 只约束点击选择，拖拽路径完全绕过
    if (!isAllowedFile(file.name)) {
      keyCounterRef.current += 1;
      const key = `${file.name}:${file.size}:${file.lastModified}:${keyCounterRef.current}`;
      setUploadedFiles(prev => [...prev, { key, name: file.name, size: file.size, status: 'error', progress: 0, error: '不支持的文件类型' }]);
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      keyCounterRef.current += 1;
      const key = `${file.name}:${file.size}:${file.lastModified}:${keyCounterRef.current}`;
      setUploadedFiles(prev => [...prev, { key, name: file.name, size: file.size, status: 'error', progress: 0, error: `文件超过 ${Math.round(MAX_FILE_SIZE / 1024 / 1024)}MB 上限` }]);
      return;
    }
    keyCounterRef.current += 1;
    const key = `${file.name}:${file.size}:${file.lastModified}:${keyCounterRef.current}`;
    setUploadedFiles(prev => [...prev, { key, name: file.name, size: file.size, status: 'uploading', progress: 0 }]);
    const controller = new AbortController();
    abortControllersRef.current.set(key, controller);
    try {
      const [uploaded] = await uploadFiles([file], (progress) => {
        setUploadedFiles((prev) => prev.map((item) =>
          item.key === key ? { ...item, progress } : item
        ));
      }, controller.signal);
      if (!uploaded?.file_id) throw new Error('Backend未返回file_id');
      // 上传期间用户可能已把该文件从列表移除：此时不再挂到会话
      const stillPresent = abortControllersRef.current.has(key);
      setUploadedFiles(prev => {
        return prev.map(f => f.key === key ? { ...f, fileId: uploaded.file_id, name: uploaded.filename, size: uploaded.size, status: 'done', progress: 100 } : f);
      });
      if (stillPresent) {
        attachFile(uploaded.file_id);
      }
    } catch (error) {
      const aborted = error instanceof Error && /取消|abort/i.test(error.message);
      if (!aborted) {
        const message = error instanceof Error ? error.message : '上传失败';
        setUploadedFiles(prev => prev.map(f => f.key === key ? { ...f, status: 'error', error: message } : f));
      } else {
        setUploadedFiles(prev => prev.filter(f => f.key !== key));
      }
    } finally {
      abortControllersRef.current.delete(key);
    }
  };

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | null = null;
    let disposed = false;
    onTauriDragDrop(async (paths) => {
      for (const path of paths.slice(0, 1)) {
        const data = await readLocalFile(path);
        if (data) {
          const name = path.split(/[\\/]/).pop() || 'file';
          // 必须构造真正的 File；给 Blob 挂 name 属性后 FormData 仍会把文件名
          // 发送成 "blob"，后端因此无法识别扩展名。
          const file = new File([data as BlobPart], name, { lastModified: Date.now() });
          await handleUploadFile(file);
        }
      }
    }).then(fn => {
      // 卸载早于 promise resolve 时立即注销，避免监听器泄漏/重复上传
      if (disposed) { fn?.(); } else { unlisten = fn; }
    });
    return () => { disposed = true; unlisten?.(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadFiles, attachFile]);

  const formatSize = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  return (
    <div className="w-full">
      <div
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={async (e) => { e.preventDefault(); setDragOver(false); for (const f of Array.from(e.dataTransfer.files)) await handleUploadFile(f); }}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
        role="button"
        tabIndex={0}
        aria-label="选择或拖放任务文件"
        className={`upload-zone ${dragOver ? 'upload-zone-active' : ''}`}
      >
        <input ref={fileInputRef} type="file" accept=".docx,.pptx,.xlsx,.pdf,.txt,.md,.csv,.json,.xml,.html,.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg" onClick={(e) => { e.currentTarget.value = ''; }} onChange={async (e) => { const file = e.target.files?.[0]; if (file) await handleUploadFile(file); }} className="hidden" />
        <div className="upload-icon"><UploadCloud className="w-5 h-5" /></div>
        <div className="min-w-0 text-left">
          <p className="text-sm font-semibold text-fg">拖放文件到这里，或点击上传</p>
          <p className="text-xs text-fg-muted mt-1">每次附加 1 个主文件；支持 DOCX、XLSX、PPTX、PDF</p>
        </div>
      </div>

      {uploadedFiles.length > 0 && (
        <div className="mt-3 space-y-2">
          {uploadedFiles.map(file => (
            <div key={file.key} className="file-chip-row">
              <div className="w-8 h-8 rounded-md bg-brand-soft flex items-center justify-center"><FileText className="w-4 h-4 text-brand" /></div>
              <div className="flex-1 min-w-0"><p className="text-xs font-medium text-fg truncate">{file.name}</p><p className="text-xs text-fg-muted mt-0.5">{formatSize(file.size)}</p></div>
              {file.status === 'done' ? <CheckCircle2 className="w-4 h-4 text-ok" aria-label="上传完成" /> : file.status === 'error' ? <span className="max-w-48 truncate text-xs text-danger" role="alert" title={file.error}>{file.error || '上传失败'}</span> : <span className="text-xs text-brand">上传中 {file.progress}%</span>}
              {file.status === 'done' && file.name.toLowerCase().endsWith('.pptx') && (
                <button
                  onClick={(e) => { e.stopPropagation(); setTemplateFile(templateFileId === file.fileId ? null : (file.fileId ?? null)); }}
                  className={`px-2 py-1 text-xs rounded ${templateFileId === file.fileId ? 'bg-brand-soft text-brand' : 'bg-muted text-fg-soft hover:bg-muted'}`}
                  title="将上传的 PPT 作为模板，按其配色/字体/版式生成"
                >
                  {templateFileId === file.fileId ? '模板中' : '设为模板'}
                </button>
              )}
              <button onClick={(e) => { e.stopPropagation(); abortControllersRef.current.get(file.key)?.abort(); abortControllersRef.current.delete(file.key); if (file.fileId) detachFile(file.fileId); if (templateFileId === file.fileId) setTemplateFile(null); setUploadedFiles(prev => prev.filter(f => f.key !== file.key)); }} className="text-fg-faint hover:text-fg-soft" title="移除" aria-label={`移除 ${file.name}`}><X className="w-4 h-4" /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
