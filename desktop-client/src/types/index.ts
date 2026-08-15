// OfficeAgent Desktop Client - 类型定义
// 与后端 OfficeAgent v0.49.0 API 对齐

// Agent类型
export type AgentType = 'auto' | 'word' | 'ppt' | 'excel' | 'workflow';

// 任务状态（后端状态映射到前端状态）
export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';

// 健康检查响应
export interface HealthResponse {
  success: boolean;
  data?: {
    status: string;
    version: string;
    uptime_seconds: number;
    environment: string;
    checks: {
      api?: { status: string };
      database?: { status: string };
      redis?: { status: string };
      storage?: { status: string; upload_count?: number; output_count?: number };
      workers?: { status: string; active_tasks?: number };
      models?: { status: string; primary?: string };
    };
  };
  message?: string;
}

// 任务步骤
export interface TaskStep {
  step: number;
  name: string;
  status: TaskStatus;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
}

// 输出文件
export interface OutputFile {
  file_id: string;
  name: string;
  path?: string;
  size: number;
  url?: string;
}

// 任务结果
export interface TaskResult {
  summary?: string;
  files?: OutputFile[];
  data?: Record<string, unknown>;
  auto_revision_task_id?: string;
  quality_revision?: {
    task_id: string;
    issues?: Array<Record<string, unknown>>;
    model_call?: Record<string, unknown>;
  };
  quality_check?: {
    passed: boolean;
    score: number;
    reports: Array<Record<string, unknown>>;
  };
  model_call?: {
    called: boolean;
    success: boolean;
    model?: string;
    provider?: string;
    fallback_used?: boolean;
    attempts?: number;
    error?: string;
  };
}

// 任务
// 任务输出文件（后端统一契约）
export interface TaskOutputFile {
  file_id: string;
  filename: string;
  download_url: string;
}

export interface Task {
  id: string;
  type: string;
  agent: string;
  status: TaskStatus;
  progress: number;
  current_step?: string;
  steps: TaskStep[];
  output_files?: TaskOutputFile[];
  result?: TaskResult;
  error?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  parent_task_id?: string;
  revision_number?: number;
}

// 上传的文件
export interface UploadedFile {
  file_id: string;
  filename: string;
  file_type: string;
  size: number;
  uploaded_at: string;
  // Compatibility aliases used by existing file list components.
  id: string;
  name: string;
  type: string;
  path?: string;
}

// 聊天消息
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  agent?: AgentType;
  files?: UploadedFile[];
  task_id?: string;
  task_status?: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  task_progress?: number;
  task_step?: string;
  task_result?: Record<string, unknown>;
  task_error?: string;
  is_follow_up?: boolean;
  parent_task_id?: string;
  revision_mode?: 'new_task' | 'add' | 'modify' | 'remove' | 'revert';
  revision_number?: number;
}

// 模型配置
export interface ModelConfig {
  provider: string;
  model: string;
  api_key: string;
  enabled: boolean;
  base_url?: string;
}

// 发送给后端的模型配置载荷（仅非敏感字段，禁止携带 api_key）
export interface ModelConfigPayload {
  provider: string;
  model: string;
}

// 应用设置
export interface AppSettings {
  backend_url: string;
  default_agent: AgentType;
  models: ModelConfig[];
  auto_open_results: boolean;
  notifications: boolean;
  autostart: boolean;
  minimize_to_tray: boolean;
}

// 聊天请求
export interface ChatRequest {
  message: string;
  agent?: AgentType;
  file_ids?: string[];
  conversation_id?: string;
  history?: Array<{ role: string; content: string }>;
  model_config?: ModelConfigPayload;
}

// 聊天响应
export interface ChatResponse {
  response: string;
  task_id?: string;
  conversation_id?: string;
  agent?: string;
  is_follow_up?: boolean;
  parent_task_id?: string;
  revision_mode?: 'new_task' | 'add' | 'modify' | 'remove' | 'revert';
  revision_number?: number;
}

export interface FileVersionInfo {
  version_id: string;
  version_number: number;
  file_size: number;
  file_hash?: string;
  change_description?: string;
  changed_by?: string;
  created_at?: string;
}

// 创建任务请求
export interface CreateTaskRequest {
  type: string;
  input: string;
  files?: string[];
  options?: Record<string, unknown>;
}
