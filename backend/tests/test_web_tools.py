from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.tools.web_tools import _build_degraded_search_result


class WebToolsTests(unittest.TestCase):
    def test_degraded_search_result_is_non_blocking(self) -> None:
        result = _build_degraded_search_result("AI demo", RuntimeError("network down"))

        self.assertTrue(result["ok"])
        self.assertTrue(result["degraded"])
        self.assertEqual(result["provider"], "unavailable")
        self.assertEqual(result["results"], [])
        self.assertIn("外部搜索当前不可用", result["message"])
        self.assertIn("模型已有知识继续设置", result["message"])


if __name__ == "__main__":
    unittest.main()
