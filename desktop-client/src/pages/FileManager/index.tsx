import { useState, useEffect } from 'react';
import {
  FolderOpen, FileText, Presentation, Sheet, File,
  Download, Trash2, Search, History, RotateCcw, X,
} from 'lucide-react';
import { useFileStore } from '../../stores';
import { deleteFile, getFileUrl, listFileVersions, restoreFileVersion } from '../../services/api';
import type { FileVersionInfo } from '../../types';

function FileIcon({ name }: { name: string }) {
  const ext = name.split('.').pop()?.toLowerCase();
  const className = 'w-5 h-5';
  switch (ext) {
    case 'docx': case 'doc': return <FileText className={`${className} text-blue-400`} />;
    case 'pptx': case 'ppt': return <Presentation className={`${className} text-orange-400`} />;
    case 'xlsx': case 'xls': case 'csv': return <Sheet className={`${className} text-green-400`} />;
    case 'pdf': return <File className={`${className} text-red-400`} />;
    default: return <File className={`${className} text-gray-500`} />;
  }
}

function formatSize(bytes: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

export default function FileManager() {
  const { files, loadFiles } = useFileStore();
  const [search, setSearch] = useState('');
  const [versionFile, setVersionFile] = useState<{ id: string; name: string } | null>(null);
  const [versions, setVersions] = useState<FileVersionInfo[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  const filteredFiles = files.filter((f) => f.name?.toLowerCase().includes(search.toLowerCase()));

  const handleDelete = async (id: string) => {
    try { await deleteFile(id); loadFiles(); } catch { /* ignore */ }
  };

  const handleDownload = (id: string) => {
    window.open(getFileUrl(id), '_blank');
  };

  const openVersions = async (id: string, name: string) => {
    setVersionFile({ id, name }); setLoadingVersions(true);
    try { setVersions(await listFileVersions(id)); } finally { setLoadingVersions(false); }
  };

  const handleRestore = async (version: number) => {
    if (!versionFile || !window.confirm(`确定恢复 ${versionFile.name} 到 v${version} 吗？`)) return;
    setRestoring(true);
    try { await restoreFileVersion(versionFile.id, version); await loadFiles(); setVersionFile(null); }
    finally { setRestoring(false); }
  };

  return (
    <div className="flex flex-col h-full bg-[#111111]">
      <div className="px-8 py-6 flex-shrink-0">
        <h1 className="text-2xl font-semibold text-white tracking-tight">文件中心</h1>
        <p className="text-sm text-gray-500 mt-1">管理上传和生成的文件</p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-3xl space-y-4">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-600" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索文件..."
              className="w-full pl-11 pr-4 py-3 bg-[#181818] border border-[#2A2A2A] rounded-xl text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-indigo-500/50 transition-colors"
            />
          </div>

          {/* File list */}
          {filteredFiles.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-gray-600">
              <FolderOpen className="w-12 h-12 mb-3" />
              <p className="text-sm">暂无文件</p>
            </div>
          ) : (
            <div className="bg-[#181818] rounded-xl border border-[#2A2A2A] overflow-hidden">
              <div className="px-4 py-3 border-b border-[#2A2A2A]">
                <span className="text-xs text-gray-500">共 {filteredFiles.length} 个文件</span>
              </div>
              <div className="divide-y divide-[#2A2A2A]">
                {filteredFiles.map((file) => (
                  <div key={file.id} className="flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition-colors">
                    <FileIcon name={file.name} />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-gray-200 truncate">{file.name}</p>
                      <p className="text-xs text-gray-600">
                        {formatSize(file.size)} · {new Date(file.uploaded_at || Date.now()).toLocaleString('zh-CN')}
                      </p>
                    </div>
                    <div className="flex items-center gap-1">
                      <button onClick={() => handleDownload(file.id)} className="p-2 text-gray-500 hover:text-indigo-400 hover:bg-white/5 rounded-lg transition-colors" title="下载">
                        <Download className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleDelete(file.id)} className="p-2 text-gray-500 hover:text-red-400 hover:bg-white/5 rounded-lg transition-colors" title="删除">
                        <Trash2 className="w-4 h-4" />
                      </button>
                      <button onClick={() => openVersions(file.id, file.name)} className="p-2 text-gray-500 hover:text-indigo-400 hover:bg-white/5 rounded-lg transition-colors" title="历史版本">
                        <History className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      {versionFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-lg rounded-xl border border-[#333] bg-[#181818] p-5 shadow-2xl">
            <div className="flex items-center justify-between mb-4"><div><h2 className="text-white font-medium">历史版本</h2><p className="text-xs text-gray-500 mt-1">{versionFile.name}</p></div><button onClick={() => setVersionFile(null)} className="text-gray-500 hover:text-white"><X className="w-4 h-4" /></button></div>
            {loadingVersions ? <p className="text-sm text-gray-500 py-8 text-center">正在加载...</p> : versions.length === 0 ? <p className="text-sm text-gray-500 py-8 text-center">暂无历史版本</p> : <div className="space-y-2 max-h-80 overflow-y-auto">{versions.slice().reverse().map((version) => <div key={version.version_id} className="flex items-center gap-3 rounded-lg border border-[#2A2A2A] px-3 py-3"><div className="flex-1"><p className="text-sm text-gray-200">v{version.version_number}</p><p className="text-xs text-gray-600">{version.change_description || '文件版本'} {version.created_at ? `· ${new Date(version.created_at).toLocaleString('zh-CN')}` : ''}</p></div><button disabled={restoring} onClick={() => handleRestore(version.version_number)} className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-indigo-300 hover:bg-indigo-500/10 disabled:opacity-50"><RotateCcw className="w-3 h-3" />恢复</button></div>)}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
