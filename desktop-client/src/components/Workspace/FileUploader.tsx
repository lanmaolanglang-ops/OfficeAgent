import { useState, useRef, useEffect } from 'react';
import { UploadCloud, FileText, X, CheckCircle2 } from 'lucide-react';
import { isTauri, onTauriDragDrop, readLocalFile } from '../../services/tauri';
import { useFileStore } from '../../stores';

interface UploadedFileInfo { key: string; fileId?: string; name: string; size: number; status: 'uploading' | 'done' | 'error'; progress: number; error?: string; }

export default function FileUploader() {
  const { uploadFiles, attachFile, detachFile, templateFileId, setTemplateFile } = useFileStore();
  const [dragOver, setDragOver] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleUploadFile = async (file: File) => {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    setUploadedFiles(prev => [...prev, { key, name: file.name, size: file.size, status: 'uploading', progress: 0 }]);
    try {
      const [uploaded] = await uploadFiles([file]);
      if (!uploaded?.file_id) throw new Error('Backend未返回file_id');
      attachFile(uploaded.file_id);
      setUploadedFiles(prev => prev.map(f => f.key === key ? { ...f, fileId: uploaded.file_id, name: uploaded.filename, size: uploaded.size, status: 'done', progress: 100 } : f));
    } catch (error) {
      const message = error instanceof Error ? error.message : '上传失败';
      setUploadedFiles(prev => prev.map(f => f.key === key ? { ...f, status: 'error', error: message } : f));
    }
  };

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | null = null;
    onTauriDragDrop(async (paths) => {
      for (const path of paths) {
        const data = await readLocalFile(path);
        if (data) {
          const name = path.split(/[\\/]/).pop() || 'file';
          const file = Object.assign(new Blob([data as BlobPart]), { name }) as unknown as File;
          await handleUploadFile(file);
        }
      }
    }).then(fn => { unlisten = fn; });
    return () => { unlisten?.(); };
  }, [uploadFiles, attachFile]);

  const formatSize = (bytes: number) => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  return (
    <div className="w-full">
      <div
        onClick={() => fileInputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={async (e) => { e.preventDefault(); setDragOver(false); for (const f of Array.from(e.dataTransfer.files)) await handleUploadFile(f); }}
        className={`upload-zone ${dragOver ? 'upload-zone-active' : ''}`}
      >
        <input ref={fileInputRef} type="file" multiple accept=".docx,.doc,.pptx,.ppt,.xlsx,.xls,.pdf,.txt,.md,.csv,.json,.xml,.html,.png,.jpg,.jpeg,.gif,.bmp,.webp,.svg" onChange={async (e) => { if (e.target.files) for (const f of Array.from(e.target.files)) await handleUploadFile(f); }} className="hidden" />
        <div className="upload-icon"><UploadCloud className="w-5 h-5" /></div>
        <div className="min-w-0 text-left">
          <p className="text-sm font-semibold text-[#35405a]">拖放文件到这里，或点击上传</p>
          <p className="text-[11px] text-[#98a1b3] mt-1">支持 Word、Excel、PPT、PDF</p>
        </div>
      </div>

      {uploadedFiles.length > 0 && (
        <div className="mt-3 space-y-2">
          {uploadedFiles.map(file => (
            <div key={file.key} className="file-chip-row">
              <div className="w-8 h-8 rounded-md bg-[#edf3ff] flex items-center justify-center"><FileText className="w-4 h-4 text-[#4978ee]" /></div>
              <div className="flex-1 min-w-0"><p className="text-xs font-medium text-[#35405a] truncate">{file.name}</p><p className="text-[10px] text-[#9ba3b1] mt-0.5">{formatSize(file.size)}</p></div>
              {file.status === 'done' ? <CheckCircle2 className="w-4 h-4 text-[#22b573]" /> : file.status === 'error' ? <span className="text-[10px] text-red-500" title={file.error}>失败</span> : <span className="text-[10px] text-[#4f73e8]">上传中</span>}
              {file.status === 'done' && (file.name.toLowerCase().endsWith('.pptx') || file.name.toLowerCase().endsWith('.ppt')) && (
                <button
                  onClick={(e) => { e.stopPropagation(); setTemplateFile(templateFileId === file.fileId ? null : (file.fileId ?? null)); }}
                  className={`px-2 py-1 text-[10px] rounded ${templateFileId === file.fileId ? 'bg-indigo-500/20 text-indigo-500' : 'bg-[#eef1f6] text-[#59647a] hover:bg-[#e3e8f0]'}`}
                  title="将上传的 PPT 作为模板，按其配色/字体/版式生成"
                >
                  {templateFileId === file.fileId ? '模板中' : '设为模板'}
                </button>
              )}
              <button onClick={(e) => { e.stopPropagation(); if (file.fileId) detachFile(file.fileId); if (templateFileId === file.fileId) setTemplateFile(null); setUploadedFiles(prev => prev.filter(f => f.key !== file.key)); }} className="text-[#a6adba] hover:text-[#59647a]" title="移除"><X className="w-4 h-4" /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
