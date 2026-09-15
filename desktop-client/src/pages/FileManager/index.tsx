import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  FolderOpen, FileText, Presentation, Sheet, File,
  Trash2, Search, History, RotateCcw, X, ArchiveRestore,
} from 'lucide-react';
import { useFileStore } from '../../stores';
import {
  deleteFile, listDeletedFilesPage, listFileVersions,
  restoreDeletedFile, restoreFileVersion,
} from '../../services/api';
import type { FileVersionInfo, UploadedFile } from '../../types';
import DownloadButton from '../../components/Common/DownloadButton';

type FileView = 'active' | 'trash';

function FileIcon({ name }: { name: string }) {
  const ext = name.split('.').pop()?.toLowerCase();
  const className = 'w-5 h-5 flex-none';
  switch (ext) {
    case 'docx': return <FileText className={`${className} text-[#496478]`} />;
    case 'pptx': return <Presentation className={`${className} text-accent`} />;
    case 'xlsx': case 'csv': return <Sheet className={`${className} text-ok`} />;
    case 'pdf': return <File className={`${className} text-danger`} />;
    default: return <File className={`${className} text-fg-soft`} />;
  }
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value?: string): string {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '时间未知' : date.toLocaleString('zh-CN');
}

export default function FileManager() {
  const { files, loadFiles } = useFileStore();
  const [view, setView] = useState<FileView>('active');
  const [trashFiles, setTrashFiles] = useState<UploadedFile[]>([]);
  const [trashTotal, setTrashTotal] = useState(0);
  const [trashLoading, setTrashLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [versionFile, setVersionFile] = useState<{ id: string; name: string } | null>(null);
  const [versions, setVersions] = useState<FileVersionInfo[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [pendingFileId, setPendingFileId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [versionError, setVersionError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => { void loadFiles(); }, [loadFiles]);

  const loadTrash = useCallback(async () => {
    setTrashLoading(true);
    try {
      const pageSize = 200;
      const allFiles: UploadedFile[] = [];
      let page = 1;
      let total = 0;
      do {
        const result = await listDeletedFilesPage({ page, page_size: pageSize });
        allFiles.push(...result.files);
        total = result.total;
        if (!result.files.length) break;
        page += 1;
      } while (allFiles.length < total);
      setTrashFiles(allFiles);
      setTrashTotal(total);
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : '无法加载回收站，请重试' });
    } finally {
      setTrashLoading(false);
    }
  }, []);

  useEffect(() => { if (view === 'trash') void loadTrash(); }, [view, loadTrash]);

  const visibleFiles = view === 'active' ? files : trashFiles;
  const filteredFiles = useMemo(() => {
    const term = search.trim().toLocaleLowerCase('zh-CN');
    return term ? visibleFiles.filter((file) => file.name?.toLocaleLowerCase('zh-CN').includes(term)) : visibleFiles;
  }, [search, visibleFiles]);

  const changeView = (nextView: FileView) => {
    setView(nextView);
    setSearch('');
    setNotice(null);
  };

  const handleSoftDelete = async (id: string, name: string) => {
    if (!window.confirm(`将“${name}”移至回收站吗？之后可以恢复。`)) return;
    setNotice(null);
    setPendingFileId(id);
    try {
      await deleteFile(id);
      await loadFiles();
      setNotice({ kind: 'success', text: `“${name}”已移至回收站` });
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : '删除失败，请重试' });
    } finally { setPendingFileId(null); }
  };

  const handleRestoreDeleted = async (id: string, name: string) => {
    setNotice(null);
    setPendingFileId(id);
    try {
      await restoreDeletedFile(id);
      await Promise.all([loadTrash(), loadFiles()]);
      setNotice({ kind: 'success', text: `“${name}”已恢复到文件中心` });
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : '恢复失败，请重试' });
    } finally { setPendingFileId(null); }
  };

  const handlePermanentDelete = async (id: string, name: string) => {
    if (!window.confirm(`永久删除“${name}”？此操作会清除文件及全部历史版本，且无法撤销。`)) return;
    setNotice(null);
    setPendingFileId(id);
    try {
      await deleteFile(id, true);
      await loadTrash();
      setNotice({ kind: 'success', text: `“${name}”已永久删除` });
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : '永久删除失败，请重试' });
    } finally { setPendingFileId(null); }
  };

  const closeVersions = useCallback(() => {
    setVersionFile(null);
    window.setTimeout(() => restoreFocusRef.current?.focus(), 0);
  }, []);

  const openVersions = async (id: string, name: string) => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    setVersionFile({ id, name });
    setLoadingVersions(true);
    setVersionError(null);
    setVersions([]);
    try { setVersions(await listFileVersions(id)); }
    catch (error) { setVersionError(error instanceof Error ? error.message : '无法加载历史版本'); }
    finally { setLoadingVersions(false); }
  };

  useEffect(() => {
    if (!versionFile) return;
    const dialog = dialogRef.current;
    const focusableSelector = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    window.setTimeout(() => dialog?.querySelector<HTMLElement>(focusableSelector)?.focus(), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeVersions(); return; }
      if (event.key !== 'Tab' || !dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector));
      if (!focusable.length) { event.preventDefault(); dialog.focus(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [versionFile, closeVersions]);

  const handleRestoreVersion = async (version: number) => {
    if (!versionFile || !window.confirm(`确定恢复“${versionFile.name}”到 v${version} 吗？当前内容会保留为一个历史版本。`)) return;
    setRestoring(true);
    setVersionError(null);
    try { await restoreFileVersion(versionFile.id, version); await loadFiles(); closeVersions(); }
    catch (error) { setVersionError(error instanceof Error ? error.message : '恢复版本失败'); }
    finally { setRestoring(false); }
  };

  const count = view === 'active' ? files.length : trashTotal;
  const emptyTitle = search ? '没有匹配的文件' : view === 'active' ? '文件中心还是空的' : '回收站是空的';
  const emptyDescription = search
    ? '调整关键词后重试，文件名支持中英文和特殊字符。'
    : view === 'active'
      ? '在工作台上传或生成文件后，会在这里统一归档。'
      : '软删除的文件会保留在这里，可恢复或永久清除。';

  return (
    <div className="flex h-full min-w-0 flex-col bg-bg">
      <header className="page-header flex flex-shrink-0 items-end justify-between gap-6">
        <div className="min-w-0">
          <h1 className="text-fg">文件中心</h1>
          <p className="mt-1 text-sm text-fg-soft">管理本机文件、历史版本与可恢复删除</p>
        </div>
        <div className="flex border border-line-strong bg-card p-1" role="tablist" aria-label="文件视图">
          <button type="button" role="tab" aria-selected={view === 'active'} onClick={() => changeView('active')} className={`min-h-9 px-4 text-xs font-semibold transition-colors ${view === 'active' ? 'bg-sidebar text-sidebar-strong' : 'text-fg-soft hover:bg-muted hover:text-fg'}`}>文件</button>
          <button type="button" role="tab" aria-selected={view === 'trash'} onClick={() => changeView('trash')} className={`flex min-h-9 items-center gap-2 px-4 text-xs font-semibold transition-colors ${view === 'trash' ? 'bg-sidebar text-sidebar-strong' : 'text-fg-soft hover:bg-muted hover:text-fg'}`}><Trash2 className="h-3.5 w-3.5" />回收站{trashTotal > 0 ? ` · ${trashTotal}` : ''}</button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-4xl space-y-4">
          <div className="relative">
            <Search className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" aria-hidden="true" />
            <input aria-label={view === 'active' ? '搜索文件' : '搜索回收站文件'} type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={view === 'active' ? '按文件名搜索…' : '在回收站中搜索…'} className="h-11 w-full border border-line-strong bg-card pl-11 pr-4 text-sm text-fg placeholder:text-fg-faint focus:border-brand focus:outline-none" />
          </div>

          {notice && (
            <div role={notice.kind === 'error' ? 'alert' : 'status'} className={`flex items-center justify-between gap-4 border px-4 py-3 text-sm ${notice.kind === 'error' ? 'border-danger/30 bg-danger-soft text-danger' : 'border-ok/30 bg-ok-soft text-ok'}`}>
              <span className="min-w-0 break-words">{notice.text}</span>
              {notice.kind === 'error' && view === 'trash' && <button type="button" onClick={() => void loadTrash()} className="flex-none font-semibold underline underline-offset-4">重试</button>}
            </div>
          )}

          {trashLoading && view === 'trash' ? (
            <div role="status" className="border border-line bg-card px-5 py-16 text-center text-sm text-fg-soft">正在核对回收站…</div>
          ) : filteredFiles.length === 0 ? (
            <div className="flex min-h-64 flex-col items-center justify-center border border-line bg-card px-6 text-center text-fg-muted">
              {view === 'trash' ? <ArchiveRestore className="mb-4 h-10 w-10" /> : <FolderOpen className="mb-4 h-10 w-10" />}
              <h2 className="text-base font-semibold text-fg">{emptyTitle}</h2>
              <p className="mt-2 max-w-md text-sm leading-6 text-fg-soft">{emptyDescription}</p>
              {search && <button type="button" onClick={() => setSearch('')} className="mt-5 text-sm font-semibold text-brand underline underline-offset-4">清除搜索</button>}
            </div>
          ) : (
            <section className="border border-line bg-card" aria-label={view === 'active' ? '文件列表' : '回收站文件列表'}>
              <div className="flex items-center justify-between border-b border-line px-4 py-3 text-xs text-fg-soft"><span>{search ? `找到 ${filteredFiles.length} 个结果` : `共 ${count} 个文件`}</span>{view === 'trash' && <span>永久删除后不可恢复</span>}</div>
              <div className="divide-y divide-line">
                {filteredFiles.map((file) => {
                  const busy = pendingFileId === file.id;
                  return (
                    <article key={file.id} className="flex min-w-0 items-center gap-3 px-4 py-3 transition-colors hover:bg-muted">
                      <FileIcon name={file.name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-fg" title={file.name}>{file.name}</p>
                        <p className="mt-0.5 truncate text-xs text-fg-muted">{formatSize(file.size)} · {view === 'trash' ? `删除于 ${formatDate(file.deleted_at)}` : formatDate(file.uploaded_at)}</p>
                      </div>
                      <div className="flex flex-none items-center gap-1">
                        {view === 'active' ? (
                          <>
                            <DownloadButton fileId={file.id} filename={file.name} compact />
                            <button type="button" onClick={() => void openVersions(file.id, file.name)} className="p-2 text-fg-soft transition-colors hover:bg-muted hover:text-brand" title="历史版本" aria-label={`查看 ${file.name} 的历史版本`}><History className="h-4 w-4" /></button>
                            <button type="button" onClick={() => void handleSoftDelete(file.id, file.name)} disabled={busy} className="p-2 text-fg-soft transition-colors hover:bg-danger-soft hover:text-danger disabled:cursor-wait disabled:opacity-40" title={busy ? '正在移至回收站' : '移至回收站'} aria-label={`将 ${file.name} 移至回收站`}><Trash2 className="h-4 w-4" /></button>
                          </>
                        ) : (
                          <>
                            <button type="button" onClick={() => void handleRestoreDeleted(file.id, file.name)} disabled={busy} className="flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold text-brand transition-colors hover:bg-brand-soft disabled:cursor-wait disabled:opacity-40" aria-label={`恢复 ${file.name}`}><RotateCcw className="h-3.5 w-3.5" />恢复</button>
                            <button type="button" onClick={() => void handlePermanentDelete(file.id, file.name)} disabled={busy} className="flex min-h-9 items-center gap-1.5 px-3 text-xs font-semibold text-danger transition-colors hover:bg-danger-soft disabled:cursor-wait disabled:opacity-40" aria-label={`永久删除 ${file.name}`}><Trash2 className="h-3.5 w-3.5" />永久删除</button>
                          </>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      </main>

      {versionFile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="version-dialog-title" tabIndex={-1} className="w-full max-w-lg border border-line-strong bg-card p-5 shadow-2xl">
            <div className="mb-4 flex min-w-0 items-start justify-between gap-4"><div className="min-w-0"><h2 id="version-dialog-title" className="text-base font-semibold text-fg">历史版本</h2><p className="mt-1 truncate text-xs text-fg-soft" title={versionFile.name}>{versionFile.name}</p></div><button type="button" onClick={closeVersions} className="flex-none p-2 text-fg-soft hover:bg-muted hover:text-fg" aria-label="关闭历史版本"><X className="h-4 w-4" /></button></div>
            {versionError ? <div role="alert" className="border border-danger/30 bg-danger-soft px-3 py-3 text-sm text-danger">{versionError}</div>
              : loadingVersions ? <p role="status" className="py-8 text-center text-sm text-fg-soft">正在加载历史版本…</p>
                : versions.length === 0 ? <p className="py-8 text-center text-sm text-fg-soft">暂无历史版本</p>
                  : <div className="max-h-80 divide-y divide-line overflow-y-auto border-y border-line">{versions.slice().reverse().map((version) => <div key={version.version_id} className="flex items-center gap-3 py-3"><div className="min-w-0 flex-1"><p className="text-sm font-semibold text-fg">v{version.version_number}</p><p className="mt-0.5 truncate text-xs text-fg-muted">{version.change_description || '文件版本'} {version.created_at ? `· ${formatDate(version.created_at)}` : ''}</p></div><button type="button" disabled={restoring} onClick={() => void handleRestoreVersion(version.version_number)} className="flex min-h-9 items-center gap-1 px-3 text-xs font-semibold text-brand hover:bg-brand-soft disabled:opacity-50"><RotateCcw className="h-3.5 w-3.5" />恢复</button></div>)}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
