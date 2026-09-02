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

export const useFileStore = create<FileState>((set) => ({
  files: [],
  attachedFileIds: [],
  templateFileId: null,
  uploading: false,
  uploadProgress: 0,

  uploadFiles: async (fileList: File[], onProgress?: (p: number) => void, signal?: AbortSignal) => {
    set({ uploading: true, uploadProgress: 0 });
    try {
      const uploaded = await Promise.all(fileList.map((f) => apiUploadFile(f, onProgress, signal)));
      set((state) => ({
        files: [...state.files, ...uploaded],
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

  attachFile: (fileId: string) => {
    set((state) => state.files.some((file) => file.id === fileId || file.file_id === fileId)
      ? { attachedFileIds: [fileId] }
      : state);
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
