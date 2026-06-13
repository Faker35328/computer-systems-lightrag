# 计算机系统课程 LightRAG 知识库

这个仓库用于构建“计算机系统基础”课程的垂直领域 RAG 系统。当前系统基于 LightRAG，已经完成课件、教材和手册资料的解析、清洗、入库流程，并支持通过 HTTP API 被 WebUI、脚本或 LangChain Agent 调用。

当前核心配置：

- LightRAG Server / WebUI: `http://localhost:9621`
- API 文档: `http://localhost:9621/docs`
- 当前工作区: `course_qwen36_plus_qwen3emb06b_courseware_v1`
- LLM: `qwen3.7-plus`
- Embedding: 本地 vLLM `Qwen/Qwen3-Embedding-0.6B`
- Embedding 服务: `http://localhost:8001/v1`
- 向量维度: `1024`
- 切分策略: `recursive_character`
- 存储: LightRAG 默认本地存储，`NanoVectorDBStorage` + `NetworkXStorage`

> 注意：README 里不会记录真实 API Key。请在 `HKUDS-LightRAG/.env` 中本地维护密钥。

## 已实现功能

### 1. LightRAG 服务化部署

仓库保留 LightRAG Docker Server、WebUI 和 OpenAI-compatible API 调用方式。部署后可以直接访问：

- WebUI: `http://localhost:9621`
- Swagger API: `http://localhost:9621/docs`
- 健康检查: `http://localhost:9621/health`

### 2. 本地 Qwen3-Embedding-0.6B 嵌入模型

新增 vLLM embedding 服务，负责提供 OpenAI-compatible `/v1/embeddings`：

- 容器服务名: `vllm-embed`
- 容器内地址: `http://vllm-embed:8001/v1`
- 宿主机地址: `http://localhost:8001/v1`
- 模型: `Qwen/Qwen3-Embedding-0.6B`
- 维度: `1024`
- 上下文长度: `32768`
- Hugging Face 缓存目录: `HKUDS-LightRAG/data/hf-cache`

LightRAG 通过 Docker 网络访问 `vllm-embed:8001`，宿主机和调试脚本通过 `localhost:8001` 访问。

### 3. LLM 切换到 qwen3.7-plus

实体关系抽取、关键词抽取、最终回答生成使用 `qwen3.7-plus`。为了保证实体关系抽取的结构稳定性，配置中关闭了思考模式：

```env
OPENAI_LLM_EXTRA_BODY='{"enable_thinking": false}'
```

### 4. 递归字符切分策略

已对 LightRAG 增加可配置切分策略：

```env
CHUNKING_STRATEGY=recursive_character
CHUNK_SIZE=1200
CHUNK_OVERLAP_SIZE=100
```

递归字符切分会优先在这些边界切分：

```text
Markdown 标题 -> 空行/段落 -> 换行 -> 中文句末标点 -> 英文句末标点 -> 分号/逗号 -> 空格 -> 字符兜底
```

同时尽量保护：

- fenced code block
- HTML table / Markdown table
- LaTeX 块公式

如果单个代码块、表格或公式本身超过 `CHUNK_SIZE`，才退回 token 兜底切分。

> 已经入库的旧 chunk 不会自动变化。该策略只影响后续上传的新文档。

### 5. 课件 PDF 处理链路

课件处理方式：

```text
课件 PDF
 -> MinerU 解析
 -> content_list.json
 -> 按 page_idx 生成页级 Markdown
 -> 质量检查
 -> 每页单独上传 LightRAG
```

当前结果：

- 课件数量: `30`
- 页级 Markdown: `1303`
- 输出目录: `processed_markdown_v2`
- Manifest: `processed_markdown_v2/courseware_manifest.json`
- 质检报告:
  - `processed_markdown_v2/quality_report.json`
  - `processed_markdown_v2/quality_report.md`

每页上传时使用可回溯来源，例如：

```text
计算机系统基础1：13. 存储器层次结构.pdf 第 57 页
```

这样回答中的 References 可以回溯到原始 PDF 的具体页码。

### 6. 教材小节处理链路

教材处理方式：

```text
CSAPP 整本 PDF
 -> MinerU 按章解析
 -> content_list.json
 -> 按小节生成 Markdown
 -> 质量检查
 -> 每个小节单独上传 LightRAG
```

当前结果：

- 覆盖 Chapter 1-12
- 输出目录: `processed_textbook_sections`
- Manifest: `processed_textbook_sections/textbook_sections_manifest.json`
- 质检报告:
  - `processed_textbook_sections/textbook_sections_quality_report.json`
  - `processed_textbook_sections/textbook_sections_quality_report.md`
- Manifest 小节数: `1207`
- 保留内容:
  - 表格
  - 代码块
  - 公式
  - 小节来源页码

教材来源格式类似：

```text
CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.2 Locality (pp. 640-644)
```

### 7. i386 手册处理链路

手册处理方式：

```text
i386 手册 / Errata
 -> 小节级 Markdown
 -> 质量检查
 -> 每小节上传 LightRAG
```

当前结果：

- 输出目录: `processed_manual_sections`
- Manifest 小节数: `650`
- 已上传: `234`
- 已存在跳过: `390`
- 过短跳过: `26`
- 上传错误: `0`

### 8. 检索阶段可观测

可以通过 `/query/data` 查看 LightRAG 的检索阶段结果，而不是只看最终生成答案。返回内容包括：

- 关键词抽取结果
- 命中的实体
- 命中的关系
- 命中的文本 chunk
- References
- 检索阶段统计信息

例如问题“空间局部性”会先被 LightRAG 预处理为类似：

```json
{
  "high_level": ["空间局部性", "计算机体系结构", "内存访问模式"],
  "low_level": ["缓存", "数据块", "连续地址"]
}
```

然后系统会分别检索文本块、实体和关系，再合并上下文交给 LLM 生成答案。

## 目录说明

```text
.
├─ HKUDS-LightRAG/                         # LightRAG Docker 项目与配置
│  ├─ .env                                 # 本地环境变量，包含密钥，不要提交
│  ├─ docker-compose.yml
│  ├─ docker-compose.embedding.yml         # vLLM embedding 增量 compose
│  └─ data/hf-cache/                       # Hugging Face 模型缓存
├─ 知识库/                                  # 原始课程 PDF
├─ mineru_output/                          # MinerU 解析输出
├─ processed_markdown_v2/                  # 课件页级 Markdown
├─ processed_textbook_sections/            # 教材小节级 Markdown
├─ processed_manual_sections/              # 手册小节级 Markdown
└─ scripts/                                # 解析、清洗、质检、上传脚本
```

重要脚本：

```text
scripts/generate_courseware_page_markdown.py
scripts/quality_courseware_markdown.py
scripts/upload_courseware_to_lightrag.py

scripts/run_textbook_mineru_chapters.py
scripts/generate_textbook_section_markdown.py
scripts/quality_textbook_sections.py
scripts/upload_textbook_sections_to_lightrag.py

scripts/generate_manual_section_markdown.py
scripts/quality_manual_sections.py
scripts/upload_manual_sections_to_lightrag.py
```

## 部署启动流程

### 1. 检查 `.env`

配置文件位于：

```text
HKUDS-LightRAG/.env
```

关键配置示例：

```env
WORKSPACE=course_qwen36_plus_qwen3emb06b_courseware_v1

LLM_BINDING=openai
LLM_BINDING_HOST=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-plus
OPENAI_LLM_EXTRA_BODY='{"enable_thinking": false}'

EMBEDDING_BINDING=openai
EMBEDDING_BINDING_HOST=http://vllm-embed:8001/v1
EMBEDDING_BINDING_API_KEY=local-key
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_DIM=1024
EMBEDDING_TOKEN_LIMIT=32768
EMBEDDING_SEND_DIM=false
EMBEDDING_USE_BASE64=false
EMBEDDING_FUNC_MAX_ASYNC=1

EMBEDDING_ASYMMETRIC=true
EMBEDDING_QUERY_PREFIX="Instruct: Given a Computer Systems course learning question, retrieve relevant textbook or lecture passages that answer the question.\nQuery: "
EMBEDDING_DOCUMENT_PREFIX=NO_PREFIX

CHUNKING_STRATEGY=recursive_character
CHUNK_SIZE=1200
CHUNK_OVERLAP_SIZE=100

MAX_ASYNC=8
MAX_PARALLEL_INSERT=3
```

不要把真实的阿里云 API Key 写进 README 或提交到 Git。

### 2. 启动 vLLM embedding 和 LightRAG

```powershell
cd D:\work-space\light-RAG\HKUDS-LightRAG

docker compose -f docker-compose.yml -f docker-compose.embedding.yml up -d vllm-embed
docker compose -f docker-compose.yml -f docker-compose.embedding.yml up -d lightrag
```

### 3. 验证服务

验证 vLLM：

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/v1/models"
```

验证 LightRAG：

```powershell
Invoke-RestMethod -Uri "http://localhost:9621/health"
```

查看文档状态：

```powershell
Invoke-RestMethod -Uri "http://localhost:9621/documents/status_counts"
```

查看日志：

```powershell
cd D:\work-space\light-RAG\HKUDS-LightRAG

docker compose -f docker-compose.yml -f docker-compose.embedding.yml logs --tail 100 vllm-embed
docker compose -f docker-compose.yml -f docker-compose.embedding.yml logs --tail 100 lightrag
```

### 4. 打开前端

- WebUI: `http://localhost:9621`
- API 文档: `http://localhost:9621/docs`

## 数据处理流程

### 课件：页级入库

课件适合按页处理。原因是 PPT 每页通常就是一个相对完整的信息单元，按页上传后 References 可以稳定回溯到 PDF 页码。

```text
PDF 第 N 页
 -> 一个 Markdown
 -> file_source = 原始 PDF + 第 N 页
 -> LightRAG 在这一页内部做兜底切分
```

生成页级 Markdown：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\generate_courseware_page_markdown.py
```

质检：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\quality_courseware_markdown.py
```

上传：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\upload_courseware_to_lightrag.py --mode full
```

### 教材：小节级入库

教材适合按小节处理。原因是一本书的每节通常包含完整概念、定义、例子和上下文，比按页更适合问答。

```text
Chapter N
 -> Section N.x
 -> 小节 Markdown
 -> LightRAG 递归字符切分
```

生成小节 Markdown：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\generate_textbook_section_markdown.py --start-chapter 3 --end-chapter 12
```

质检：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\quality_textbook_sections.py
```

上传：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\upload_textbook_sections_to_lightrag.py `
  --mode full `
  --start-chapter 3 `
  --end-chapter 12 `
  --batch-size 20 `
  --wait-interval 10 `
  --wait-timeout 7200
```

### 手册：小节级入库

手册同样适合按小节上传：

```powershell
D:\Anaconda_envs\envs\mineru\python.exe .\scripts\upload_manual_sections_to_lightrag.py --mode full
```

## 常用 HTTP API

### 1. 健康检查

```http
GET http://localhost:9621/health
```

用于确认 LightRAG 是否在线，以及当前配置是否生效。

### 2. 查看文档处理状态

```http
GET http://localhost:9621/documents/status_counts
```

常见字段：

- `pending`: 已提交但未开始处理
- `processing`: 正在处理
- `preprocessed`: 已预处理，等待后续索引
- `processed`: 已完成
- `failed`: 失败

上传批量资料时，建议等待：

```text
pending = 0
processing = 0
preprocessed = 0
failed = 0
```

### 3. 上传纯文本或 Markdown

```http
POST http://localhost:9621/documents/text
```

请求体：

```json
{
  "text": "# 测试文档\n\n空间局部性表示程序倾向于访问相邻内存地址。",
  "file_source": "manual-test.md"
}
```

PowerShell 示例：

```powershell
$payload = @{
  text = "# 测试文档`n`n空间局部性表示程序倾向于访问相邻内存地址。"
  file_source = "manual-test.md"
} | ConvertTo-Json -Depth 5 -Compress

$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:9621/documents/text" `
  -ContentType "application/json; charset=utf-8" `
  -Body $bytes
```

### 4. 查询最终答案

```http
POST http://localhost:9621/query
```

请求体：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "include_references": true,
  "include_chunk_content": true
}
```

### 5. 查看检索阶段结果

```http
POST http://localhost:9621/query/data
```

请求体：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "top_k": 10,
  "chunk_top_k": 10
}
```

这个接口适合调试“检索到了什么”，返回通常包括：

```text
data.entities
data.relationships
data.chunks
data.references
metadata.keywords
metadata.processing_info
```

### 6. 查看交给 LLM 的上下文

仍然调用 `/query`，但设置 `only_need_context=true`：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "only_need_context": true,
  "top_k": 10,
  "chunk_top_k": 10
}
```

这会返回整理后的上下文，通常包含：

- Knowledge Graph Data
- Entity
- Relationship
- Document Chunks
- Reference Document List

它适合检查“LightRAG 最终喂给 LLM 的材料是什么”。

> 浏览器地址栏只能直接访问 GET 接口，例如 `/health`。`/query`、`/query/data`、`/documents/text` 是 POST 接口，需要 Swagger、PowerShell、curl、Python 或 LangChain 调用。

## LangChain Agent 对接

当前推荐方式是 HTTP 调用 LightRAG，而不是在 Agent 进程里直接 import LightRAG。这样部署边界更清晰：

```text
LangChain Agent
 -> HTTP
 -> LightRAG Server
 -> vLLM Embedding + qwen3.7-plus
 -> 本地知识库
```

建议给 Agent 暴露 3 个工具。

### Tool 1: 获取最终答案

用于正常问答。

```python
import requests

LIGHTRAG_URL = "http://localhost:9621"

def lightrag_answer(query: str) -> str:
    payload = {
        "query": query,
        "mode": "mix",
        "include_references": True,
        "include_chunk_content": True,
    }
    resp = requests.post(f"{LIGHTRAG_URL}/query", json=payload, timeout=180)
    resp.raise_for_status()
    return resp.text
```

### Tool 2: 获取检索证据

用于让 Agent 先看检索结果，再决定是否继续问、是否补充检索。

```python
import requests

LIGHTRAG_URL = "http://localhost:9621"

def lightrag_retrieve(query: str) -> dict:
    payload = {
        "query": query,
        "mode": "mix",
        "top_k": 10,
        "chunk_top_k": 10,
    }
    resp = requests.post(f"{LIGHTRAG_URL}/query/data", json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()
```

### Tool 3: 获取 LLM 上下文

用于调试或让 Agent 获取已经拼装好的上下文。

```python
import requests

LIGHTRAG_URL = "http://localhost:9621"

def lightrag_context(query: str) -> str:
    payload = {
        "query": query,
        "mode": "mix",
        "only_need_context": True,
        "top_k": 10,
        "chunk_top_k": 10,
    }
    resp = requests.post(f"{LIGHTRAG_URL}/query", json=payload, timeout=180)
    resp.raise_for_status()
    return resp.text
```

### LangChain Tool 示例

```python
from langchain_core.tools import tool

@tool
def course_rag_answer(question: str) -> str:
    """Ask the Computer Systems LightRAG knowledge base and return a grounded answer with references."""
    return lightrag_answer(question)

@tool
def course_rag_retrieve(question: str) -> dict:
    """Retrieve entities, relationships, chunks, and references from the Computer Systems LightRAG knowledge base."""
    return lightrag_retrieve(question)

@tool
def course_rag_context(question: str) -> str:
    """Return the assembled LightRAG context that would be sent to the LLM."""
    return lightrag_context(question)
```

推荐 Agent 使用策略：

1. 普通课程问答：优先调用 `course_rag_answer`。
2. 需要解释引用来源或排查幻觉：先调用 `course_rag_retrieve`。
3. 需要自己组织最终回答：调用 `course_rag_context`，再由 Agent 自己生成。
4. 需要确认服务是否可用：调用 `/health` 或 `/documents/status_counts`。

## LightRAG 检索流程说明

以问题“空间局部性”为例，LightRAG 的大致流程是：

```text
用户问题
 -> LLM 抽取 high_level / low_level 关键词
 -> 原问题加 query prefix 后转向量
 -> low_level 关键词用于实体/关系和低层检索
 -> high_level 关键词用于高层主题检索
 -> 检索 chunks_vdb / entities_vdb / relationships_vdb
 -> 合并去重并按 token budget 截断
 -> 整理成上下文
 -> qwen3.7-plus 生成最终答案
```

当前没有接入重排序模型。可以在 `/health` 中确认：

```text
enable_rerank = false
rerank_binding = null
```

因此当前排序主要来自 LightRAG 的向量相似度、图谱关联和内部合并逻辑，而不是独立 reranker。
