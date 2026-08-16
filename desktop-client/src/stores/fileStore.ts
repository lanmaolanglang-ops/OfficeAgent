import { create } from 'zustand';
import type { UploadedFile } from '../types';
import { uploadFiles as apiUploadFiles, listFiles as apiListFiles } from '../services/api';

interface FileState {
  files: UploadedFile[];
  attachedFileIds: string[];
  templateFileId: string | null;
  uploading: boolean;
  uploadProgress: number;
  uploadFiles: (files: File[]) => Promise<UploadedFile[]>;
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

  uploadFiles: async (fileList: File[]) => {
    set({ uploading: true, uploadProgress: 0 });
    try {
      const uploaded = await apiUploadFiles(fileList);
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
      const files = await apiListFiles({ page: 1, page_size: 20 });
      set({ files });
    } catch {
      // ignore
    }
  },

  attachFile: (fileId: string) => {
    set((state) => state.files.some((file) => file.id === fileId || file.file_id === fileId)
      ? { attachedFileIds: state.attachedFileIds.includes(fileId)
          ? state.attachedFileIds
          : [...state.attachedFileIds, fileId] }
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
    set({ attachedFileIds: [] });
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
