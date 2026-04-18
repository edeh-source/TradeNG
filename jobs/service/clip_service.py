"""
jobs/service/clip_service.py
=============================
CLIP image encoder singleton.

Role in the hybrid system
──────────────────────────
  CLIP is used ONLY for the image side of matching:
    • Encodes portfolio photos into 512-dim vectors.
    • Scores are compared against the job's sentence-transformer text embedding
      using cross-modal cosine similarity.

  Text-to-text similarity is handled entirely by text_encoder.py
  (sentence-transformers), which is more accurate for the purpose.

Why keep CLIP at all?
──────────────────────
  The image↔text cross-modal capability is unique to CLIP.
  An employer who writes "solar panel installation on a Lagos rooftop" can
  match workers whose PORTFOLIO PHOTOS visually look like that — something
  no text-only model can do.

Install
───────
    pip install git+https://github.com/openai/CLIP.git torch torchvision Pillow

Usage
─────
    from jobs.service.clip_service import clip_image_encoder
    vec = clip_image_encoder.encode_image_file("/path/to/photo.jpg")
    vec = clip_image_encoder.encode_image_bytes(raw_bytes)
    vec = clip_image_encoder.encode_text("solar panel installation")  # for job side
"""

import logging
import threading
from io import BytesIO
from typing import List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CLIP_MODEL_NAME  = 'ViT-B/32'
CLIP_EMBED_DIM   = 512


class CLIPImageEncoder:
    """
    Lazy-loading singleton for CLIP's visual encoder.

    Text encoding is also exposed so the job's sentence-transformer embedding
    can be compared against portfolio image embeddings in the same vector space.
    """

    _instance: Optional['CLIPImageEncoder'] = None
    _lock = threading.Lock()

    def __new__(cls) -> 'CLIPImageEncoder':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._model       = None
                    obj._preprocess  = None
                    obj._clip        = None
                    obj._device      = None
                    obj._model_lock  = threading.Lock()
                    cls._instance    = obj
        return cls._instance

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            try:
                import clip
                import torch
                device = 'cuda' if torch.cuda.is_available() else 'cpu'
                logger.info("Loading CLIP model %s on %s …", CLIP_MODEL_NAME, device)
                model, preprocess = clip.load(CLIP_MODEL_NAME, device=device)
                model.eval()
                self._model      = model
                self._preprocess = preprocess
                self._clip       = clip
                self._device     = device
                self._torch      = torch
                logger.info("CLIP image encoder loaded.")
            except Exception as exc:
                logger.exception("Failed to load CLIP model: %s", exc)
                raise

    # ── Image encoding ───────────────────────────────────────────────────────

    def encode_image_file(self, image_path: str) -> List[float]:
        """Encode an image file at the given path. Returns 512-dim float list."""
        self._ensure_loaded()
        try:
            image = Image.open(image_path).convert('RGB')
        except Exception as exc:
            raise ValueError(f"Cannot open image {image_path}: {exc}") from exc
        return self._encode_pil(image)

    def encode_image_bytes(self, image_bytes: bytes) -> List[float]:
        """Encode raw image bytes. Returns 512-dim float list."""
        self._ensure_loaded()
        try:
            image = Image.open(BytesIO(image_bytes)).convert('RGB')
        except Exception as exc:
            raise ValueError(f"Cannot decode image bytes: {exc}") from exc
        return self._encode_pil(image)

    def _encode_pil(self, image: Image.Image) -> List[float]:
        with self._torch.no_grad():
            tensor    = self._preprocess(image).unsqueeze(0).to(self._device)
            embedding = self._model.encode_image(tensor)
            embedding = embedding / embedding.norm(dim=-1, keepdim=True)
            return embedding.cpu().float().numpy()[0].tolist()

    # ── Text encoding (for cross-modal comparison) ───────────────────────────

    def encode_text_for_image_comparison(self, text: str) -> List[float]:
        """
        Encode text with CLIP's text encoder.

        Use this ONLY when comparing text against portfolio images — it puts
        both in the same 512-dim CLIP space so cross-modal cosine similarity
        is meaningful.

        For text-to-text similarity, use text_encoder.encode() instead.
        """
        self._ensure_loaded()
        with self._torch.no_grad():
            tokens    = self._clip.tokenize([text], truncate=True).to(self._device)
            embedding = self._model.encode_text(tokens)
            embedding = embedding / embedding.norm(dim=-1, keepdim=True)
            return embedding.cpu().float().numpy()[0].tolist()

    # ── Similarity ───────────────────────────────────────────────────────────

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        va, vb = np.array(a, np.float32), np.array(b, np.float32)
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))

    @staticmethod
    def batch_cosine_similarity(
        query: List[float], candidates: List[List[float]]
    ) -> List[float]:
        if not candidates:
            return []
        q = np.array(query, np.float32)
        C = np.array(candidates, np.float32)
        nq = np.linalg.norm(q)
        if nq == 0:
            return [0.0] * len(candidates)
        q = q / nq
        norms = np.linalg.norm(C, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-8, norms)
        return ((C / norms) @ q).tolist()


# Module-level singleton
clip_image_encoder = CLIPImageEncoder()