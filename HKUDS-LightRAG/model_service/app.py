import base64
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForSequenceClassification, AutoTokenizer


EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
MODEL_DEVICE = os.getenv("MODEL_DEVICE", "cpu")
MODEL_MAX_LENGTH = int(os.getenv("MODEL_MAX_LENGTH", "8192"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "4"))
RERANK_BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "4"))


class ModelState:
    embedding_model: SentenceTransformer | None = None
    rerank_tokenizer: Any | None = None
    rerank_model: AutoModelForSequenceClassification | None = None
    started_at: float | None = None
    embedding_lock = threading.Lock()
    rerank_lock = threading.Lock()


state = ModelState()


def _resolve_device() -> str:
    if MODEL_DEVICE == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if MODEL_DEVICE == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("MODEL_DEVICE=cuda but CUDA is not available")
    return MODEL_DEVICE


DEVICE = _resolve_device()


def _load_models() -> None:
    state.started_at = time.time()

    state.embedding_model = SentenceTransformer(
        EMBEDDING_MODEL,
        device=DEVICE,
        cache_folder=os.getenv("SENTENCE_TRANSFORMERS_HOME"),
    )
    state.embedding_model.max_seq_length = MODEL_MAX_LENGTH

    state.rerank_tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
    state.rerank_model = AutoModelForSequenceClassification.from_pretrained(
        RERANK_MODEL
    )
    state.rerank_model.to(DEVICE)
    state.rerank_model.eval()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load_models()
    yield


app = FastAPI(title="Computer Systems Local Model API", lifespan=lifespan)


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str = EMBEDDING_MODEL
    encoding_format: Literal["float", "base64"] = "float"
    dimensions: int | None = None


class RerankRequest(BaseModel):
    query: str
    documents: list[str] = Field(default_factory=list)
    model: str = RERANK_MODEL
    top_n: int | None = None
    return_documents: bool | None = None


def _as_text_list(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        return [value]
    return value


def _approx_tokens(texts: list[str]) -> int:
    return sum(max(1, len(text) // 4) for text in texts)


def _batched(items: list[Any], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield start, items[start : start + batch_size]


def _embed(texts: list[str]) -> np.ndarray:
    if state.embedding_model is None:
        raise HTTPException(status_code=503, detail="Embedding model is not loaded")

    vectors: list[np.ndarray] = []
    with state.embedding_lock:
        for _, batch in _batched(texts, EMBED_BATCH_SIZE):
            batch_vectors = state.embedding_model.encode(
                batch,
                batch_size=len(batch),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            vectors.append(np.asarray(batch_vectors, dtype=np.float32))

    if not vectors:
        return np.empty((0, 1024), dtype=np.float32)
    return np.vstack(vectors)


def _score_pairs(query: str, documents: list[str]) -> list[float]:
    if state.rerank_tokenizer is None or state.rerank_model is None:
        raise HTTPException(status_code=503, detail="Rerank model is not loaded")

    scores: list[float] = []
    with state.rerank_lock, torch.no_grad():
        for _, batch in _batched(documents, RERANK_BATCH_SIZE):
            encoded = state.rerank_tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=True,
                max_length=MODEL_MAX_LENGTH,
                return_tensors="pt",
            )
            encoded = {key: value.to(DEVICE) for key, value in encoded.items()}
            logits = state.rerank_model(**encoded).logits
            logits = logits.reshape(-1).float()
            batch_scores = torch.sigmoid(logits).cpu().tolist()
            scores.extend(float(score) for score in batch_scores)
    return scores


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "device": DEVICE,
        "embedding_model": EMBEDDING_MODEL,
        "rerank_model": RERANK_MODEL,
        "embedding_loaded": state.embedding_model is not None,
        "rerank_loaded": state.rerank_model is not None,
        "max_length": MODEL_MAX_LENGTH,
        "embed_batch_size": EMBED_BATCH_SIZE,
        "rerank_batch_size": RERANK_BATCH_SIZE,
    }


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": EMBEDDING_MODEL,
                "object": "model",
                "owned_by": "local-fastapi",
            },
            {
                "id": RERANK_MODEL,
                "object": "model",
                "owned_by": "local-fastapi",
            },
        ],
    }


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
    texts = _as_text_list(request.input)
    vectors = _embed(texts)

    if request.dimensions is not None and request.dimensions != vectors.shape[1]:
        raise HTTPException(
            status_code=400,
            detail=(
                "This local embedding service does not support dimension reduction; "
                f"requested {request.dimensions}, actual {vectors.shape[1]}"
            ),
        )

    data = []
    for index, vector in enumerate(vectors):
        if request.encoding_format == "base64":
            embedding: str | list[float] = base64.b64encode(
                vector.astype(np.float32).tobytes()
            ).decode("ascii")
        else:
            embedding = vector.astype(float).tolist()
        data.append({"object": "embedding", "index": index, "embedding": embedding})

    prompt_tokens = _approx_tokens(texts)
    return {
        "object": "list",
        "model": request.model,
        "data": data,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "total_tokens": prompt_tokens,
        },
    }


@app.post("/rerank")
@app.post("/v1/rerank")
def rerank(request: RerankRequest) -> dict[str, Any]:
    if not request.documents:
        return {
            "id": "rerank-empty",
            "model": request.model,
            "results": [],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    scores = _score_pairs(request.query, request.documents)
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
    if request.top_n is not None:
        ranked = ranked[: request.top_n]

    results = []
    for index, score in ranked:
        item: dict[str, Any] = {
            "index": index,
            "relevance_score": score,
        }
        if request.return_documents:
            item["document"] = {"text": request.documents[index]}
        results.append(item)

    token_count = _approx_tokens([request.query, *request.documents])
    return {
        "id": f"rerank-{int(time.time() * 1000)}",
        "model": request.model,
        "results": results,
        "usage": {
            "prompt_tokens": token_count,
            "total_tokens": token_count,
        },
    }
