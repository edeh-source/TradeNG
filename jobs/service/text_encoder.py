"""
jobs/service/text_encoder.py
=============================
Sentence-transformer singleton for text-to-text semantic similarity.

Responsibilities
────────────────
  • Load the sentence-transformer model ONCE per process (lazy singleton).
  • Expose encode() and encode_batch() with L2-normalised outputs.
  • Never touch the database.

Model choice: all-mpnet-base-v2
────────────────────────────────
  Chosen because:
    • Best-in-class semantic similarity scores on STSB / STS benchmarks.
    • 768-dim embeddings — richer than CLIP's 512-dim.
    • Handles up to 512 tokens — covers any realistic bio or job description.
    • ~420 MB model size, ~30–80 ms/inference on CPU.

  Alternatives (if CPU budget is tight):
    • all-MiniLM-L6-v2  — 90% of the quality at 5× the speed, 80 MB.
    • paraphrase-multilingual-mpnet-base-v2  — if you add Yoruba/Hausa support.

Install
───────
    pip install sentence-transformers

Usage
─────
    from jobs.service.text_encoder import text_encoder
    vec = text_encoder.encode("Electrician Lagos solar panel installation")
"""

import logging
import threading
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Change this to 'all-MiniLM-L6-v2' for a faster, lighter model.
TEXT_MODEL_NAME = 'all-mpnet-base-v2'
EMBEDDING_DIM   = 768   # matches all-mpnet-base-v2 output


class TextEncoder:
    """
    Lazy-loading singleton for the sentence-transformer text encoder.

    Usage:
        from jobs.service.text_encoder import text_encoder
        vec   = text_encoder.encode("some text")
        vecs  = text_encoder.encode_batch(["text1", "text2"])
        score = text_encoder.cosine_similarity(vec_a, vec_b)
    """

    _instance: Optional['TextEncoder'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'TextEncoder':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._model       = None
                    obj._model_lock  = threading.Lock()
                    cls._instance    = obj
        return cls._instance

    # ── Private ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load the model on first call. Subsequent calls are a no-op."""
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
                logger.info("Loading sentence-transformer model %s …", TEXT_MODEL_NAME)
                self._model = SentenceTransformer(TEXT_MODEL_NAME)
                logger.info("Text encoder loaded (dim=%d).", EMBEDDING_DIM)
            except Exception as exc:
                logger.exception("Failed to load text encoder: %s", exc)
                raise

    # ── Public API ───────────────────────────────────────────────────────────

    def encode(self, text: str) -> List[float]:
        """
        Encode a single text string into a normalised 768-dim float list.

        The model already L2-normalises output when normalize_embeddings=True,
        so cosine_similarity is equivalent to a dot product.
        """
        self._ensure_loaded()
        text = text.strip()
        if not text:
            raise ValueError("Cannot encode an empty string.")
        vec = self._model.encode(
            text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.tolist()

    def encode_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Encode a list of strings in one batched forward pass.
        Roughly 10–50× faster than encoding one at a time.

        Empty strings in the list are replaced with a zero vector to avoid
        crashing the batch — callers should filter them out beforehand.
        """
        self._ensure_loaded()
        if not texts:
            return []

        # Mask empty strings — encode a placeholder, then zero the output
        placeholders = {i: '' for i, t in enumerate(texts) if not t.strip()}
        safe_texts   = [t if t.strip() else 'placeholder' for t in texts]

        vecs = self._model.encode(
            safe_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )

        result = vecs.tolist()
        for idx in placeholders:
            result[idx] = [0.0] * EMBEDDING_DIM

        return result

    @property
    def embedding_dim(self) -> int:
        return EMBEDDING_DIM

    # ── Similarity helpers ───────────────────────────────────────────────────

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        """
        Cosine similarity between two vectors.
        Because encode() normalises outputs, this equals the dot product.
        Returns float in [-1.0, 1.0]. For well-formed text pairs, expect 0.4–0.95.
        """
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))

    @staticmethod
    def batch_cosine_similarity(
        query: List[float],
        candidates: List[List[float]],
    ) -> List[float]:
        """
        Vectorised cosine similarity: one query vs many candidates.
        Single NumPy matrix-multiply — O(N·D) vs O(N·D) Python loop.

        Returns a list of floats in the same order as `candidates`.
        """
        if not candidates:
            return []
        q = np.array(query, dtype=np.float32)
        C = np.array(candidates, dtype=np.float32)   # shape (N, 768)

        nq = np.linalg.norm(q)
        if nq == 0:
            return [0.0] * len(candidates)
        q = q / nq

        norms = np.linalg.norm(C, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-8, norms)
        C = C / norms

        return (C @ q).tolist()


# Module-level singleton — import this anywhere
text_encoder = TextEncoder()