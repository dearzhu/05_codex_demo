# Docker 部署指南

本指南说明如何在 M1/M2/M3 Mac 上构建 `linux/amd64` 镜像，并部署到 x86_64 Linux 服务器。

---

## 一、构建 AMD64 镜像（在 Mac 上执行）

### 1. 前置条件

- Docker Desktop 已启动
- buildx 已启用（Docker Desktop 默认自带）

验证：

```bash
docker --version
docker buildx version
```

### 2. 一键构建

```bash
./build_amd64.sh
```

将生成两个本地镜像：

| 镜像 | 用途 |
|---|---|
| `knowledge-base:amd64` | FastAPI API + Streamlit UI（共用） |
| `knowledge-base-ocr:amd64` | PaddleOCR 服务（可选） |

> 首次构建需要下载 PyTorch、ChromaDB 等依赖，耗时较长（约 10-30 分钟），之后有缓存会快很多。

### 3. 推送镜像到服务器

#### 方式 A：Docker Hub / 私有仓库（推荐）

```bash
REGISTRY=your_dockerhub_username PUSH=1 ./build_amd64.sh
```

推送后镜像名为：
- `your_dockerhub_username/knowledge-base:amd64`
- `your_dockerhub_username/knowledge-base-ocr:amd64`

#### 方式 B：离线 tar 包传输

```bash
docker save knowledge-base:amd64 -o kb-api.tar
docker save knowledge-base-ocr:amd64 -o kb-ocr.tar

# 复制到服务器后
docker load -i kb-api.tar
docker load -i kb-ocr.tar
```

---

## 二、服务器部署

### 1. 服务器前置条件

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

验证：

```bash
docker --version
docker compose version
```

### 2. 准备项目文件

在服务器上创建部署目录并上传文件：

```bash
mkdir -p /opt/knowledge-base && cd /opt/knowledge-base
```

需要上传：
- `docker-compose.yml`
- `.env`（生产环境变量，不提交到 git）
- （如果走离线 tar 包）`kb-api.tar`、`kb-ocr.tar`

参考 `.env.example` 配置生产环境变量：

```bash
cp .env.example .env
vim .env
```

必填项：

```dotenv
# DeepSeek / LLM API
LLM_API_KEY=sk-xxxxxx
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# 生产环境务必修改
JWT_SECRET=替换为随机长字符串
```

### 3. 启动服务

```bash
# 方式 A：使用已构建/已加载的镜像（推荐，不会在服务器上重新构建）
docker compose up -d

# 方式 B：需要 OCR 扫描件识别时，启用 ocr profile
docker compose --profile ocr up -d

# 方式 C：在服务器上有完整源码时，直接在服务器上构建并启动
# docker compose up -d --build
# docker compose --profile ocr up -d --build
```

> 注意：若服务器只有镜像（`docker load` 或仓库拉取），不要加 `--build`；
> `--build` 会要求服务器上有完整项目源码。

查看状态：

```bash
docker compose ps
docker compose logs -f api
```

访问地址：

| 服务 | 地址 |
|---|---|
| Web UI | `http://服务器IP:8501` |
| API 文档 | `http://服务器IP:8000/docs` |
| OCR 健康检查 | `http://服务器IP:8521/health` |

默认管理员：`admin / admin123`（首次启动自动创建，生产环境请登录后修改或通过 API 修改）。

### 4. 数据持久化

Docker Compose 已挂载命名卷，服务重启/升级不丢数据：

| 卷 | 挂载点 | 内容 |
|---|---|---|
| `kb-data` | `/app/data` | SQLite 元数据库 + ChromaDB 向量库 |
| `kb-uploads` | `/app/uploads` | 上传的原始文档 |
| `kb-models` | `/root/.cache` | Embedding 模型缓存 |
| `kb-ocr-models` | `/root/.paddlex` | OCR 模型缓存 |

### 5. 常用运维命令

```bash
# 查看日志
docker compose logs -f api
docker compose logs -f ui

# 重启单个服务
docker compose restart api

# 停止所有服务
docker compose down

# 停止并删除容器（保留数据卷）
docker compose down

# 完全清理（删除数据卷，谨慎操作）
docker compose down -v

# 手动重置数据
docker compose exec api rm -f /app/data/kb.db
docker compose exec api rm -rf /app/data/chromadb
```

---

## 三、镜像架构说明

本项目在 Apple Silicon（arm64）上开发，但目标服务器为 x86_64（amd64），因此必须构建 `linux/amd64` 镜像。

```bash
# 核心命令
docker buildx build --platform linux/amd64 -t knowledge-base:amd64 .
```

关键点：

- `buildx` 在 M1 上通过 QEMU 模拟 amd64 执行 pip 安装
- 构建产物是 amd64 镜像，无法在 arm64 本机直接以原生性能运行，但可以正常构建、保存、推送
- 如果服务器也是 arm64（如华为鲲鹏），改为 `--platform linux/arm64` 即可

---

## 四、常见问题

### Q1: 构建时报 "failed to resolve source metadata ... i/o timeout"

国内网络访问 Docker Hub（`docker.io`）经常超时。本项目已内置国内镜像兜底：

- 基础镜像默认使用 `docker.m.daocloud.io/library/python:3.11-slim`
- PyPI 默认使用 `https://pypi.tuna.tsinghua.edu.cn/simple`

`build_amd64.sh` 和 `docker-compose.yml` 已自动传入这两个参数，直接构建即可：

```bash
./build_amd64.sh
```

如需覆盖（例如在海外环境）：

```bash
BASE_IMAGE=python:3.11-slim PIP_INDEX_URL=https://pypi.org/simple ./build_amd64.sh
```

也可以给 Docker daemon 配置 registry mirror（Docker Desktop → Settings → Docker Engine）：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://registry.cn-hangzhou.aliyuncs.com"
  ]
}
```

> 注意：`buildx` 使用独立的 BuildKit 容器，不一定继承 daemon 的 mirror 配置，所以项目内用 `--build-arg` 直接指定镜像源，比只改 daemon.json 更可靠。

### Q2: 构建时提示 "no matching manifest for linux/amd64"

部分依赖（如 `onnxruntime`、`paddlepaddle`）版本过旧时可能没有 amd64 wheel。解决：

```bash
docker buildx rm amd64-builder
docker buildx create --name amd64-builder --driver docker-container --use
```

### Q3: 首次启动 API 很慢

首次启动会下载 Embedding 模型（约 400MB）并初始化 ChromaDB。日志中看到模型下载属于正常现象。`kb-models` 卷会缓存模型，之后启动变快。

### Q4: OCR 服务识别中文乱码/失败

确认 OCR 容器已安装中文字体（Dockerfile.ocr 已包含 `fonts-noto-cjk`）。首次调用会下载 PaddleOCR 模型（约 200MB），耗时约 10-30 秒。

### Q5: UI 页面报 API 连接失败

确认 API 容器健康检查通过：

```bash
docker compose ps
curl http://127.0.0.1:8000/api/v1/health
```

UI 容器通过 `http://api:8000/api/v1` 访问 API，这是 compose 内部网络地址，无需修改。
