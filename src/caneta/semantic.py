"""Meaning-based (multilingual) similarity with local embeddings — optional, off by default.

Model: minishlab/potion-multilingual-128M via fastembed (ONNX, no PyTorch; static embeddings, so
encoding is near-instant). Downloaded once (~0.5 GB) into ~/.caneta/models when the author turns
the feature on; the text never leaves the computer. In a pt/es/en evaluation against the 117-journal
index it put a right journal in the top 3 for 5/5 queries (TF-IDF alone: 4/5); the final journal
score is 0.8 × semantic + 0.2 × TF-IDF, keeping a little exact-term signal.

Used for: journal matching, re-ranking literature search results by closeness to the author's
introduction, and picking full-text passages for the results comparison."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from . import config, extras

MODEL = "minishlab/potion-multilingual-128M"
SEMANTIC_WEIGHT = 0.8


def models_dir():
    return config.APP_DIR / "models"


def _marker():
    return models_dir() / f".ready-{hashlib.sha1(MODEL.encode()).hexdigest()[:10]}"


def ready() -> bool:
    return extras.has("fastembed") and _marker().is_file()


def enabled() -> bool:
    return config.load().semantic and ready()


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL, cache_dir=str(models_dir()))


def enable(log=print) -> None:
    """Install fastembed if needed, download the model, test it, and turn the feature on."""
    if not extras.has("fastembed"):
        extras.install(extras.SEMANTIC_PACKAGES, log)
    log(f"Baixando o modelo {MODEL} (≈0,5 GB, só na primeira vez)…")
    models_dir().mkdir(parents=True, exist_ok=True)
    _model.cache_clear()
    vec = embed(["teste de funcionamento"])
    if not len(vec):
        raise extras.ExtraError("o modelo não gerou vetores")
    _marker().write_text(json.dumps({"model": MODEL}), encoding="utf-8")
    cfg = config.load()
    cfg.semantic = True
    config.save(cfg)
    log("Busca por significado ativada.")


def disable() -> None:
    cfg = config.load()
    cfg.semantic = False
    config.save(cfg)


def embed(texts: list[str]):
    """L2-normalised vectors (numpy array, one row per text)."""
    import numpy as np

    vecs = np.array(list(_model().embed([t or " " for t in texts])), dtype="float32")
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def similarities(query: str, docs: list[str]) -> list[float]:
    """Cosine similarity between one query and many documents."""
    if not docs:
        return []
    vecs = embed([query, *docs])
    return [float(x) for x in vecs[1:] @ vecs[0]]
