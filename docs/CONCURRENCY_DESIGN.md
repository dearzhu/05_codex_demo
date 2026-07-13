# 并发处理设计方案

## 一、并发全景分析

```
          ┌────────────────────┐
          │   API Gateway      │  ← 多人并发请求
          │  (FastAPI async)   │
          └──────┬─────────────┘
                 │
   ┌─────────────┼───────────────┐
   │             │               │
   ▼             ▼               ▼
 文档导入      检索问答        后台管理
 (批量并发)    (多人并发)       (低频)
   │             │               │
   ▼             ▼               ▼
  +---------+  +---------+  +--------+
  │ 任务队列 │  │ 连接池   │  │ 事务   │
  │ 有界信号量│  │ 异步并发 │  │ 读写锁  │
  +---------+  +---------+  +--------+
```

### 三大并发场景

| 场景 | 特征 | 瓶颈 |
|---|---|---|
| 文档批量导入 | 吞吐量敏感，延迟不敏感 | CPU（解析+OCR）、GPU（Embedding） |
| 多人检索问答 | 延迟敏感，QPS 是关键指标 | ChromaDB 查询、LLM 推理 |
| 后台异步处理 | 吞吐量中等，需进度跟踪 | 队列积压、重试逻辑 |

---

## 二、API 层并发

### 2.1 FastAPI 异步原生

所有 API 路由使用 `async def`，利用 FastAPI 的事件循环处理并发请求：

```bash
uvicorn main:app --workers 4
```

- `workers=4`：4 个进程，配合 `SO_REUSEPORT` 均匀分发请求
- 每个进程内部由 Event Loop 处理数千个并发连接
- CPU 密集型操作（Embedding、分块）用 `run_in_executor` 交给线程池

### 2.2 连接池管理

```python
_http_clients = {
    "ocr": httpx.AsyncClient(
        base_url=OCR_SERVICE_URL,
        limits=Limits(max_connections=4, max_keepalive_connections=4)
    ),
    "llm": httpx.AsyncClient(
        timeout=60.0,
        limits=Limits(max_connections=8, max_keepalive_connections=8)
    ),
}
```

- 每个外部服务一个独立客户端，复用 TCP 连接
- 限制最大连接数，避免下游被压垮
- 应用启动时预热连接池

### 2.3 限流（中间件）

```
/api/v1/search    → 10 req/s per IP
/api/v1/query     → 5  req/s per IP（LLM 昂贵）
/api/v1/documents → 2  req/s per IP（上传带宽）
```

**实现方式**：
- **P0（单机）**：内存令牌桶，每个 IP 独立桶
- **P2（分布式）**：Redis 滑动窗口，跨多实例共享配额
- 超限返回 `429 Too Many Requests` + `Retry-After` 头部

```python
from fastapi import Request, HTTPException
from collections import defaultdict
import time

class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

_buckets: dict[str, TokenBucket] = defaultdict(lambda: TokenBucket(10, 20))
```

---

## 三、文档导入并发

批量导入 100 个文档时，需经过 **解析 → OCR → 分块 → Embedding → 存储** 五阶段，每阶段都需并行。

### 3.1 流水线架构（Pipeline Pattern）

```
                    ┌───────────┐
  文档上传 ────────→│  任务队列  │←─────── API 层入队
                    │ asyncio.Queue│
                    └─────┬─────┘
                          │ 消费者 × N
                          ▼
                    ┌───────────┐
                    │  文档解析  │  ← 4 个 Worker，依赖 CPU
                    ├───────────┤
                    │  文本分块  │  ← 内存操作，无 IO 等待
                    ├───────────┤
                    │ Embedding  │  ← 批处理，32 条/批
                    ├───────────┤
                    │   存储     │  ← 批量写入 ChromaDB + SQLite
                    └───────────┘
```

### 3.2 有界信号量控制并发度

```python
import asyncio

class IngestionPipeline:
    def __init__(self, max_parsers=4, max_embed_batch=32):
        self.parse_semaphore = asyncio.Semaphore(max_parsers)
        self.embed_queue = asyncio.Queue()
        self.embed_batch_size = max_embed_batch

    async def process_document(self, file_path: str):
        async with self.parse_semaphore:
            chunks = await self.parse_document(file_path)
        for chunk in chunks:
            await self.embed_queue.put(chunk)

    async def embed_worker(self):
        while True:
            batch = []
            for _ in range(self.embed_batch_size):
                chunk = await self.embed_queue.get()
                batch.append(chunk)
            embeddings = await self.batch_embed(batch)
            await self.batch_store(batch, embeddings)
```

### 并发上限参数

| 资源 | 上限 | 理由 |
|---|---|---|
| 并发解析数 | 4 | CPU 密集型，过量导致上下文切换 |
| Embedding 批大小 | 32 | 平衡 GPU 利用率和延迟 |
| ChromaDB 批量写入 | 100 条/批 | 单次写入有元数据序列化开销 |
| OCR 并发请求 | 2 | OCR 服务单进程单线程，多请求反而排队 |

### 3.3 进度与状态追踪

每个文档导入任务的状态机：

```
pending → parsing → chunking → embedding → storing → completed
                                              → failed（含错误信息 & 重试次数）
```

设计：

```python
# SQLite documents 表增加字段
# status: str            — pending/parsing/chunking/embedding/storing/completed/failed
# error_message: str     — 失败原因
# retry_count: int       — 重试次数
# started_at: datetime   — 开始处理时间
# completed_at: datetime — 完成时间

# API 端点为前端轮询提供进度
GET /api/v1/documents/{id}/status
→ {"status": "embedding", "progress": "15/120 chunks"}
```

### 3.4 幂等性与去重

- **文档去重**：SHA256 文件指纹。上传时先计算 hash，查询 SQLite 中已有记录，相同指纹直接返回已有文档 ID+状态
- **分块去重**：同一文档重新导入时，先删除旧分块再写入（全量更新）
- **API 幂等**：客户端携带 `X-Request-Id` 头部，服务端 5 秒内相同 ID 去重，避免重复提交

```python
_request_dedup: dict[str, float] = {}  # request_id → timestamp
DEDUP_WINDOW = 5.0

def is_duplicate(request_id: str) -> bool:
    now = time.monotonic()
    if request_id in _request_dedup and now - _request_dedup[request_id] < DEDUP_WINDOW:
        return True
    _request_dedup[request_id] = now
    return False
```

---

## 四、Embedding 并发

### 4.1 批处理队列

Sentence-Transformer 的 `encode()` 方法原生支持 batch 参数，单次推理多条文本的效率远高于逐条推理。

```
逐条编码 100 条：100 × 0.2s = 20s
批量编码 100 条：ceil(100/32) × 0.8s = 2.5s  → **8x 加速**
```

### 4.2 缓存层

三级缓存，逐级回退：

```
    内存 LRU Cache（最近 1000 条）
            │ miss
            ▼
   SQLite persistence cache（`embedding_cache` 表）
            │ miss
            ▼
   Sentence-Transformer 推理
```

```sql
CREATE TABLE embedding_cache (
    text_hash TEXT PRIMARY KEY,         -- SHA256(文本)
    embedding BLOB,                     -- pickle/bytes 序列化向量
    model_version TEXT,                 -- 模型名+版本，版本变化时清空
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 4.3 GPU 资源管理

Embedding 使用 GPU 时，多个进程同时推理会耗尽显存。策略：

| 阶段 | 方案 |
|---|---|
| P0 | 单进程运行，`torch.no_grad()` + 定期 `torch.cuda.empty_cache()` |
| P1 | Embedding 服务独立进程，主服务通过 HTTP 调用 |
| P2 | Triton Inference Server 部署 Embedding 模型 |

```python
# P0 方案：防止显存泄漏
import torch
import gc

def batch_embed(texts: list[str]) -> list[list[float]]:
    with torch.no_grad():
        embeddings = model.encode(texts, batch_size=32, show_progress_bar=False)
    torch.cuda.empty_cache()
    gc.collect()
    return embeddings.tolist()
```

---

## 五、检索问答并发

### 5.1 混合检索并行化

向量检索和 BM25 检索相互独立，完全可并行：

```python
async def hybrid_search(query: str, top_k: int = 30):
    vector_task = asyncio.create_task(vector_search(query, top_k))
    bm25_task = asyncio.create_task(bm25_search(query, top_k))
    vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)
    return merge_and_rerank(vector_results, bm25_results, query)
```

### 5.2 ChromaDB 连接策略

| 阶段 | 模式 | 并发能力 |
|---|---|---|
| P0 | `PersistentClient` + `threading.Lock` | 写串行，读串行 |
| P1 | `HttpClient` → ChromaDB Server | 写串行，读并行 |
| P2 | ChromaDB Cluster | 读写均并行 |

P0 阶段写保护实现：

```python
_chroma_lock = threading.Lock()

def add_chunks(self, chunks: list[Chunk]):
    with _chroma_lock:
        self.collection.add(
            ids=[c.chunk_id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            metadatas=[c.metadata for c in chunks],
            documents=[c.content for c in chunks],
        )
```

### 5.3 请求级超时控制

每个检索请求有独立的超时预算，超时则降级：

| 阶段 | 超时 | 降级行为 |
|---|---|---|
| 向量检索 | 3s | 返回空向量结果 |
| BM25 检索 | 1s | 只用向量结果 |
| 重排序 | 2s | 只用混合检索结果 |
| LLM 生成 | 30s | 返回"检索完成，但回答超时" |

```python
async def search_with_timeout(query: str) -> SearchResult:
    try:
        return await asyncio.wait_for(
            hybrid_search(query),
            timeout=3.0
        )
    except asyncio.TimeoutError:
        logger.warning(f"Search timeout for query: {query}")
        return SearchResult(items=[], timed_out=True)
```

### 5.4 查询去重（可选）

1 秒内完全相同的 query（用户频繁点击或前端重试），第二次直接返回缓存结果：

```python
query_cache: dict[str, tuple[float, SearchResult]] = {}
CACHE_TTL = 1.0

def get_cached_query(query: str) -> SearchResult | None:
    if query in query_cache:
        ts, result = query_cache[query]
        if time.monotonic() - ts < CACHE_TTL:
            return result
    return None
```

---

## 六、OCR 并发

### 6.1 请求队列

PaddleOCR 是单进程单线程模型，所有请求排队执行：

```
请求到达 → 放入 asyncio.Queue → Worker 串行消费 → 返回结果
```

每个请求设置超时（默认 120s），超时返回 503 并建议重试。

### 6.2 结果缓存

```
Key: SHA256(图片内容)
Value: { results: [...], full_text: "..." }
TTL: 24 小时
存储: SQLite ocr_cache 表
```

命中缓存的图片跳过 OCR 识别，直接返回结果。

### 6.3 调用端限流

主项目调用 OCR 时，客户端侧也做并发控制：

```python
class OCRClient:
    def __init__(self):
        self._semaphore = asyncio.Semaphore(2)

    async def ocr_image(self, path: str) -> dict:
        async with self._semaphore:
            return await self._call("POST", "/ocr/image", files={...})
```

---

## 七、数据库并发

### 7.1 SQLite 并发策略（单机）

SQLite 的瓶颈在写操作（同一时刻只能一个写事务），但读操作可并发。

关键配置：

```sql
PRAGMA journal_mode=WAL;       -- 写不阻塞读
PRAGMA busy_timeout=5000;      -- 等待锁最多 5 秒
PRAGMA synchronous=NORMAL;     -- 减少 fsync 频率
PRAGMA cache_size=-64000;      -- 64MB 缓存
```

连接池设计：

```python
import sqlite3
import queue
import threading

_READ_POOL: queue.Queue[sqlite3.Connection] = queue.Queue()
_WRITE_LOCK = threading.Lock()

def init_db(db_path: str, pool_size: int = 4):
    for _ in range(pool_size):
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _READ_POOL.put(conn)

def get_read_conn() -> sqlite3.Connection:
    return _READ_POOL.get()

def get_write_conn() -> sqlite3.Connection:
    _WRITE_LOCK.acquire()
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def release_write_conn(conn: sqlite3.Connection):
    conn.close()
    _WRITE_LOCK.release()
```

### 7.2 PostgreSQL 生产方案（P2+）

当数据量超过 10 万 Chunk 或 QPS 超过 100 时，迁移到 PostgreSQL：

```python
import asyncpg

pool = await asyncpg.create_pool(
    dsn=DB_DSN,
    min_size=4,
    max_size=16,
    command_timeout=10,
)

async with pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM documents WHERE ...")
```

---

## 八、后台任务与重试

### 8.1 轻量级任务队列（P0）

```python
class TaskManager:
    def __init__(self, max_workers: int = 2, max_retries: int = 3):
        self.queue: asyncio.Queue[Task] = asyncio.Queue()
        self.max_retries = max_retries
        self._workers = []

    async def start(self):
        self._workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_workers)
        ]

    async def enqueue(self, task: Task):
        await self.queue.put(task)

    async def _worker(self, wid: int):
        while True:
            task = await self.queue.get()
            try:
                await task.execute()
            except Exception as e:
                if task.retry_count < self.max_retries:
                    task.retry_count += 1
                    task.backoff = min(60, task.backoff * 2)
                    await asyncio.sleep(task.backoff)
                    await self.queue.put(task)
                else:
                    logger.error(f"Task failed after {self.max_retries} retries: {e}")
```

### 8.2 生产级方案（P2+）

迁移到 Celery + Redis / RabbitMQ，支持：

- 持久化任务（进程重启不丢失）
- 定时任务 / 周期性任务
- 任务状态监控（Flower）
- 分布式 Worker

### 8.3 优雅关闭

```python
async def shutdown(sig, loop):
    logger.info("Shutting down gracefully...")
    # 1. 停止接收新请求
    # 2. 等待 in-flight 任务（最多 30s）
    tasks = [t for t in asyncio.all_tasks()
             if t is not asyncio.current_task()]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    # 3. 关闭连接池
    # 4. 清理临时文件
    loop.stop()
```

---

## 九、新增组件一览

相比原始方案，并发版增加了以下组件：

| 组件 | 作用 | 引入阶段 |
|---|---|---|
| `asyncio.Semaphore` 组 | 控制各阶段并发度 | P0 |
| 文档流水线 | 解析→分块→Embedding→存储 四阶段并行流水线 | P0 |
| 连接池管理器 | httpx 客户端池、数据库连接池 | P0 |
| 请求级超时 + 降级 | 检索各阶段独立超时控制 | P0 |
| 导入状态机 | 文档导入进度追踪 | P0 |
| 令牌桶限流 | API 限流中间件 | P1 |
| ChromaDB HTTP 模式 | 支持多进程并发写入/查询 | P1 |
| 查询缓存 | 相同 query 短时间内去重 | P1 |
| OCR 结果缓存 | 相同图片跳过重复 OCR | P1 |
| PostgreSQL 连接池 | 替代 SQLite 单机写入 | P2 |
| Celery 任务队列 | 替代轻量级 asyncio.Queue | P2 |
| 优雅关闭 | 安全停止服务 | P1 |

---

## 十、选型决策记录

| 决策 | 选择 | 拒绝的理由 |
|---|---|---|
| 流水线同步方式 | `asyncio.Queue` | RabbitMQ 对单机方案过重 |
| 限流方式 | 内存令牌桶 | Redis 增加了部署复杂度，单机足够 |
| 任务重试 | 指数退避 + 有界队列 | 固定间隔浪费资源，无限队列导致 OOM |
| ChromaDB 并发 | P0 Lock → P1 HTTP 模式 | P0 不需要外部依赖，快速验证 |
| Embedding 批处理 | 32 条 / batch | 经验值，可在配置文件中调整 |
| OCR 排队 | asyncio.Queue + 单 Worker | PaddleOCR 不支持并行推理 |
| 文档去重 | SHA256 文件指纹 | 时间戳去重不可靠，UUID 无法检测内容重复 |
