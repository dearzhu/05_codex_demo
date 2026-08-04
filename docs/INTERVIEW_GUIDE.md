# 企业知识库检索系统 — 面试指南

---

## 一、项目概述

### 一句话说明

> 基于 RAG（检索增强生成）架构的企业知识库系统，支持 PDF/DOCX 等多格式文档上传、智能语义检索和自然语言问答。

### 核心能力

- 上传 PDF/DOCX/PPTX/XLSX/MD/HTML 等多格式文档
- 自动解析、分块、向量化存入知识库
- 混合检索（语义 + 关键词）找到相关内容
- 基于 DeepSeek 等 LLM 的 RAG 问答

### 技术栈

| 层 | 技术 | 版本 |
|---|---|---|
| 后端框架 | FastAPI + Uvicorn | Python 3.11 |
| 向量数据库 | ChromaDB | 1.5.x |
| Embedding | BAAI/bge-large-zh-v1.5 | 1024 维 |
| 重排序 | BAAI/bge-reranker-v2-m3 | Cross-encoder |
| 全文检索 | rank-bm25 | BM25 算法 |
| LLM API | DeepSeek Chat (OpenAI 兼容) | deepseek-v4 |
| 元数据库 | SQLite (WAL 模式) | 内建 |
| 前端 | Streamlit | 1.59 |
| OCR | PaddleOCR | 3.7 (本地服务) |
| 包管理 | uv | 0.7 |

---

## 二、架构详解

### 2.1 整体架构

```
                ┌────────────────────────────────────────┐
                │            用户层                       │
                │  Streamlit Web UI (:8501)              │
                │  curl / Python SDK                     │
                └────────────────┬───────────────────────┘
                                 │ HTTP
                ┌────────────────▼───────────────────────┐
                │          API 层 (FastAPI :8000)         │
                │  认证 → 限流 → 路由 → 处理 → 响应      │
                │  JWT  | 令牌桶 | 10 REST 端点          │
                └──────┬────────────────┬────────────────┘
                       │                │
          ┌────────────▼────┐    ┌──────▼────────────┐
          │   检索引擎       │    │   文档处理流水线    │
          │ 向量搜索 + BM25 │    │ 解析→分块→Embedding│
          │ 重排序 + RAG    │    │ 异步 + 线程池      │
          └────────┬───────┘    └──────┬─────────────┘
                   │                   │
          ┌────────▼───────────────────▼──────────┐
          │            存储层                      │
          │  ChromaDB  |  SQLite  |  文件系统      │
          └────────────────────────────────────────┘
                   │
          ┌────────▼────────┐
          │  PaddleOCR      │  (本地 HTTP 服务)
          │  扫描件识别     │
          └─────────────────┘
```

### 2.2 数据流

**文档上传流：**
```
上传 PDF → 保存到文件系统 → 创建元数据记录 → 入队异步处理
    → 解析 (PyMuPDF) → 分块 (3级策略) → Embedding (batch) → 存储
```

**检索问答流：**
```
用户输入 → Query 改写 → 并行混合检索 → 重排序
    → 组装 Context → LLM 调用 → 返回回答+来源
```

---

## 三、核心技术深度解析

### 3.1 RAG (检索增强生成)

RAG 是解决 LLM 知识更新难、幻觉问题的主流方案。

**为什么需要 RAG：**
- 大模型的训练数据有截止日期，无法知道企业内部的最新文档
- 纯 LLM 会"编造"答案（幻觉），RAG 通过检索真实文档约束生成
- 修改知识库只需要替换文档，不需要重新训练模型

**本项目中的 RAG 流程：**

```
用户问题 "请假流程是什么？"
         │
         ▼  Query 改写 ─── 短查询扩展、多轮对话拼接
   "员工请假流程是什么？"
         │
         ▼  混合检索（并行）
   ┌──────┴──────┐
   ▼              ▼
向量搜索         BM25 搜索
(语义匹配)      (关键词匹配)
   │              │
   └──────┬──────┘
          ▼  RRF 融合 + 重排序
   Top-5 文档片段
          │
          ▼  Context 组装
   [来源: 员工手册.pdf]
   第三条：请假需提前一天申请...
          │
          ▼  LLM 生成
   "根据员工手册，请假需提前一天填写审批单..."
```

**关键代码** (rag/pipeline.py)：

```python
async def rag_query(query, top_k=10):
    # 1. 改写
    rewritten = rewrite_query(query, history)
    # 2. 混合检索（向量 + BM25 并行）
    search_results, _ = await hybrid_search(rewritten)
    # 3. 重排序
    reranked = await rerank(rewritten, search_results, top_k)
    # 4. Context 组装
    context = "\n\n".join([f"[来源: {doc}]\n{text}" for ...])
    # 5. LLM 调用
    answer, tokens = await _call_llm(query=rewritten, context=context)
    return {"answer": answer, "sources": sources}
```

### 3.2 向量检索 vs 关键词检索

| 特性 | 向量检索 (Dense) | 关键词检索 (Sparse/BM25) |
|---|---|---|
| 原理 | 语义匹配，理解"苹果"≈"水果" | 词频统计，精确匹配关键词 |
| 优点 | 处理同义词、语义相似 | 精确匹配专有名词、编号 |
| 缺点 | 对罕见词不敏感 | 无法理解语义，"苹果手机"搜不到"iPhone" |
| 适合场景 | 开放域问答、概念理解 | 精确查找、代码搜索、产品型号 |

**本项目采用混合检索**，两者取长补短。

### 3.3 Embedding 模型：BGE-large-zh-v1.5

- **输出维度**：1024（较高的维度保留更多语义信息）
- **语言**：中文优化（也可处理英文）
- **检索指令**：查询时拼接 `"为这个句子生成表示以用于检索相关文章："`
- **归一化**：输出归一化为单位向量，余弦相似度等价于内积

**为什么不是 M3E：**
BGE 在 MTEB 中文榜单（C-MTEB）上排名更高，社区更活跃，更新更频繁。

### 3.4 混合检索与 RRF 融合

**Reciprocal Rank Fusion (RRF)** 是一种无需训练的排序融合方法：

```
score = α × vector_norm + (1-α) × bm25_norm
```

- α 默认 0.7，偏向语义检索
- 先各自检索 Top-30，再融合取 Top-10
- 向量结果和 BM25 结果分别归一化到 [0, 1]

### 3.5 Cross-encoder 重排序

**Bi-encoder vs Cross-encoder：**

```
Bi-encoder:              Cross-encoder:
query → [vec]            [query, doc] → score
doc   → [vec]            交互式注意力机制
         ↓ cosine
       score
```

- Bi-encoder 快但精度低（预计算向量）
- Cross-encoder 慢但精度高（每次重新计算）
- 策略：Bi-encoder 粗筛 Top-30 → Cross-encoder 精排 Top-10

### 3.6 分块策略（三级兜底）

```
Level 1: 按 Markdown 标题切分
  如：## 第一章 → 语义完整的章节

Level 2: 按段落切分（双换行符）
  回退条件：没有标题结构

Level 3: 递归字符切分（512 字符 + 128 重叠）
  回退条件：段落超过 512 字符
```

每层的选择：
- **语义分块**保留文档结构，LLM 理解更准确
- **段落分块**简单可靠，适用大多数文档
- **字符分块**兜底，保证每段不超过模型输入限制

### 3.7 Embedding 三级缓存

```
内存 LRU (1000 条) → SQLite 持久化 → 模型推理
命中率预期：> 60%
```

- **内存缓存**：cachetools LRU，最近的查询
- **持久缓存**：SQLite `embedding_cache` 表，key=text_hash
- **模型版本**：每个缓存关联模型版本，版本变更自动失效

### 3.8 限流器（令牌桶算法）

```
桶容量 20 个令牌，每秒补充 10 个
请求消耗 1 个令牌
令牌不足 → 429 Too Many Requests
```

**为什么不是漏桶或固定窗口：**
令牌桶允许突发流量（桶内积攒的令牌），更适合知识库这种"大部分时间空闲，偶尔密集搜索"的场景。

---

## 四、并发设计

### 4.1 异步流水线

```
文档上传 → asyncio Queue → Worker 2个 → ThreadPoolExecutor
                                           ├── PDF 解析 (CPU)
                                           ├── 分块 (CPU)
                                           └── Embedding (CPU/GPU)
```

同步操作用 `run_in_executor` 丢进线程池，不阻塞事件循环。

### 4.2 信号量控制

| 资源 | 并发上限 | 原因 |
|---|---|---|
| 文档解析 | 4 | CPU 密集型，多则上下文切换 |
| OCR 调用 | 2 | PaddleOCR 单线程 |
| Embedding 批处理 | 32条/批 | GPU 利用率平衡 |

### 4.3 超时降级

| 阶段 | 超时 | 降级行为 |
|---|---|---|
| 向量搜索 | 3s | 返回空结果 |
| BM25 搜索 | 1s | 只用向量结果 |
| 重排序 | 2s | 只混合不重排 |
| LLM 生成 | 30s | 返回"LLM 暂不可用" |

### 4.4 任务重试（指数退避）

```
失败 → 等待 1s → 重试 → 失败 → 等待 2s → 重试
→ 失败 → 等待 4s → 重试 → 失败 → 放弃
```

---

## 五、数据库设计

### SQLite 连接池

```python
_READ_POOL: 4 个连接（队列），并发读取
_WRITE_LOCK: 串行写入（SQLite 单写者限制）
```

**为什么要用连接池：**
- 避免每次请求都创建销毁连接（开销约 10ms）
- 控制并发连接数，防止 SQLite 锁竞争

**WAL 模式：**
```sql
PRAGMA journal_mode=WAL;   -- 写不阻塞读
PRAGMA busy_timeout=5000;  -- 等待锁超时
PRAGMA synchronous=NORMAL; -- 减少 fsync
```

WAL 模式下，读操作从不被写阻塞，写操作只被其他写阻塞。

---

## 六、关键问题与解决方案

### Q1: ChromaDB 报 "Failed to parse hnsw parameters from segment metadata"

**原因：** ChromaDB 1.5.x 的 `get_or_create_collection()` 不支持 `hnsw:M` 和 `hnsw:ef_construction` 参数，只支持 `hnsw:space`。

**解决：** 移除多余参数，使用默认值。

```diff
- metadata={"hnsw:space": "cosine", "hnsw:M": 16, "hnsw:ef_construction": 200}
+ metadata={"hnsw:space": "cosine"}
```

### Q2: LLM 一直返回 "LLM 暂不可用"

**原因：** Shell 环境变量 `OPENAI_API_KEY`（另一个项目的 OpenAI Key，164 位）覆盖了 `.env` 文件中的 DeepSeek Key（35 位）。pydantic-settings 的优先级是：**环境变量 > .env 文件**。

**解决：** 将配置字段重命名为 `llm_api_key`，对应环境变量 `LLM_API_KEY`，避开与 OpenAI 标准环境变量的冲突。

```diff
- openai_api_key: str = Field(default=..., env="LLM_API_KEY")
+ llm_api_key: str = "sk-placeholder"
```

### Q3: 大 PDF 上传后事件循环阻塞，其他请求超时

**原因：** 文档处理的异步任务中直接调用了同步函数（PyMuPDF 解析、分块），阻塞了事件循环。

**解决：** CPU 密集型操作用 `run_in_executor` 交给线程池。

```python
# 之前（阻塞事件循环）
content = parse_document(file_path)
chunks = chunk_document(doc_id, content, doc_meta)

# 之后（线程池执行）
content = await loop.run_in_executor(None, parse_document, file_path)
chunks = await loop.run_in_executor(None, lambda: chunk_document(...))
```

### Q4: Prompt 模板报 NameError：`{文档名}` is not defined

**原因：** `build_prompt()` 使用了 f-string（`f"""..."""`），`{文档名}` 被 Python 当作变量名解析。

**解决：** f-string 中用 `{{文档名}}` 转义为字面大括号。

```diff
- return f"""...引用原文时标注 [来源: {文档名}]"""
+ return f"""...引用原文时标注 [来源: {{文档名}}]"""
```

### Q5: PaddleOCR 3.7 API 不兼容

**原因：** PaddleOCR 3.7 移除了 `show_log` 参数，将 `ocr.ocr()` 替换为 `ocr.predict()`，返回格式从嵌套列表改为 dict-like 对象。

**解决：** OCR 服务单独进程部署（不依赖主项目的 Python 环境），用 `http.server` 提供 HTTP API，主项目通过 httpx 调用。

---

## 七、部署运维

### 启动步骤

```bash
# 1. 安装依赖
uv sync

# 2. 启动 OCR 服务（需要 PaddleOCR）
bash ocr_service/start.sh start

# 3. 启动 API
bash start_api.sh

# 4. 启动 UI（新终端）
bash start_ui.sh
```

### 启动脚本说明

- `start_api.sh` — uvicorn + FastAPI，`--reload` 模式
- `start_ui.sh` — Streamlit web 界面
- `reset_data.sh` — 清空数据库和向量库

### OCR 服务说明

PaddleOCR 单独部署为 HTTP 服务，原因：
1. PaddlePaddle 依赖沉重（约 500MB），不污染主项目
2. 主项目用 `httpx` 调用，解耦
3. OCR 首次加载模型需 10s+，独立服务避免每次重启都重新加载

---

## 八、面试常见问题

### 为什么选 ChromaDB 而不是 Milvus/Qdrant？

- **P0 阶段**只有单机需求，ChromaDB 零配置启动，不需要独立部署
- 数据量 < 100 万 Chunk 时性能差异不大
- 迁移路径清晰：P1 切 ChromaDB HTTP 模式，P2 切 Milvus

### 为什么 Embedding 不用 GPU？

M1 Mac 上没有 NVIDIA GPU，使用 Mac 的 MPS 后端有兼容性问题。CPU 推理 32 条/批约 0.8s，对于 MVP 阶段可接受。

### SQLite 能支撑多大并发？

- 读并发：无限制（WAL 模式）
- 写并发：1（单写者锁）
- 预期规模：< 1000 QPS 读 / < 100 QPS 写 足够
- 超出后迁移到 PostgreSQL + asyncpg 连接池

### 如何保证搜索质量？

1. **混合检索**：语义 + 关键词互补
2. **重排序**：Cross-encoder 二次筛选
3. **Context 优化**：按原文顺序排列，不按分数
4. **质量指标**：Recall@10、MRR、用户反馈

### 这个项目还可以怎么改进？

- **P2 阶段**：PostgreSQL 替代 SQLite，Celery 替代 asyncio.Queue
- **在线学习**：用户反馈修正排序
- **知识图谱**：实体抽取 + 关系检索
- **多模态**：图表 OCR + 理解
- **RBAC**：细粒度文档权限

---

## 九、关键技术概念速查

| 概念 | 一句话解释 |
|---|---|
| RAG | 检索增强生成：先搜文档再让 LLM 回答，解决幻觉 |
| Embedding | 把文本转换成向量，语义相近的向量距离近 |
| Cosine Similarity | 衡量两个向量方向是否一致，值 [-1, 1] |
| BM25 | 关键词检索算法：词频 × 逆文档频率，比 TF-IDF 更好 |
| HNSW | 分层可导航小世界图：向量检索的主流索引算法 |
| Cross-encoder | 把 query 和 doc 拼在一起做注意力计算，精度高 |
| Bi-encoder | query 和 doc 各自编码，再算余弦相似度，速度快 |
| WAL | Write-Ahead Logging：SQLite 的并发模式，写不阻塞读 |
| Token Bucket | 限流算法：固定速率生成令牌，突发消费积累的令牌 |
| RRF | Reciprocal Rank Fusion：多个排序结果融合算法 |
| Signal 的 Semaphore | asyncio 的信号量，控制并发访问资源数量 |
| run_in_executor | 将同步函数丢到线程池执行，不阻塞事件循环 |

---

> **温馨提示**：面试时不要背诵文档，重点是用自己的话讲清楚每个模块为什么这么设计、遇到过什么坑、怎么解决的。面试官更关心你的**工程判断力**而不是记性。
