/**
 * 设置页的供应商 / 模型候选（P5-4 / 审计 M36③）。
 *
 * canonical 供应商名与模型名由后端 `office_agent/models/model_schemas.py`
 * 定义：provider 是 `claude`（`anthropic` 只是 `normalize_provider` 的别名），
 * 这里只列后端目录里真实存在的名称，避免前端再维护一份"未来/虚构版本"清单。
 *
 * 抽成独立模块（而不是放在页面文件里导出）是为了不破坏 React Fast Refresh：
 * 页面文件只导出组件。
 */

export const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  claude: 'Anthropic Claude',
  doubao: '豆包',
  qwen: '通义千问',
  agnes: 'Agnes AI',
};
