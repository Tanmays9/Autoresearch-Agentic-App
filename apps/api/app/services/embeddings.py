from __future__ import annotations

import math
import threading

from ..config import get_settings


_model_lock = threading.Lock()
_embedding_model = None
_model_initialized = False


def _model():
    global _embedding_model, _model_initialized
    if _model_initialized:
        return _embedding_model
    # The crawler scores several pages concurrently.  FastEmbed downloads and
    # opens model files during construction, so initialization must happen only
    # once even when the first batch arrives on several worker threads.
    with _model_lock:
        if _model_initialized:
            return _embedding_model
        settings = get_settings()
        if not settings.enable_embeddings:
            _model_initialized = True
            return None
        try:
            from fastembed import TextEmbedding

            _embedding_model = TextEmbedding(model_name=settings.embedding_model)
        except Exception:
            _embedding_model = None
        _model_initialized = True
        return _embedding_model


def embed_text(text: str) -> list[float] | None:
    # ONNX sessions are reusable, but serializing calls keeps the lightweight
    # local worker predictable while crawl targets themselves remain parallel.
    model = _model()
    if model is None:
        return None
    with _model_lock:
        return [float(value) for value in next(model.embed([text]))]


def reset_embedding_model_for_tests() -> None:
    """Clear process-local model state for isolated settings tests."""
    global _embedding_model, _model_initialized
    with _model_lock:
        _embedding_model = None
        _model_initialized = False


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0
