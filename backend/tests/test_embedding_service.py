from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from backend.app.services.embedding_service import EmbeddingService


class _FakeModel:
    def encode(self, texts: list[str], normalize_embeddings: bool = True):
        return [[float(len(text)), 1.0 if normalize_embeddings else 0.0] for text in texts]


class EmbeddingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        EmbeddingService._instance = None

    def tearDown(self) -> None:
        EmbeddingService._instance = None

    def test_embed_prefers_local_cache_before_remote_download(self) -> None:
        calls: list[bool] = []

        def fake_sentence_transformer(model_name: str, local_files_only: bool = False):
            self.assertEqual(model_name, "BAAI/bge-small-zh-v1.5")
            calls.append(local_files_only)
            self.assertTrue(local_files_only)
            return _FakeModel()

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = fake_sentence_transformer

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            service = EmbeddingService()
            vectors = service.embed(["abc"])

        self.assertEqual(calls, [True])
        self.assertEqual(vectors, [[3.0, 1.0]])

    def test_embed_skips_remote_download_when_mirror_unavailable(self) -> None:
        calls: list[bool] = []

        def fake_sentence_transformer(model_name: str, local_files_only: bool = False):
            self.assertEqual(model_name, "BAAI/bge-small-zh-v1.5")
            calls.append(local_files_only)
            if local_files_only:
                raise OSError("not cached")
            raise AssertionError("remote download should not be attempted")

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = fake_sentence_transformer

        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            service = EmbeddingService()
            with patch.object(service, "_remote_model_accessible", return_value=False):
                with self.assertRaises(RuntimeError) as exc_info:
                    service.embed(["abc"])

        self.assertEqual(calls, [True])
        self.assertIn("镜像当前不可达", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
