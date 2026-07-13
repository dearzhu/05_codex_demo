# 企业知识库检索系统

基于 RAG（检索增强生成）架构的企业知识库系统，支持多格式文档的智能检索与问答。

---

## 系统架构

```
 ┌───────────┐    ┌───────────┐
 │ Streamlit │    │  FastAPI  │
 │  UI:8501  │───→│ API:8000  │
 └───────────┘    └─────┬─────┘
                        │
              ┌─────────┼─────────┐
              │         │         │
              ▼         ▼         ▼
         ChromaDB    SQLite    LLM API
         (向量库)   (元数据)   (DeepSeek)
              │
              ▼
         PaddleOCR
         (OCR服务:8521)
```

## 快速开始

### 1. 安装依赖

```bash
uv sync
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，修改以下配置：

# LLM API（DeepSeek 示例）
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=deepseek-chat

# Embedding 模型（首次运行会自动下载）
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
```

### 3. 启动 OCR 服务（可选，处理扫描件时需要）

```bash
bash ocr_service/start.sh start     # 启动
bash ocr_service/start.sh status    # 查看状态
bash ocr_service/start.sh stop      # 停止
```

PaddleOCR 首次启动会自动下载模型（约 200MB），耗时 10-30 秒。
后续启动秒级完成。服务运行在 `http://127.0.0.1:8521`。

### 4. 启动 API 服务

```bash
bash start_api.sh
# 或指定端口：
# API_PORT=8765 bash start_api.sh 
```

API 服务默认运行在 `http://127.0.0.1:8000`。

首次启动时自动：
- 创建 SQLite 数据库和表
- 创建默认管理员账户 `admin / admin123`
- 初始化 ChromaDB 向量库

### 5. 启动 Web UI（新终端）

```bash
bash start_ui.sh
```

Web UI 默认运行在 `http://127.0.0.1:8501`。

---

## API 接口

| 方法 | 端点 | 说明 |
|---|---|---|
| `GET` | `/api/v1/health` | 健康检查 |
| `POST` | `/api/v1/auth/register` | 注册 |
| `POST` | `/api/v1/auth/login` | 登录 |
| `POST` | `/api/v1/documents/upload` | 上传文档 |
| `GET` | `/api/v1/documents` | 文档列表 |
| `GET` | `/api/v1/documents/{id}/status` | 处理状态 |
| `DELETE` | `/api/v1/documents/{id}` | 删除文档 |
| `GET` | `/api/v1/tags` | 标签列表 |
| `POST` | `/api/v1/search` | 检索（返回文档片段） |
| `POST` | `/api/v1/query` | RAG 问答（返回回答+来源） |

### 上传文档

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@文档.pdf" \
  -F 'tags=["技术文档","V2.1"]'
```

### 检索

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "考勤制度", "top_k": 10}'
```

### RAG 问答

```bash
curl -X POST http://localhost:8000/api/v1/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "员工考勤和请假规定是什么？", "top_k": 5}'
```

返回示例：

```json
{
  "answer": "根据员工手册，考勤制度包括... [来源: 员工手册.pdf]",
  "sources": [
    {"doc_name": "员工手册.pdf", "chunk": "...", "score": 0.92}
  ],
  "tokens_used": 452
}
```

---

## 支持的文档格式

| 格式 | 解析方式 |
|---|---|
| PDF | PyMuPDF（文本型）/ PaddleOCR（扫描型） |
| DOCX / DOC | python-docx |
| PPTX / PPT | python-pptx |
| XLSX / XLS | openpyxl |
| MD / TXT | 原生读取 |
| HTML / HTM | BeautifulSoup |

---

## 项目结构

```
knowledge_base/
├── main.py              # FastAPI 入口
├── config.py            # 环境配置
├── task_manager.py      # 后台任务队列
├── rate_limiter.py      # 令牌桶限流
├── models/
│   ├── schemas.py       # Pydantic 模型
│   └── database.py      # SQLite 连接池
├── ingestion/
│   ├── parser.py        # 文档解析器
│   ├── metadata.py      # 元数据提取
│   └── ocr_client.py    # OCR 服务客户端
├── processing/
│   ├── chunker.py       # 多级分块
│   └── embedder.py      # Embedding 批处理 + 缓存
├── storage/
│   ├── vector_store.py  # ChromaDB 封装
│   ├── metadata_store.py# SQLite CRUD
│   └── file_store.py    # 文件存储
├── retrieval/
│   ├── hybrid_search.py # 混合检索（向量+BM25）
│   ├── reranker.py      # Cross-encoder 重排序
│   └── query_rewriter.py# Query 改写
├── rag/
│   ├── pipeline.py      # RAG 全流程
│   └── prompts.py       # Prompt 模板
├── api/
│   ├── routes.py        # API 路由
│   └── auth.py          # JWT 认证
└── ui/
    └── app.py           # Streamlit 前端
```

## 并发特性

| 特性 | 说明 |
|---|---|
| 限流 | 令牌桶中间件：搜索 10 req/s、查询 5 req/s、上传 2 req/s |
| 异步处理 | asyncio 任务队列 + ThreadPoolExecutor 处理 CPU 密集型操作 |
| 信号量 | 文档解析 4 并发、OCR 2 并发 |
| 流水线并行 | 解析→分块→Embedding→存储 四阶段并行流水线 |
| 超时降级 | 向量检索 3s、BM25 1s、重排序 2s、LLM 30s |
| 连接池 | httpx 连接复用、SQLite 读写分离 |
| 缓存 | Embedding 内存+SQLite 三级缓存、OCR SHA256 缓存 |
| 幂等去重 | SHA256 文件指纹去重、请求 ID 幂等 |

详细设计见 [docs/CONCURRENCY_DESIGN.md](docs/CONCURRENCY_DESIGN.md)

## 默认账户

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 管理员 |

首次启动时自动创建。

---

## 测试验证

```python
# 完整测试流程（上传 → 处理 → 检索 → 问答）
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "考勤制度", "top_k": 5}'

curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"query": "员工考勤规定是什么？", "top_k": 5}'
```
