// Tauri API helpers - 动态加载，浏览器环境有回退

// 检测是否在Tauri环境
export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

// 动态导入Tauri核心
async function getTauriCore() {
  if (!isTauri()) return null;
  try {
    return await import('@tauri-apps/api/core');
  } catch {
    return null;
  }
}

// 动态导入Tauri事件
async function getTauriEvent() {
  if (!isTauri()) return null;
  try {
    return await import('@tauri-apps/api/event');
  } catch {
    return null;
  }
}

// 动态导入Tauri窗口
async function getTauriWindow() {
  if (!isTauri()) return null;
  try {
    const mod = await import('@tauri-apps/api/window');
    return mod;
  } catch {
    return null;
  }
}

// ========== 文件对话框 ==========
export async function saveFileDialog(
  defaultPath?: string,
  filters?: { name: string; extensions: string[] }[]
): Promise<string | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try {
    const dialog = await import('@tauri-apps/plugin-dialog');
    const result = await dialog.save({ defaultPath, filters });
    return result as string | null;
  } catch {
    return null;
  }
}

// ========== 文件操作 ==========
export async function readLocalFile(path: string): Promise<Uint8Array | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try {
    const fs = await import('@tauri-apps/plugin-fs');
    return await fs.readFile(path);
  } catch {
    return null;
  }
}

export async function readTextFile(path: string): Promise<string | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try {
    const fs = await import('@tauri-apps/plugin-fs');
    return await fs.readTextFile(path);
  } catch {
    return null;
  }
}

export async function fileExists(path: string): Promise<boolean> {
  const core = await getTauriCore();
  if (!core) return false;
  try {
    return await core.invoke('file_exists', { path });
  } catch {
    return false;
  }
}

export async function getFileInfo(path: string) {
  const core = await getTauriCore();
  if (!core) return null;
  try {
    return await core.invoke('get_file_info', { path });
  } catch {
    return null;
  }
}

export async function listDirectory(path: string): Promise<string[] | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try {
    return await core.invoke('list_directory', { path });
  } catch {
    return null;
  }
}

export function showInFolder(path: string): void {
  if (isTauri()) {
    getTauriCore().then(core => { core?.invoke('show_in_folder', { path }); });
  }
}

export function openFilePath(path: string): void {
  if (isTauri()) {
    getTauriCore().then(core => { core?.invoke('open_file_path', { path }); });
  } else {
    window.open('file:///' + path, '_blank');
  }
}

export function openPath(path: string): void {
  openFilePath(path);
}

// ========== 路径获取 ==========
export async function getAppDataDir(): Promise<string | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try { return await core.invoke('get_app_data_dir'); } catch { return null; }
}

export async function getDownloadDir(): Promise<string | null> {
  const core = await getTauriCore();
  if (!core) return null;
  try { return await core.invoke('get_download_dir'); } catch { return null; }
}

// ========== 通知 ==========
export async function requestNotificationPermission(): Promise<boolean> {
  if (isTauri()) {
    try {
      const notification = await import('@tauri-apps/plugin-notification');
      return (await notification.requestPermission()) === 'granted';
    } catch { return false; }
  }
  if ('Notification' in window) {
    return (await Notification.requestPermission()) === 'granted';
  }
  return false;
}

export async function isNotificationPermissionGranted(): Promise<boolean> {
  if (isTauri()) {
    try {
      const notification = await import('@tauri-apps/plugin-notification');
      return await notification.isPermissionGranted();
    } catch { return false; }
  }
  return 'Notification' in window && Notification.permission === 'granted';
}

export function sendNotification(title: string, body: string): Promise<void> {
  if (isTauri()) {
    return getTauriCore().then(async core => {
      try {
        await core?.invoke('send_notification', { title, body });
      } catch {
        const notification = await import('@tauri-apps/plugin-notification');
        notification.sendNotification({ title, body });
      }
    });
  } else if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body });
  }
  return Promise.resolve();
}

export function showNotification(title: string, body: string): Promise<void> {
  return sendNotification(title, body);
}

// ========== 开机启动 ==========
export async function setAutoStart(enabled: boolean): Promise<boolean> {
  const core = await getTauriCore();
  if (!core) return false;
  try {
    return await core.invoke('set_autostart', { enabled });
  } catch {
    try {
      const autostart = await import('@tauri-apps/plugin-autostart');
      if (enabled) await autostart.enable(); else await autostart.disable();
      return true;
    } catch { return false; }
  }
}

export async function getAutoStart(): Promise<boolean> {
  const core = await getTauriCore();
  if (!core) return false;
  try {
    return await core.invoke('get_autostart');
  } catch {
    try {
      const autostart = await import('@tauri-apps/plugin-autostart');
      return await autostart.isEnabled();
    } catch { return false; }
  }
}

// ========== 窗口控制 ==========
export async function minimizeWindow(): Promise<void> {
  const winMod = await getTauriWindow();
  if (winMod) await winMod.getCurrentWindow().minimize();
}

export async function toggleMaximize(): Promise<void> {
  const winMod = await getTauriWindow();
  if (winMod) {
    const win = winMod.getCurrentWindow();
    if (await win.isMaximized()) await win.unmaximize(); else await win.maximize();
  }
}

export async function closeWindow(): Promise<void> {
  const winMod = await getTauriWindow();
  if (winMod) await winMod.getCurrentWindow().close();
}

export async function showMainWindow(): Promise<void> {
  const core = await getTauriCore();
  if (core) { try { await core.invoke('show_main_window'); return; } catch {} }
  const winMod = await getTauriWindow();
  if (winMod) { const win = winMod.getCurrentWindow(); await win.show(); await win.setFocus(); }
}

export async function quitApp(): Promise<void> {
  const core = await getTauriCore();
  if (core) { try { await core.invoke('quit_app'); return; } catch {} }
}

// ========== 窗口行为设置 ==========
// 同步"关闭时最小化到托盘"到 Rust 侧（CloseRequested 时 Rust 读取该状态）
export async function setMinimizeToTray(enabled: boolean): Promise<void> {
  const core = await getTauriCore();
  if (!core) return;
  try {
    await core.invoke('set_minimize_to_tray', { enabled });
  } catch {
    // 浏览器环境或旧版本后端：忽略
  }
}

// ========== 拖拽事件 ==========
export async function onTauriDragDrop(
  handler: (paths: string[]) => void
): Promise<(() => void) | null> {
  const eventMod = await getTauriEvent();
  if (!eventMod) return null;
  try {
    // Rust 侧过滤扩展名后以 office-agent://drag-drop 转发（数组 payload）。
    // 不监听核心的 tauri://drag-drop：它与 Rust 转发的是同一次拖拽，
    // 两个都听会导致一次拖拽上传两遍。
    return await eventMod.listen<string[]>('office-agent://drag-drop', (event) => {
      const payload = event.payload as { paths?: string[] } | string[];
      const paths = Array.isArray(payload) ? payload : (payload.paths ?? []);
      handler(paths);
    });
  } catch { return null; }
}
