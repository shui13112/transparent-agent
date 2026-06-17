"""Cross-encoder reranker for improving RAG retrieval quality."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import List, Tuple

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

logger = logging.getLogger(__name__)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

def _resolve_model_path(model_name: str) -> str:
    """Look up local HF cache for the model; return local path if found."""
    import os as _os

    # Collect unique search roots (dedup in case multiple env vars point same dir)
    roots: set[str] = set()
    hf_home = _os.environ.get("HF_HOME")
    if hf_home:
        roots.add(hf_home)               # direct placement
        roots.add(str(Path(hf_home) / "hub"))  # HF Hub default layout
    llama = _os.environ.get("LLAMA_INDEX_CACHE_DIR")
    if llama:
        roots.add(llama)
    hf_cache = _os.environ.get("HF_HUB_CACHE")
    if hf_cache:
        roots.add(hf_cache)
    # Always include default HuggingFace cache as fallback
    default_cache = str(Path.home() / ".cache" / "huggingface" / "hub")
    roots.add(default_cache)

    dirnames = [
        f"models--{model_name.replace('/', '--')}",
        model_name.replace("/", "--"),
        model_name.split("/")[-1] if "/" in model_name else model_name,
    ]
    for root in roots:
        for dn in dirnames:
            base = Path(root) / dn
            if not base.is_dir():
                base = Path(root) / dn / "snapshots"
                if base.is_dir():
                    snaps = sorted(base.iterdir(), reverse=True)
                    for snap in snaps:
                        if list(snap.glob("*.safetensors")) or list(
                            snap.glob("pytorch_model.bin")
                        ):
                            return str(snap)
                continue
            if list(base.glob("*.safetensors")) or list(
                base.glob("pytorch_model.bin")
            ):
                return str(base)
            # HF Hub cache: model files live in snapshots/<hash>/, not directly in base
            snapshots_dir = base / "snapshots"
            if snapshots_dir.is_dir():
                snaps = sorted(snapshots_dir.iterdir(), reverse=True)
                for snap in snaps:
                    if list(snap.glob("*.safetensors")) or list(
                        snap.glob("pytorch_model.bin")
                    ):
                        return str(snap)
    return model_name


class Reranker:
    """Cross-encoder reranker singleton.

    Uses BAAI/bge-reranker-v2-m3 by default — a multilingual model suitable
    for Chinese-English mixed content. Thread-safe: concurrent calls before the
    first download completes will block on the lock instead of each triggering
    a separate download from HuggingFace.
    """

    _instance: Reranker | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls, model_name: str = "BAAI/bge-reranker-v2-m3") -> Reranker:
        if cls._instance is not None:
            return cls._instance
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(model_name)
            return cls._instance

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model_name = model_name
        self._tokenizer = None
        self._model = None
        self._device = None
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        model_path = _resolve_model_path(self.model_name)
        logger.info(">>> [系统] 加载 Reranker 模型: %s", model_path)

        self._tokenizer = AutoTokenizer.from_pretrained(model_path)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_path)
        self._model.eval()

        self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._model.to(self._device)

    def rerank(
        self, query: str, chunks: List[str], top_k: int | None = None
    ) -> List[Tuple[int, float]]:
        """Rerank chunks against the query.

        Returns list of (original_index, score) sorted by score descending.
        """
        if not chunks:
            return []

        self._ensure_loaded()

        pairs = [[query, chunk] for chunk in chunks]
        inputs = self._tokenizer(
            pairs,
            padding=True,
            truncation=True,
            return_tensors="pt",
            max_length=512,
        ).to(self._device)

        with torch.no_grad():
            scores = (
                self._model(**inputs, return_dict=True)
                .logits.view(-1)
                .float()
                .cpu()
                .numpy()
            )

        indexed_scores = list(enumerate(float(s) for s in scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        if top_k is not None:
            indexed_scores = indexed_scores[:top_k]

        return indexed_scores
