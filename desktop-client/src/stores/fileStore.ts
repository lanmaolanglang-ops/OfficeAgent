import { create } from 'zustand';
import type { UploadedFile } from '../types';
import { uploadFile as apiUploadFile, listFilesPage as apiListFilesPage } from '../services/api';

interface FileState {
  files: UploadedFile[];
  attachedFileIds: string[];
  templateFileId: string | null;
  uploading: boolean;
  uploadProgress: number;
  uploadFiles: (files: File[], onProgress?: (p: number) => void, signal?: AbortSignal) => Promise<UploadedFile[]>;
  loadFiles: () => Promise<void>;
  attachFile: (fileId: string) => void;
  detachFile: (fileId: string) => void;
  setTemplateFile: (fileId: string | null) => void;
  clearAttachments: () => void;
  removeFile: (id: string) => void;
  clearFiles: () => void;
}

/** 上传结果的稳定标识（后端 file_id 优先）。 */
export function uploadedFileKey(file: UploadedFile): string {
  return file.file_id || file.id || '';
}

/**
 * 合并上传结果到已有列表：按 file_id 去重，重复上传覆盖旧条目而不是追加。
 * （P5-5：修复前是 `[...state.files, ...uploaded]`，同一文件重复上传会累积出多条。）
 */
export function mergeUploadedFiles(
  existing: UploadedFile[],
  uploaded: UploadedFile[],
): UploadedFile[] {
  const byId = new Map<string, UploadedFile>();
  for (const file of existing) {
    const key = uploadedFileKey(file);
    if (key) byId.set(key, file);
  }
  for (const file of uploaded) {
    const key = uploadedFileKey(file);
    if (key) byId.set(key, file);
  }
  return Array.from(byId.values());
}

export const useFileStore = create<FileState>((set) => ({
  files: [],
  attachedFileIds: [],
  templateFileId: null,
  uploading: false,
  uploadProgress: 0,

  uploadFiles: async (fileList: File[], onProgress?: (p: number) => void, signal?: AbortSignal) => {
    set({ uploading: true, uploadProgress: 0 });
    const total = fileList.length;
    const progressByIndex = new Map<number, number>();
    // 并发上传时每个文件独立记录进度并折算为整体进度，
    // 避免"最后一个文件的回调覆盖其它文件"（P5-5）。
    const reportProgress = (index: number, value: number) => {
      progressByIndex.set(index, value);
      if (total === 0) return;
      let sum = 0;
      for (let i = 0; i < total; i += 1) sum += progressByIndex.get(i) ?? 0;
      const aggregate = Math.round(sum / total);
      set({ uploadProgress: aggregate });
      onProgress?.(aggregate);
    };
    try {
      const uploaded = await Promise.all(fileList.map((f, index) =>
        apiUploadFile(f, (progress) => reportProgress(index, progress), signal)));
      set((state) => ({
        files: mergeUploadedFiles(state.files, uploaded),
        uploading: false,
        uploadProgress: 100,
      }));
      return uploaded;
    } catch (error) {
      set({ uploading: false, uploadProgress: 0 });
      throw error;
    }
  },

  loadFiles: async () => {
    try {
      const pageSize = 200;
      const files: UploadedFile[] = [];
      let page = 1;
      let total = 0;
      do {
        const result = await apiListFilesPage({ page, page_size: pageSize });
        files.push(...result.files);
        total = result.total;
        if (result.files.length === 0) break;
        page += 1;
      } while (files.length < total);
      set({ files });
    } catch {
      // ignore
    }
  },

  // P5-5：attachFile 原本是 `[fileId]` 覆盖式单选，与"多文件上传"叠加时
  // 只有最后一个会被附加（UI 显示多个 done 却只提交一个 file_id）。
  // 现改为集合累加 + 去重；仍未上传成功的未知 id 不附加。
  attachFile: (fileId: string) => {
    set((state) => {
      const known = state.files.some((file) => file.id === fileId || file.file_id === fileId);
      if (!known || state.attachedFileIds.includes(fileId)) return state;
      return { attachedFileIds: [...state.attachedFileIds, fileId] };
    });
  },

  detachFile: (fileId: string) => {
    set((state) => ({
      attachedFileIds: state.attachedFileIds.filter((id) => id !== fileId),
    }));
  },

  setTemplateFile: (fileId: string | null) => {
    set({ templateFileId: fileId });
  },

  clearAttachments: () => {
    set({ attachedFileIds: [], templateFileId: null });
  },

  removeFile: (id: string) => {
    set((state) => ({
      files: state.files.filter((f) => f.id !== id),
      attachedFileIds: state.attachedFileIds.filter((fileId) => fileId !== id),
      // 移除的正是当前模板文件时，同步清掉模板引用，避免悬空 file_id
      templateFileId: state.templateFileId === id ? null : state.templateFileId,
    }));
  },

  clearFiles: () => {
    set({ files: [], attachedFileIds: [], templateFileId: null, uploadProgress: 0 });
  },
}));
