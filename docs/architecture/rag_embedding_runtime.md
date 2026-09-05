# RAG Semantic Embedding 运行时说明

Office Agent 的 RAG 检索使用真正的 semantic embedding，不再使用
`HashingEmbedder` 哈希词袋作为默认语义来源。

## 桌面发行路径

桌面 frozen 构建不打包 `torch` / `transformers`，因此默认不安装本地
`sentence-transformers`。正常桌面用户需要在设置页配置一个
OpenAI-compatible Embedding Provider：

- provider: 任意 OpenAI-compatible 服务（例如 OpenAI、火山引擎、通义千问
  或其他自定义 `/embeddings` 端点）
- base_url: 服务地址
- model: embedding 模型名
- api_key: 访问密钥

配置保存在统一数据目录的 `embedding_config.json`，API Key 使用与语言模型、
生图模型相同的 Fernet 加密，不落明文。

## 本地开发/可选后端

安装 `.[semantic]` 后，`create_semantic_embedder()` 会优先使用本地
`sentence-transformers`（默认 `paraphrase-multilingual-MiniLM-L12-v2`），
模型由 sentence-transformers 按需缓存到其默认缓存目录；未缓存时首次使用
需要联网下载，之后可离线复用。该路径不写入 frozen installer。

## 未配置行为

未配置 Embedding Provider 且未安装 `semantic` extra 时，RAG 任务会返回
明确失败：

> RAG 语义检索后端未配置。请在设置中配置 Embedding Provider，或安装 semantic 依赖。

不会静默退回 HashingEmbedder，也不会把旧 hashing 向量与 semantic 向量混合。
