# Local CPU Model API

This service replaces the previous vLLM embedding/rerank containers with a
CPU-only FastAPI service.

It exposes:

- `GET /health`
- `GET /v1/models`
- `POST /v1/embeddings`
- `POST /rerank`
- `POST /v1/rerank`

The service is designed for LightRAG's existing OpenAI-compatible embedding
binding and Cohere-compatible rerank binding.

Default models:

- Embedding: `Qwen/Qwen3-Embedding-0.6B`
- Rerank: `BAAI/bge-reranker-v2-m3`

The service is CPU-first and does not require NVIDIA runtime or vLLM.
