import { create } from 'zustand';
import type { UploadedFile } from '../types';
import { uploadFiles as apiUploadFiles, listFiles as apiListFiles } from '../services/api';

interface FileState {
  files: UploadedFile[];
  attachedFileIds: string[];
  uploading: boolean;
  uploadProgress: number;
  uploadFiles: (files: File[]) => Promise<UploadedFile[]>;
  loadFiles: () => Promise<void>;
  attachFile: (fileId: string) => void;
  detachFile: (fileId: string) => void;
  clearAttachments: () => void;
  removeFile: (id: string) => void;
  clearFiles: () => void;
}

export const useFileStore = create<FileState>((set) => ({
  files: [],
  attachedFileIds: [],
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

  clearAttachments: () => {
    set({ attachedFileIds: [] });
  },

  removeFile: (id: string) => {
    set((state) => ({
      files: state.files.filter((f) => f.id !== id),
      attachedFileIds: state.attachedFileIds.filter((fileId) => fileId !== id),
    }));
  },

  clearFiles: () => {
    set({ files: [], attachedFileIds: [], uploadProgress: 0 });
  },
}));
