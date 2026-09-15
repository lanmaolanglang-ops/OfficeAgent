import { getFileBytes } from './api';
import { getDownloadDir, isTauri, saveFileDialog, writeLocalFile } from './tauri';

export type DownloadResult =
  | { status: 'saved'; path: string | null }
  | { status: 'cancelled'; path: null };

function safeFilename(value: string): string {
  const cleaned = Array.from(String(value || 'download'), (character) => {
    const code = character.charCodeAt(0);
    return code < 32 || '<>:"/\\|?*'.includes(character) ? '_' : character;
  }).join('').trim();
  return cleaned || 'download';
}

function extensionFilter(filename: string) {
  const index = filename.lastIndexOf('.');
  if (index <= 0 || index === filename.length - 1) return undefined;
  return [{ name: `${filename.slice(index + 1).toUpperCase()} 文件`, extensions: [filename.slice(index + 1)] }];
}

export async function downloadOutputFile(fileId: string, filename: string): Promise<DownloadResult> {
  const bytes = await getFileBytes(fileId);
  const cleanName = safeFilename(filename);
  if (isTauri()) {
    const directory = await getDownloadDir();
    const separator = directory?.includes('\\') ? '\\' : '/';
    const defaultPath = directory ? `${directory.replace(/[\\/]$/, '')}${separator}${cleanName}` : cleanName;
    const path = await saveFileDialog(defaultPath, extensionFilter(cleanName));
    if (!path) return { status: 'cancelled', path: null };
    try {
      await writeLocalFile(path, bytes);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      throw new Error(`无法写入所选位置；文件可能正被占用，或该目录不可写。${detail ? ` ${detail}` : ''}`);
    }
    return { status: 'saved', path };
  }

  // TypeScript 6 distinguishes ArrayBuffer from SharedArrayBuffer in BlobPart.
  // Copy the response into a browser-owned ArrayBuffer for the web fallback.
  const browserBuffer = new Uint8Array(bytes.byteLength);
  browserBuffer.set(bytes);
  const blob = new Blob([browserBuffer.buffer]);
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = cleanName;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return { status: 'saved', path: null };
}
