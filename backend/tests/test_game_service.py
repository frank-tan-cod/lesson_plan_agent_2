from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.schemas import GenerateLessonGamesRequest
from backend.app.core.settings import settings
from backend.app.services.game_service import GAME_OUTPUT_DIR, generate_games_for_plan


class GameServiceTests(unittest.TestCase):
    def test_game_outputs_use_public_uploads_directory(self) -> None:
        self.assertEqual(GAME_OUTPUT_DIR, PROJECT_ROOT / "uploads" / "games")

    def test_generate_games_falls_back_and_renders_html(self) -> None:
        plan = SimpleNamespace(
            id=f"plan-{uuid.uuid4()}",
            title="浮力教案",
            subject="物理",
            grade="八年级",
            content={
                "sections": [
                    {"type": "导入", "content": "观察木块在水中的状态。比较漂浮和下沉的差异。"},
                    {"type": "新授", "content": "理解浮力的方向。分析液体对物体的作用。"},
                    {"type": "练习", "content": "结合实验现象判断浮力变化。"},
                ]
            },
        )

        request = GenerateLessonGamesRequest(game_count=3)
        with patch("backend.app.services.game_service._get_llm_client", side_effect=RuntimeError("no llm")):
            games = generate_games_for_plan(plan, request)

        self.assertEqual(len(games), 3)
        self.assertTrue(all(item.get("html_url") for item in games))
        self.assertEqual({item.get("template") for item in games}, {"single_choice", "true_false", "flip_cards"})

        for item in games:
            html_url = str(item.get("html_url") or "")
            self.assertTrue(html_url.startswith(f"{settings.PUBLIC_BASE_URL}/uploads/games/"))
            filename = html_url.split("/")[-1]
            self.assertTrue((GAME_OUTPUT_DIR / filename).exists())


if __name__ == "__main__":
    unittest.main()
