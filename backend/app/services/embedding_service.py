"""Embedding wrapper for semantic retrieval."""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logger = logging.getLogger(__name__)
_MODEL_PROBE_TIMEOUT_SECONDS = 2.0
_MODEL_PROBE_CACHE_SECONDS = 300.0


class EmbeddingService:
    """Singleton wrapper around sentence-transformers."""

    _instance: "EmbeddingService | None" = None
    _instance_lock = Lock()

    def __new__(cls, model_name: str = "BAAI/bge-small-zh-v1.5") -> "EmbeddingService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance.model_name = model_name
                    instance._model = None
                    instance._model_lock = Lock()
                    instance._remote_probe_at = 0.0
                    instance._remote_probe_ok: bool | None = None
                    cls._instance = instance
        return cls._instance

    def _load_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:  # pragma: no cover - depends on optional package
                        raise RuntimeError("缺少 sentence-transformers 依赖，无法生成向量。") from exc

                    try:
                        self._model = SentenceTransformer(self.model_name, local_files_only=True)
                        return self._model
                    except Exception as exc:
                        local_error = exc

                    if not self._remote_model_accessible():
                        raise RuntimeError(
                            f"嵌入模型 {self.model_name} 本地未缓存，且镜像当前不可达，已跳过下载。"
                        ) from local_error

                    try:
                        self._model = SentenceTransformer(self.model_name)
                    except Exception as exc:
                        self._remote_probe_at = time.monotonic()
                        self._remote_probe_ok = False
                        raise RuntimeError(f"加载嵌入模型 {self.model_name} 失败：{exc}") from exc
        return self._model

    def _remote_model_accessible(self) -> bool:
        now = time.monotonic()
        if (
            self._remote_probe_ok is not None
            and now - self._remote_probe_at < _MODEL_PROBE_CACHE_SECONDS
        ):
            return self._remote_probe_ok

        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        url = f"{endpoint}/{self.model_name}/resolve/main/./modules.json"
        request = Request(url, method="HEAD")

        ok = False
        try:
            with urlopen(request, timeout=_MODEL_PROBE_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                ok = 200 <= int(status) < 400
        except HTTPError as exc:
            logger.warning("Embedding mirror probe failed for %s: HTTP %s", self.model_name, exc.code)
        except URLError as exc:
            logger.warning("Embedding mirror probe failed for %s: %s", self.model_name, exc.reason)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding mirror probe failed for %s: %s", self.model_name, exc)

        self._remote_probe_at = now
        self._remote_probe_ok = ok
        return ok

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into dense vectors."""
        if not texts:
            return []

        model = self._load_model()
        vectors = model.encode(texts, normalize_embeddings=True)
        if hasattr(vectors, "tolist"):
            return vectors.tolist()
        return [list(item) for item in vectors]
