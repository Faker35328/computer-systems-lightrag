# 计算机系统课程 LightRAG 知识库

这个仓库用于构建“计算机系统基础”课程的垂直领域 RAG 系统。当前系统基于 LightRAG，已经完成课件、教材和手册资料的解析、清洗、入库流程，并支持通过 HTTP API 被 WebUI、脚本或 LangChain Agent 调用。

当前核心配置：

- LightRAG Server / WebUI: `http://localhost:9621`
- API 文档: `http://localhost:9621/docs`
- 当前工作区: `course_qwen36_plus_qwen3emb06b_courseware_v1`
- LLM: `qwen3.7-plus`
- Embedding: 本地 vLLM `Qwen/Qwen3-Embedding-0.6B`
- Embedding 服务: `http://localhost:8001/v1`
- Reranker: 本地 vLLM `BAAI/bge-reranker-v2-m3`
- Reranker 服务: `http://localhost:8000/rerank`
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
- 当前服务上下文长度: `8192`
- Hugging Face 缓存目录: `HKUDS-LightRAG/data/hf-cache`

LightRAG 通过 Docker 网络访问 `vllm-embed:8001`，宿主机和调试脚本通过 `localhost:8001` 访问。

### 3. 本地 reranker 重排序模型

新增 vLLM rerank 服务，负责提供 Cohere-compatible `/rerank` 接口：

- 容器服务名: `vllm-rerank`
- 容器内地址: `http://vllm-rerank:8000/rerank`
- 宿主机地址: `http://localhost:8000/rerank`
- 模型: `BAAI/bge-reranker-v2-m3`
- 默认策略: 服务常驻，但 `RERANK_BY_DEFAULT=false`

也就是说，普通查询默认不重排序；需要评测或处理歧义问题时，在请求中显式传入：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "enable_rerank": true
}
```

8GB 显卡下采用保守显存分配，并把两个 vLLM 服务的 `--max-model-len` 都控制在 `8192`：

```text
vllm-embed   gpu-memory-utilization = 0.43
vllm-rerank  gpu-memory-utilization = 0.38
```

当前已完成验证：

- `http://localhost:8000/rerank` 可正常返回排序分数。
- 示例问题 `空间局部性是什么？` 中，相关文本得分约 `0.9985`，不相关文本得分约 `0.00017`。
- LightRAG `/health` 已显示 `rerank_binding=cohere`、`rerank_model=BAAI/bge-reranker-v2-m3`。
- `/query/data` 使用 `enable_rerank=true` 时，LightRAG 日志中出现 `Successfully reranked: 8 chunks from 44 original chunks`，说明不是只启动了容器，而是已经实际参与检索排序。

### 4. LLM 切换到 qwen3.7-plus

实体关系抽取、关键词抽取、最终回答生成使用 `qwen3.7-plus`。为了保证实体关系抽取的结构稳定性，配置中关闭了思考模式：

```env
OPENAI_LLM_EXTRA_BODY='{"enable_thinking": false}'
```

### 5. 递归字符切分策略

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

### 6. 课件 PDF 处理链路

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

### 7. 教材小节处理链路

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

### 8. i386 手册处理链路

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

### 9. 检索阶段可观测

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

### 10. 课程 JSON 关键词增强

系统已经把课程知识骨架接入 LightRAG Server 的原生关键词检索链路。它不会改写用户原始问题，而是在 LightRAG 自己抽取 `high_level / low_level` 关键词之后，用 `计算机系统基础1.json` 和 `计算机系统基础2.json` 做一次课程节点定位和关键词补强。

当前流程：

```text
WebUI / LangChain Agent
 -> LightRAG Server /query
 -> LightRAG 原生 LLM 抽取 high_level / low_level 关键词
 -> 课程 JSON 本地召回候选课程节点
 -> qwen3.7-plus 从候选节点中精选相关节点
 -> 融合课程关键词
 -> LightRAG 原检索流程
 -> 返回答案和 References
```

课程骨架文件位置：

```text
HKUDS-LightRAG/course_outlines/计算机系统基础1.json
HKUDS-LightRAG/course_outlines/计算机系统基础2.json
```

如果请求显式传入 `hl_keywords` 或 `ll_keywords`，系统会尊重调用方传入的关键词，不再执行课程 JSON 增强。这保留了 LightRAG 原本预留的手动关键词接口语义。

`/query/data` 会额外返回调试字段：

```text
metadata.course_outline_enhancer
```

普通 `/query` 和 WebUI 最终回答不会显示这个调试字段。

## 目录说明

```text
.
├─ HKUDS-LightRAG/                         # LightRAG Docker 项目与配置
│  ├─ .env                                 # 本地环境变量，包含密钥，不要提交
│  ├─ docker-compose.yml
│  ├─ docker-compose.embedding.yml         # vLLM embedding 增量 compose
│  ├─ course_outlines/                     # 课程 JSON 骨架，用于查询阶段关键词增强
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
EMBEDDING_TOKEN_LIMIT=8192
EMBEDDING_SEND_DIM=false
EMBEDDING_USE_BASE64=false
EMBEDDING_FUNC_MAX_ASYNC=1

EMBEDDING_ASYMMETRIC=true
EMBEDDING_QUERY_PREFIX="Instruct: Given a Computer Systems course learning question, retrieve relevant textbook or lecture passages that answer the question.\nQuery: "
EMBEDDING_DOCUMENT_PREFIX=NO_PREFIX

RERANK_BINDING=cohere
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_BINDING_HOST=http://vllm-rerank:8000/rerank
RERANK_BINDING_API_KEY=local-key
RERANK_BY_DEFAULT=false
MIN_RERANK_SCORE=0.0
RERANK_ENABLE_CHUNKING=true
RERANK_MAX_TOKENS_PER_DOC=2048

CHUNKING_STRATEGY=recursive_character
CHUNK_SIZE=1200
CHUNK_OVERLAP_SIZE=100

ENABLE_COURSE_KEYWORD_ENHANCER=true
COURSE_OUTLINE_FILES=/app/course_outlines/计算机系统基础1.json,/app/course_outlines/计算机系统基础2.json
COURSE_OUTLINE_SELECTOR=llm
COURSE_OUTLINE_CANDIDATE_K=20
COURSE_OUTLINE_SELECTED_K=3
COURSE_OUTLINE_SKIP_IF_KEYWORDS=true

MAX_ASYNC=8
MAX_PARALLEL_INSERT=3
```

不要把真实的阿里云 API Key 写进 README 或提交到 Git。

### 2. 启动 vLLM embedding / reranker 和 LightRAG

```powershell
cd D:\work-space\light-RAG\HKUDS-LightRAG

docker compose -f docker-compose.yml -f docker-compose.embedding.yml up -d vllm-embed vllm-rerank
docker compose -f docker-compose.yml -f docker-compose.embedding.yml up -d lightrag
```

### 3. 验证服务

验证 vLLM：

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/v1/models"
Invoke-RestMethod -Uri "http://localhost:8000/v1/models"
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

默认情况下 `RERANK_BY_DEFAULT=false`，这类请求不会启用重排序。需要单次开启本地 BGE Reranker 时，显式加入：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "top_k": 10,
  "chunk_top_k": 10,
  "enable_rerank": true
}
```

这个接口适合调试“检索到了什么”，返回通常包括：

```text
data.entities
data.relationships
data.chunks
data.references
metadata.keywords
metadata.course_outline_enhancer
metadata.processing_info
```

其中 `metadata.keywords` 是最终用于检索的融合关键词；`metadata.course_outline_enhancer` 用于调试课程 JSON 命中的节点和补充关键词。

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

以问题“空间局部性是什么？”为例，可以把一次完整查询理解成下面这条链路：

```text
用户问题
 -> /query 或 /query/data
 -> qwen3.7-plus 抽取 high_level / low_level 关键词
 -> 课程 JSON 骨架定位课程节点并补充关键词
 -> 原问题加 embedding query prefix 后转成 1024 维向量
 -> 检索 chunks_vdb 得到语义相似文本块
 -> 用 low_level 关键词检索 entities_vdb / 本地图谱邻域
 -> 用 high_level 关键词检索 relationships_vdb / 全局主题关系
 -> 合并文本块、实体、关系
 -> 去重并按 max_entity_tokens / max_relation_tokens / max_total_tokens 截断
 -> 如果 enable_rerank=true，调用 BGE Reranker 对候选 chunks 重排序
 -> 组装成最终上下文
 -> qwen3.7-plus 基于上下文生成答案
 -> 返回答案和 References
```

### 1. 输入问题

```text
空间局部性是什么？
```

如果调用 `/query/data`，请求体可以类似：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "top_k": 8,
  "chunk_top_k": 8,
  "max_entity_tokens": 6000,
  "max_relation_tokens": 8000,
  "max_total_tokens": 30000,
  "enable_rerank": false
}
```

### 2. LightRAG 原生关键词抽取

LightRAG 会先调用当前 LLM 从问题中抽取两类关键词：

- `high_level`: 更偏主题、概念、领域层面的关键词，主要用于全局关系和主题检索。
- `low_level`: 更偏具体实体、术语、细节层面的关键词，主要用于实体和局部检索。

本例中原始关键词为：

```json
{
  "high_level": ["空间局部性", "计算机体系结构", "内存访问模式"],
  "low_level": ["缓存", "数据块", "邻近数据"]
}
```

### 3. 课程 JSON 骨架增强

随后 Server 会读取：

```text
HKUDS-LightRAG/course_outlines/计算机系统基础1.json
HKUDS-LightRAG/course_outlines/计算机系统基础2.json
```

内部会先把课程树展平成很多课程路径节点，例如：

```text
计算机系统基础1 > 存储器层次结构 > 局部性原理 > 空间局部性
```

增强器会使用“原始问题 + 原始 high_level / low_level 关键词”做本地候选召回，再让 `qwen3.7-plus` 从候选中精选最相关节点。本例中命中的课程节点是：

```text
计算机系统基础1 > 存储器层次结构 > 局部性原理 > 空间局部性
计算机系统基础1 > 存储器层次结构 > 编写高速缓存友好代码 > 举例：矩阵乘法 > 重排循环顺序实现更好的空间局部性
```

然后从命中节点的路径、兄弟节点、子节点中补充课程关键词。融合后的最终关键词为：

```json
{
  "high_level": [
    "空间局部性",
    "计算机体系结构",
    "内存访问模式",
    "存储器层次结构",
    "局部性原理",
    "编写高速缓存友好代码",
    "举例：矩阵乘法",
    "重排循环顺序实现更好的空间局部性"
  ],
  "low_level": [
    "缓存",
    "数据块",
    "邻近数据",
    "时间局部性",
    "矩阵分块实现更好的时间局部性"
  ]
}
```

这个调试信息可以从 `/query/data` 的下面字段看到：

```text
metadata.keywords
metadata.course_outline_enhancer
```

如果调用方显式传入 `hl_keywords` 或 `ll_keywords`，课程增强器会跳过，系统直接使用调用方传入的关键词。

### 4. 原问题向量检索文本块

文本块检索仍然使用原始问题，而不是改写后的问题。因为当前使用 Qwen3 Embedding 的 instruction-aware retrieval，查询侧会自动加上 query prefix：

```text
Instruct: Given a Computer Systems course learning question, retrieve relevant textbook or lecture passages that answer the question.
Query: 空间局部性是什么？
```

这段文本会被本地 vLLM 的 `Qwen/Qwen3-Embedding-0.6B` 转成 `1024` 维向量，然后去 `chunks_vdb` 里找语义最相似的课件/教材/手册文本块。

本例中 `/query/data` 返回的文本块示例：

```text
chunk 1:
来源：计算机系统基础1：13. 存储器层次结构.pdf 第 57 页
内容预览：局部性原理：程序倾向于使用最近访问过的数据和指令，或是与之临近的数据和指令...

chunk 2:
来源：CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.6.2 Rearranging Loops to Increase Spatial Locality (pp. 679-682)
内容预览：A matrix multiply function is usually implemented using three nested loops...

chunk 3:
来源：计算机系统基础1：13. 存储器层次结构.pdf 第 68 页
内容预览：局部性特征如何导致缓存命中 How locality induces cache hits...
```

### 5. 关键词驱动的图谱检索

除了直接查文本块，LightRAG 还会用关键词查图谱：

```text
low_level keywords
 -> entities_vdb
 -> 找到相关实体及其邻域关系

high_level keywords
 -> relationships_vdb
 -> 找到主题层面的相关关系
```

本例 `/query/data` 的统计信息：

```json
{
  "entities": 17,
  "relationships": 91,
  "chunks": 8,
  "references": 8
}
```

命中的实体示例：

```json
{
  "entity_name": "空间局部性",
  "entity_type": "concept",
  "description": "空间局部性（Spatial Locality）是计算机体系结构中局部性原理的一种重要形式，主要描述了程序在访问内存时的一种特定行为模式...",
  "file_path": "计算机系统基础1：13. 存储器层次结构.pdf 第 57 页<SEP>计算机系统基础1：13. 存储器层次结构.pdf 第 58 页..."
}
```

命中的关系示例：

```json
{
  "src_id": "空间局部性",
  "tgt_id": "高速缓存",
  "description": "空间局部性是利用高速缓存提高性能的重要原理之一，强调步长为1的存储器访问模式。",
  "keywords": "工作原理,缓存优化",
  "file_path": "计算机系统基础1：14. 高速缓存.pdf 第 20 页"
}
```

### 6. 合并、去重和 token budget 截断

LightRAG 会把三路召回结果合并：

```text
文本块召回结果
+ 实体召回结果
+ 关系召回结果
```

然后按配置中的 token budget 控制最终上下文大小：

```text
max_entity_tokens
max_relation_tokens
max_total_tokens
```

本例的处理统计：

```json
{
  "total_entities_found": 17,
  "total_relations_found": 91,
  "entities_after_truncation": 17,
  "relations_after_truncation": 91,
  "merged_chunks_count": 44,
  "final_chunks_count": 8
}
```

这表示系统先从图谱和文本检索中收集到更多候选 chunks，合并去重后，最后保留了 8 个 chunks 进入最终上下文。

如果请求体中设置了 `enable_rerank=true`，则会在候选 chunks 进入最终上下文之前调用本地 BGE Reranker：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "top_k": 8,
  "chunk_top_k": 8,
  "enable_rerank": true
}
```

重排序阶段使用的是：

```text
model = BAAI/bge-reranker-v2-m3
endpoint = http://vllm-rerank:8000/rerank
```

它会对候选 chunks 计算“问题-文档”相关性分数，并按分数重新排序。实际验证时，LightRAG 日志出现过：

```text
Successfully reranked: 8 chunks from 44 original chunks
```

这表示系统先从文本向量、实体邻域、关系检索中收集并合并出 44 个候选 chunks，然后由 BGE Reranker 重新打分排序，最后保留 8 个进入最终上下文。

重排序阶段可以理解成下面这个中间过程。

进入 reranker 前，LightRAG 已经准备好一批候选文本块，结构上类似：

```json
{
  "query": "空间局部性是什么？",
  "candidate_chunks": [
    {
      "source": "计算机系统基础1：13. 存储器层次结构.pdf 第 57 页",
      "content_preview": "局部性原理；时间局部性；空间局部性..."
    },
    {
      "source": "CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.2 Locality (p. 640)",
      "content_preview": "Programs tend to use data and instructions with addresses near or equal to those they have used recently..."
    },
    {
      "source": "CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.6.2 Rearranging Loops to Increase Spatial Locality (pp. 679-682)",
      "content_preview": "Rearranging loops can improve spatial locality in matrix multiplication..."
    },
    {
      "source": "计算机系统基础1：14. 高速缓存.pdf 第 24 页",
      "content_preview": "缓存友好代码、步长为 1 的访问、数组连续访问..."
    }
  ]
}
```

LightRAG 内部会把这些候选内容转换成 Cohere-compatible rerank 请求，发送给容器内服务：

```http
POST http://vllm-rerank:8000/rerank
```

请求体逻辑上类似：

```json
{
  "model": "BAAI/bge-reranker-v2-m3",
  "query": "空间局部性是什么？",
  "documents": [
    "来源：计算机系统基础1：13. 存储器层次结构.pdf 第 57 页\n局部性原理；时间局部性；空间局部性...",
    "来源：CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.2 Locality (p. 640)\nPrograms tend to use data and instructions...",
    "来源：CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.6.2 Rearranging Loops to Increase Spatial Locality (pp. 679-682)\nRearranging loops can improve spatial locality...",
    "来源：计算机系统基础1：14. 高速缓存.pdf 第 24 页\n缓存友好代码、步长为 1 的访问、数组连续访问..."
  ],
  "top_n": 8
}
```

`/rerank` 的返回会带每个候选文档的 `index` 和 `relevance_score`。为了验证模型本身是否会区分相关和不相关内容，单独测试过一个最小例子：

```json
{
  "query": "空间局部性是什么？",
  "documents": [
    "空间局部性是程序倾向于访问相邻内存地址。",
    "网络套接字用于进程间通信。"
  ]
}
```

返回的核心结果是：

```json
{
  "results": [
    {
      "index": 0,
      "relevance_score": 0.9985
    },
    {
      "index": 1,
      "relevance_score": 0.00017
    }
  ]
}
```

这说明 BGE Reranker 会把与“空间局部性”直接相关的文档排到前面，把明显不相关的“网络套接字”压到后面。

在 LightRAG 的 `/query/data` 结果中，最终可以看到重排后的 `data.chunks` 和 `data.references` 顺序。一次开启 `enable_rerank=true` 的实测中，最终进入上下文的前几条来源类似：

```text
1. 计算机系统基础1：14. 高速缓存.pdf 第 24 页
2. 计算机系统基础1：13. 存储器层次结构.pdf 第 57 页
3. 计算机系统基础1：13. 存储器层次结构.pdf 第 68 页
4. CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.5 Writing Cache-Friendly Code (pp. 669-672)
5. 计算机系统基础1：13. 存储器层次结构.pdf 第 58 页
```

注意：当前 `/query/data` 主要展示重排后的 chunks 和 references，不一定直接暴露每条 chunk 的 `rerank_score`。如果要看分数，可以直接调用 `http://localhost:8000/rerank` 做小规模验证；如果要看 LightRAG 是否调用成功，则查看容器日志中的 `Successfully reranked...`。

如果 `enable_rerank=false` 或不传该字段，则跳过这一步，仍使用 LightRAG 原本的向量相似度、图谱关联和内部合并排序。

### 7. 组装上下文并生成答案

最终上下文通常包含：

```text
Knowledge Graph Data
Entity
Relationship
Document Chunks
Reference Document List
```

然后 `qwen3.7-plus` 基于这个上下文生成回答。普通 `/query` 只返回答案和 References；`/query/data` 会返回检索阶段中间产物，适合调试。

本例最终答案会引用课件和教材来源，例如：

```text
空间局部性（Spatial Locality）是计算机体系结构中局部性原理的一种核心形式，主要描述了程序在访问内存时倾向于集中访问邻近地址的数据或指令的行为模式...
```

### 8. References 示例

该问题的 `/query/data` Top References 示例：

```text
1. 计算机系统基础1：13. 存储器层次结构.pdf 第 57 页
2. CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.6.2 Rearranging Loops to Increase Spatial Locality (pp. 679-682)
3. 计算机系统基础1：13. 存储器层次结构.pdf 第 68 页
4. CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.4.5 Issues with Writes (pp. 666-667)
5. CSAPP 3e: Chapter 6 The Memory Hierarchy - 6.2 Locality (p. 640)
```

这些 References 来自上传文档时写入的 `file_source`。课件是按页上传，所以可以回溯到“某个 PDF 第几页”；教材是按小节上传，所以可以回溯到“Chapter / 小节 / 页码范围”。

当前已经配置本地 BGE Reranker，但默认不自动启用。可以在 `/health` 中确认 rerank provider 是否可用：

```text
enable_rerank = true
rerank_binding = cohere
rerank_model = BAAI/bge-reranker-v2-m3
```

是否真正对某次查询启用重排序，由请求体中的 `enable_rerank` 控制：

```json
{
  "query": "空间局部性是什么？",
  "mode": "mix",
  "enable_rerank": true
}
```

如果不传该字段或传 `false`，排序主要来自 LightRAG 的向量相似度、图谱关联和内部合并逻辑；传 `true` 时，会在候选 chunks 上额外调用 BGE Reranker 重新排序。
