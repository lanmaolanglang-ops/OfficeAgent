import type {
  HealthResponse,
  Task,
  ChatRequest,
  ChatResponse,
  UploadedFile,
  FileVersionInfo,
} from '../types';
import { getAuthHeaders, handleUnauthorized } from './auth';

const DEFAULT_BASE_URL = 'http://127.0.0.1:8765';

// 允许的文件扩展名
export const ALLOWED_EXTENSIONS = [
  '.docx',
  '.pptx',
  '.xlsx',
  '.pdf',
  '.txt', '.md', '.csv', '.json', '.xml', '.html',
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg',
];

// 与后端 StorageService 默认上限保持一致
export const MAX_FILE_SIZE = 100 * 1024 * 1024;

// 检查文件是否允许上传
export function isAllowedFile(filename: string): boolean {
  const ext = '.' + filename.split('.').pop()?.toLowerCase();
  return ALLOWED_EXTENSIONS.includes(ext);
}

// 格式化文件大小
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return (bytes / Math.pow(k, i)).toFixed(1) + ' ' + sizes[i];
}

/** 当前生效的后端地址（设置页与本组件展示用） */
export function getBackendUrl(): string {
  return getBaseUrl();
}

function getBaseUrl(): string {
  try {
    return localStorage.getItem('backend_url') || DEFAULT_BASE_URL;
  } catch {
    return DEFAULT_BASE_URL;
  }
}

function getBackendError(payload: unknown, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const body = payload as Record<string, unknown>;
    if (typeof body.detail === 'string') return body.detail;
    if (typeof body.message === 'string') return body.message;
  }
  return fallback;
}

// 请求默认超时：本地后端被阻塞时 fetch 会无限挂起，
// 必须有超时上限，否则轮询链永久卡住、发送按钮锁死
const DEFAULT_REQUEST_TIMEOUT_MS = 15000;

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS
): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
        // 认证头最后合并：调用方自定义 header 不得覆盖安全头。
        // 无凭据时为空对象（本地免认证模式）。
        ...getAuthHeaders(),
      },
      signal: options.signal ?? controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error(`请求超时（${Math.round(timeoutMs / 1000)}s）：后端未响应`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401) {
    // 凭据已失效：立即作废，避免后续请求继续携带旧凭据。
    handleUnauthorized();
    throw new Error('HTTP 401: 认证失败，请重新提供凭据');
  }

  if (!response.ok) {
    const errorText = await response.text().catch(() => 'Unknown error');
    let message = errorText;
    try {
      message = getBackendError(JSON.parse(errorText), errorText);
    } catch {
      // Keep the Backend response text when it is not JSON.
    }
    throw new Error(`HTTP ${response.status}: ${message}`);
  }

  return response.json();
}

// 健康检查 - /health 端点直接返回状态对象，不是 {success, data} 格式
export async function checkHealth(): Promise<HealthResponse> {
  const url = `${getBaseUrl()}/health`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  let response: Response;
  try {
    response = await fetch(url, { signal: controller.signal });
  } catch {
    throw new Error('后端不可达');
  } finally {
    clearTimeout(timer);
  }
  // 503 = 后端在线但整体降级；只有网络失败/非 503 错误才视为不可达
  if (!response.ok && response.status !== 503) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  // 适配两种格式：直接返回 或 {success, data} 包装
  if (data.success !== undefined) {
    return data as HealthResponse;
  }
  return { success: data.status === 'healthy', data } as HealthResponse;
}
// 模型配置状态（GET/POST /api/settings/model）
export interface ModelItem {
  id: string;
  provider: string;
  model: string;
  display_name: string;
  api_key_mask: string;
  is_default: boolean;
}

export interface ModelSettingsStatus {
  configured: boolean;
  default_model_id?: string | null;
  models: ModelItem[];
}

// 获取当前本地模型配置状态（已保存模型列表 + 默认模型）
export async function getModelSettings(): Promise<ModelSettingsStatus> {
  return await request<ModelSettingsStatus>('/api/settings/model');
}

// 切换默认模型（已保存的 Key 无缝切换，无需重新输入）
export async function setDefaultModel(modelId: string): Promise<ModelSettingsStatus> {
  return await request<ModelSettingsStatus>('/api/settings/model/default', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  });
}

export interface ModelConnectionTest {
  success: boolean;
  model_id: string;
  latency_ms: number;
  message: string;
}

export async function testModelConnection(modelId: string): Promise<ModelConnectionTest> {
  return await request<ModelConnectionTest>(
    `/api/settings/model/${encodeURIComponent(modelId)}/test`,
    { method: 'POST' },
    75_000,
  );
}

// 保存本地模型配置（API Key 仅写入后端应用数据目录的加密配置）
export async function saveModelSettings(payload: {
  provider: string;
  model: string;
  api_key: string;
  base_url?: string;
}): Promise<ModelSettingsStatus> {
  return await request<ModelSettingsStatus>('/api/settings/model', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// 生图模型配置（GET/POST /api/settings/image-model）
export interface ImageModelSettings {
  configured: boolean;
  provider: string;
  model: string;
  base_url: string;
  mcp_url: string;
  api_key_mask: string;
}

export interface ImageModelConnectionTest {
  success: boolean;
  provider: string;
  model: string;
  latency_ms: number;
  bytes?: number;
  message: string;
}

// 获取生图模型配置
export async function getImageModelSettings(): Promise<ImageModelSettings> {
  return await request<ImageModelSettings>('/api/settings/image-model');
}

// 保存生图模型配置（api_key 留空表示保留已保存的 Key）
export async function saveImageModelSettings(payload: {
  provider: string;
  model: string;
  api_key: string;
  base_url?: string;
  mcp_url?: string;
}): Promise<ImageModelSettings> {
  return await request<ImageModelSettings>('/api/settings/image-model', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// 真实生成并立即删除一张测试图；由用户显式触发，可能产生一次计费
export async function testImageModelConnection(): Promise<ImageModelConnectionTest> {
  return await request<ImageModelConnectionTest>(
    '/api/settings/image-model/test',
    { method: 'POST' },
    150_000,
  );
}

// Embedding 模型配置（GET/POST /api/settings/embedding-model）
export interface EmbeddingModelSettings {
  configured: boolean;
  provider: string;
  model: string;
  base_url: string;
  api_key_mask: string;
}

export interface EmbeddingModelConnectionTest {
  success: boolean;
  provider: string;
  model: string;
  dimension?: number;
  message: string;
}

export async function getEmbeddingModelSettings(): Promise<EmbeddingModelSettings> {
  return await request<EmbeddingModelSettings>('/api/settings/embedding-model');
}

export async function saveEmbeddingModelSettings(payload: {
  provider?: string;
  model: string;
  api_key: string;
  base_url?: string;
}): Promise<EmbeddingModelSettings> {
  return await request<EmbeddingModelSettings>('/api/settings/embedding-model', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function testEmbeddingModelConnection(): Promise<EmbeddingModelConnectionTest> {
  return await request<EmbeddingModelConnectionTest>(
    '/api/settings/embedding-model/test',
    { method: 'POST' },
    75_000,
  );
}

// 上传文件（支持进度回调与中止信号）
export async function uploadFile(
  file: File,
  onProgress?: (progress: number) => void,
  signal?: AbortSignal
): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable && onProgress) {
        const progress = Math.round((e.loaded / e.total) * 100);
        onProgress(progress);
      }
    });

    xhr.addEventListener('load', () => {
      let result: Record<string, unknown>;
      try {
        result = JSON.parse(xhr.responseText) as Record<string, unknown>;
      } catch {
        reject(new Error(`上传失败: Backend返回了无效响应 (HTTP ${xhr.status})`));
        return;
      }

      if (xhr.status === 401) {
        handleUnauthorized();
        reject(new Error('上传失败: 认证失败，请重新提供凭据'));
        return;
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`上传失败: ${getBackendError(result, `HTTP ${xhr.status}`)}`));
        return;
      }

      const data = (result.data && typeof result.data === 'object'
        ? result.data
        : result) as Record<string, unknown>;
      const fileId = data.file_id;
      if (result.success === false || typeof fileId !== 'string' || !fileId) {
        reject(new Error(`上传失败: ${getBackendError(result, 'Backend未返回file_id')}`));
        return;
      }

      const filename = typeof data.filename === 'string' ? data.filename : file.name;
      const fileType = typeof data.file_type === 'string' ? data.file_type : file.type;
      const size = typeof data.size === 'number' ? data.size : file.size;
      resolve({
        file_id: fileId,
        filename,
        file_type: fileType,
        size,
        uploaded_at: typeof data.uploaded_at === 'string' ? data.uploaded_at : new Date().toISOString(),
        id: fileId,
        name: filename,
        type: fileType,
      });
    });

    xhr.addEventListener('error', () => {
      reject(new Error('Upload failed: Network error'));
    });

    xhr.addEventListener('abort', () => {
      reject(new Error('上传已取消'));
    });

    if (signal) {
      if (signal.aborted) { xhr.abort(); }
      else { signal.addEventListener('abort', () => xhr.abort(), { once: true }); }
    }

    // 大文件在回环地址上传很快，10 分钟足以覆盖 100MB 上限的最差情况
    xhr.timeout = 10 * 60 * 1000;
    xhr.addEventListener('timeout', () => {
      reject(new Error('上传超时，请检查后端服务'));
    });

    xhr.open('POST', `${getBaseUrl()}/api/file/upload`);
    // 与 request() 共用同一凭据来源；multipart 不设置 Content-Type，
    // 由浏览器补 boundary。无凭据时不加头（本地免认证模式）。
    for (const [name, value] of Object.entries(getAuthHeaders())) {
      xhr.setRequestHeader(name, value);
    }
    xhr.send(formData);
  });
}

export async function uploadFiles(files: File[]): Promise<UploadedFile[]> {
  return Promise.all(files.map((f) => uploadFile(f)));
}

// 获取任务
export async function getTask(taskId: string): Promise<Task> {
  const result = await request<{ success: boolean; data?: Record<string, unknown> }>(
    `/api/task/${taskId}`
  );
  return mapBackendTask(result.data || {});
}

// 分页列出任务（P5-6：后端 /api/task/ 已返回 total/page/page_size）
export interface TaskListPage {
  tasks: Task[];
  total: number;
  page: number;
  page_size: number;
}

export async function listTasksPage(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<TaskListPage> {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.page) query.set('page', String(params.page));
  if (params?.page_size) query.set('page_size', String(params.page_size));

  const qs = query.toString();
  const path = qs ? `/api/task/?${qs}` : '/api/task/';

  const result = await request<{
    success: boolean;
    data?: {
      tasks?: Array<Record<string, unknown>>;
      total?: number;
      page?: number;
      page_size?: number;
    };
  }>(path);

  const tasks = (result.data?.tasks || []).map(mapBackendTask);
  return {
    tasks,
    total: result.data?.total ?? tasks.length,
    page: result.data?.page ?? params?.page ?? 1,
    page_size: result.data?.page_size ?? params?.page_size ?? 50,
  };
}

// 兼容只需要单页数组的调用方
export async function listTasks(params?: {
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<Task[]> {
  return (await listTasksPage(params)).tasks;
}

// 发送聊天消息
export async function sendChatMessage(req: ChatRequest): Promise<ChatResponse> {
  const result = await request<{
    success: boolean;
    data?: Record<string, unknown>;
  }>('/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      message: req.message,
      agent_hint: req.agent,
      file_ids: req.file_ids,
      template_file_id: req.template_file_id,
      conversation_id: req.conversation_id,
      context: {
        ...(req.model_config ? { model_config: req.model_config } : {}),
        ...(req.history ? { history: req.history } : {}),
      },
    }),
  });

  const data = result.data || {};
  return {
    message: (data.message as string) || (data.response as string) || '',
    task_id: data.task_id as string | undefined,
    conversation_id: data.conversation_id as string | undefined,
    agent: data.agent as string | undefined,
    is_follow_up: data.is_follow_up as boolean | undefined,
    parent_task_id: data.parent_task_id as string | undefined,
    revision_mode: data.revision_mode as ChatResponse['revision_mode'],
    revision_number: data.revision_number as number | undefined,
  };
}

// 获取文件下载URL
export function getFileUrl(fileId: string): string {
  return `${getBaseUrl()}/api/file/download/${fileId}`;
}

export async function listFileVersions(fileId: string): Promise<FileVersionInfo[]> {
  const result = await request<{ data?: { versions?: FileVersionInfo[] } }>(
    `/api/file/${fileId}/versions`
  );
  return result.data?.versions || [];
}

export async function restoreFileVersion(fileId: string, version: number): Promise<UploadedFile> {
  const result = await request<{ data?: UploadedFile }>(
    `/api/file/${fileId}/versions/${version}/restore`, { method: 'POST' }
  );
  if (!result.data) throw new Error('版本恢复失败');
  return result.data;
}

export interface FileListPage {
  files: UploadedFile[];
  total: number;
  page: number;
  page_size: number;
}

function mapBackendFile(f: Record<string, unknown>): UploadedFile {
  return {
    file_id: (f.file_id as string) || (f.id as string) || '',
    filename: (f.filename as string) || (f.name as string) || '',
    file_type: (f.file_type as string) || '',
    size: (f.size as number) || 0,
    id: (f.file_id as string) || (f.id as string) || '',
    name: (f.filename as string) || (f.name as string) || '',
    type: (f.file_type as string) || '',
    uploaded_at: (f.upload_time as string) || (f.created_at as string) || '',
    deleted_at: (f.deleted_at as string) || undefined,
  };
}

// 分页列出文件
export async function listFilesPage(params?: {
  file_type?: string;
  page?: number;
  page_size?: number;
}): Promise<FileListPage> {
  const query = new URLSearchParams();
  if (params?.file_type) query.set('file_type', params.file_type);
  if (params?.page) query.set('page', String(params.page));
  if (params?.page_size) query.set('page_size', String(params.page_size));

  const qs = query.toString();
  const path = qs ? `/api/file/?${qs}` : '/api/file/';

  const result = await request<{
    success: boolean;
    data?: {
      files?: Array<Record<string, unknown>>;
      total?: number;
      page?: number;
      page_size?: number;
    };
  }>(path);

  const files = (result.data?.files || []).map(mapBackendFile);
  return {
    files,
    total: result.data?.total ?? files.length,
    page: result.data?.page ?? params?.page ?? 1,
    page_size: result.data?.page_size ?? params?.page_size ?? 50,
  };
}

// 兼容只需要单页数组的调用方
export async function listFiles(params?: {
  file_type?: string;
  page?: number;
  page_size?: number;
}): Promise<UploadedFile[]> {
  return (await listFilesPage(params)).files;
}

// 删除文件
export async function deleteFile(fileId: string, permanent = false): Promise<void> {
  const suffix = permanent ? '?permanent=true' : '';
  await request(`/api/file/${fileId}${suffix}`, { method: 'DELETE' });
}

// 分页列出回收站文件
export async function listDeletedFilesPage(params?: {
  page?: number;
  page_size?: number;
}): Promise<FileListPage> {
  const query = new URLSearchParams();
  if (params?.page) query.set('page', String(params.page));
  if (params?.page_size) query.set('page_size', String(params.page_size));
  const qs = query.toString();
  const result = await request<{
    success: boolean;
    data?: {
      files?: Array<Record<string, unknown>>;
      total?: number;
      page?: number;
      page_size?: number;
    };
  }>(qs ? `/api/file/trash?${qs}` : '/api/file/trash');
  const files = (result.data?.files || []).map(mapBackendFile);
  return {
    files,
    total: result.data?.total ?? files.length,
    page: result.data?.page ?? params?.page ?? 1,
    page_size: result.data?.page_size ?? params?.page_size ?? 50,
  };
}

export async function restoreDeletedFile(fileId: string): Promise<void> {
  await request(`/api/file/${fileId}/restore`, { method: 'POST' });
}

// 映射后端任务数据到前端Task
export function mapBackendTask(data: Record<string, unknown>): Task {
  const statusMap: Record<string, Task['status']> = {
    pending: 'pending',
    queued: 'pending',
    running: 'processing',
    processing: 'processing',
    success: 'completed',
    completed: 'completed',
    failed: 'failed',
    error: 'failed',
    cancelled: 'cancelled',
    canceled: 'cancelled',
  };

  const backendStatus = (data.status as string) || 'pending';
  const status = statusMap[backendStatus] || 'pending';
  const progress = (data.progress as number) ?? (status === 'completed' ? 100 : 0);

  return {
    id: (data.task_id as string) || (data.id as string) || '',
    type: (data.task_type as string) || 'unknown',
    agent: (data.agent as string) || 'auto',
    status,
    progress,
    current_step: data.current_step as string | undefined,
    steps: (data.steps as Task['steps']) ?? [],
    result: data.result as Task['result'],
    output_files: data.output_files as Task['output_files'],
    error: data.error as string | undefined,
    created_at: (data.created_at as string) || new Date().toISOString(),
    started_at: data.started_at as string | undefined,
    completed_at: data.completed_at as string | undefined,
    parent_task_id: data.parent_task_id as string | undefined,
    revision_number: (data.revision_number as number) || 1,
  };
}
