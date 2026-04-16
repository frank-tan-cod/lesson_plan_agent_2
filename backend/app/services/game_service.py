"""Mini-game generation and HTML rendering for lesson plans."""

from __future__ import annotations

import html
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..core.settings import require_llm_api_key, settings
from ..schemas import GenerateLessonGamesRequest, MiniGamePayload

logger = logging.getLogger(__name__)

GAME_TEMPLATES = ("single_choice", "true_false", "flip_cards")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPLOADS_DIR = PROJECT_ROOT / "uploads"
GAME_OUTPUT_DIR = UPLOADS_DIR / "games"
MAX_PLAN_JSON_CHARS = 12000


class GameGenerationError(RuntimeError):
    """Raised when mini-game generation fails completely."""


def generate_games_for_plan(
    plan: Any,
    request: GenerateLessonGamesRequest,
    *,
    llm_client: OpenAI | None = None,
) -> list[dict[str, Any]]:
    """Generate a list of normalized mini-game payloads for one lesson plan."""
    content = plan.content if isinstance(plan.content, dict) else {}
    raw_sections = content.get("sections")
    sections = [item for item in raw_sections if isinstance(item, dict)] if isinstance(raw_sections, list) else []
    if not sections:
        raise GameGenerationError("当前教案没有可用于生成小游戏的章节内容。")

    templates = _select_templates(request.game_count, request.templates)
    generated = _generate_with_llm(
        plan=plan,
        sections=sections,
        game_count=request.game_count,
        templates=templates,
        llm_client=llm_client,
    )
    if generated:
        return _render_html_outputs(plan_id=str(plan.id), games=generated)

    fallback = _generate_with_fallback(sections=sections, templates=templates)
    if not fallback:
        raise GameGenerationError("未能从当前教案提取出适合生成小游戏的知识点。")
    return _render_html_outputs(plan_id=str(plan.id), games=fallback)


def _generate_with_llm(
    *,
    plan: Any,
    sections: list[dict[str, Any]],
    game_count: int,
    templates: list[str],
    llm_client: OpenAI | None,
) -> list[dict[str, Any]]:
    """Attempt to generate games through the configured LLM."""
    try:
        client = llm_client or _get_llm_client()
    except Exception as exc:  # noqa: BLE001
        logger.info("Mini-game LLM unavailable, fallback to heuristic generator: %s", exc)
        return []

    prompt = _build_generation_prompt(plan=plan, sections=sections, game_count=game_count, templates=templates)
    try:
        response = client.chat.completions.create(
            model=settings.MODEL_NAME,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是课堂互动设计师。"
                        "请从当前教案中提取最适合做课堂小游戏的知识点，只输出结构化 JSON。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            stream=False,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mini-game LLM generation failed, fallback to heuristic generator: %s", exc)
        return []

    return _normalize_generated_games(payload, templates=templates, fallback_sections=sections)


def _build_generation_prompt(
    *,
    plan: Any,
    sections: list[dict[str, Any]],
    game_count: int,
    templates: list[str],
) -> str:
    """Build the LLM prompt for mini-game generation."""
    plan_json = json.dumps(
        {
            "title": getattr(plan, "title", ""),
            "subject": getattr(plan, "subject", ""),
            "grade": getattr(plan, "grade", ""),
            "sections": sections,
        },
        ensure_ascii=False,
        indent=2,
    )
    if len(plan_json) > MAX_PLAN_JSON_CHARS:
        plan_json = f"{plan_json[:MAX_PLAN_JSON_CHARS]}\n...（已截断）"

    return f"""请根据下面教案生成 {game_count} 个课堂小游戏，模板只允许使用：{", ".join(templates)}。

教案 JSON：
{plan_json}

输出要求：
1. 只输出 JSON 对象，不要解释。
2. 顶层结构：
{{
  "games": [
    {{
      "template": "single_choice 或 true_false 或 flip_cards",
      "title": "游戏标题",
      "description": "给学生看的简短玩法说明",
      "source_section": "对应章节名",
      "learning_goal": "本游戏想巩固的知识点",
      "data": {{}}
    }}
  ]
}}
3. `single_choice` 的 `data` 结构：
{{
  "questions": [
    {{
      "stem": "题干",
      "options": ["A", "B", "C"],
      "answer": "正确选项原文",
      "explanation": "一句简短解释"
    }}
  ]
}}
4. `true_false` 的 `data` 结构：
{{
  "statements": [
    {{
      "statement": "判断句",
      "answer": true,
      "explanation": "一句简短解释"
    }}
  ]
}}
5. `flip_cards` 的 `data` 结构：
{{
  "cards": [
    {{
      "front": "正面短词",
      "back": "背面解释"
    }}
  ]
}}
6. 每个游戏只聚焦一个知识点，内容面向课堂投影，语言短促清楚。
7. 不要生成超出教案的知识点，不要输出空数组。"""


def _normalize_generated_games(
    payload: Any,
    *,
    templates: list[str],
    fallback_sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize the LLM response into validated game payloads."""
    if not isinstance(payload, dict):
        return []
    raw_games = payload.get("games")
    if not isinstance(raw_games, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_games):
        if not isinstance(item, dict):
            continue
        template = str(item.get("template") or "").strip()
        if template not in templates:
            template = templates[index % len(templates)]
        section = _resolve_section_for_game(item.get("source_section"), fallback_sections, index)
        game = _normalize_single_game(
            raw=item,
            template=template,
            fallback_section=section,
            index=index,
        )
        if game is not None:
            normalized.append(game)
    return normalized


def _resolve_section_for_game(source_section: Any, sections: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Choose the best-matching section to anchor the generated game."""
    target = str(source_section or "").strip().lower()
    for section in sections:
        label = str(section.get("type") or section.get("title") or section.get("name") or "").strip().lower()
        if target and label == target:
            return section
    return sections[min(index, len(sections) - 1)]


def _normalize_single_game(
    *,
    raw: dict[str, Any],
    template: str,
    fallback_section: dict[str, Any],
    index: int,
) -> dict[str, Any] | None:
    """Normalize one game while enforcing template-specific data shape."""
    label = _section_label(fallback_section, index)
    base_payload = {
        "id": str(raw.get("id") or f"game_{uuid.uuid4().hex[:8]}"),
        "template": template,
        "title": str(raw.get("title") or f"{label}互动练习").strip(),
        "description": str(raw.get("description") or "根据课堂内容完成互动挑战。").strip(),
        "source_section": str(raw.get("source_section") or label).strip() or label,
        "learning_goal": str(raw.get("learning_goal") or _first_point(fallback_section) or label).strip() or label,
        "data": _normalize_template_data(template, raw.get("data"), fallback_section),
    }
    try:
        return MiniGamePayload.model_validate(base_payload).model_dump(exclude_none=True)
    except Exception:
        return None


def _normalize_template_data(template: str, raw_data: Any, section: dict[str, Any]) -> dict[str, Any]:
    """Coerce one template payload into a predictable structure."""
    data = dict(raw_data) if isinstance(raw_data, dict) else {}
    points = _section_points(section)
    if template == "single_choice":
        questions = data.get("questions")
        if not isinstance(questions, list) or not questions:
            return _fallback_single_choice_data(section, points)
        normalized_questions: list[dict[str, Any]] = []
        for question in questions[:3]:
            if not isinstance(question, dict):
                continue
            options = [
                str(item).strip()
                for item in question.get("options", [])
                if str(item).strip()
            ]
            answer = str(question.get("answer") or "").strip()
            stem = str(question.get("stem") or "").strip()
            if not stem or len(options) < 2 or not answer:
                continue
            if answer not in options:
                options = [answer, *options][:4]
            normalized_questions.append(
                {
                    "stem": stem,
                    "options": options[:4],
                    "answer": answer,
                    "explanation": str(question.get("explanation") or "").strip(),
                }
            )
        return {"questions": normalized_questions or _fallback_single_choice_data(section, points)["questions"]}

    if template == "true_false":
        statements = data.get("statements")
        if not isinstance(statements, list) or not statements:
            return _fallback_true_false_data(section, points)
        normalized_statements: list[dict[str, Any]] = []
        for statement in statements[:4]:
            if not isinstance(statement, dict):
                continue
            text = str(statement.get("statement") or "").strip()
            if not text:
                continue
            normalized_statements.append(
                {
                    "statement": text,
                    "answer": bool(statement.get("answer")),
                    "explanation": str(statement.get("explanation") or "").strip(),
                }
            )
        return {"statements": normalized_statements or _fallback_true_false_data(section, points)["statements"]}

    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        return _fallback_flip_cards_data(section, points)
    normalized_cards: list[dict[str, Any]] = []
    for card in cards[:6]:
        if not isinstance(card, dict):
            continue
        front = str(card.get("front") or "").strip()
        back = str(card.get("back") or "").strip()
        if front and back:
            normalized_cards.append({"front": front, "back": back})
    return {"cards": normalized_cards or _fallback_flip_cards_data(section, points)["cards"]}


def _generate_with_fallback(*, sections: list[dict[str, Any]], templates: list[str]) -> list[dict[str, Any]]:
    """Generate basic but usable games without an LLM."""
    selected_sections = [section for section in sections if _section_points(section)] or sections
    games: list[dict[str, Any]] = []
    for index, template in enumerate(templates):
        section = selected_sections[min(index, len(selected_sections) - 1)]
        label = _section_label(section, index)
        points = _section_points(section)
        if template == "single_choice":
            data = _fallback_single_choice_data(section, points)
            title = f"{label}选择挑战"
            description = "选出最符合本节内容的一项。"
        elif template == "true_false":
            data = _fallback_true_false_data(section, points)
            title = f"{label}判断快答"
            description = "判断下面说法是否正确。"
        else:
            data = _fallback_flip_cards_data(section, points)
            title = f"{label}翻卡记忆"
            description = "点击卡片，快速回顾关键词和解释。"
        games.append(
            MiniGamePayload(
                id=f"game_{uuid.uuid4().hex[:8]}",
                template=template,
                title=title,
                description=description,
                source_section=label,
                learning_goal=_first_point(section) or label,
                data=data,
            ).model_dump(exclude_none=True)
        )
    return games


def _fallback_single_choice_data(section: dict[str, Any], points: list[str]) -> dict[str, Any]:
    """Build a multiple-choice game from one section summary."""
    correct = points[0] if points else f"理解{_section_label(section, 0)}的核心内容"
    distractors = [
        f"只需要记住标题，不用理解原因",
        f"课堂中不需要观察或比较现象",
        f"任何结论都不必联系本节知识点",
    ]
    options = [correct, distractors[0], distractors[1]]
    return {
        "questions": [
            {
                "stem": f"下面哪一项最符合“{_section_label(section, 0)}”这一环节的学习重点？",
                "options": options,
                "answer": correct,
                "explanation": correct,
            },
            {
                "stem": "如果要用一句话概括这一环节，应该优先抓住什么？",
                "options": [correct, distractors[1], distractors[2]],
                "answer": correct,
                "explanation": "先抓住本节真正要学生理解的核心点。",
            },
        ]
    }


def _fallback_true_false_data(section: dict[str, Any], points: list[str]) -> dict[str, Any]:
    """Build a true/false game from one section summary."""
    truths = points[:2] or [f"本节内容聚焦{_section_label(section, 0)}。"]
    statements = [
        {
            "statement": truths[0],
            "answer": True,
            "explanation": "这条内容来自当前教案章节。",
        },
        {
            "statement": f"{_section_label(section, 0)}只要求死记标题，不需要理解过程。",
            "answer": False,
            "explanation": "课堂任务通常强调理解、观察、比较或应用，而不只是记标题。",
        },
    ]
    if len(truths) > 1:
        statements.append(
            {
                "statement": truths[1],
                "answer": True,
                "explanation": "这也是当前章节里的有效知识点。",
            }
        )
    return {"statements": statements}


def _fallback_flip_cards_data(section: dict[str, Any], points: list[str]) -> dict[str, Any]:
    """Build a flip-card game from one section summary."""
    cards = []
    for point in (points[:4] or [f"回顾{_section_label(section, 0)}", str(section.get("content") or "").strip() or "暂无内容"]):
        front = point[:14].rstrip("，。；：") or _section_label(section, 0)
        cards.append(
            {
                "front": front,
                "back": point,
            }
        )
    return {"cards": cards[:6]}


def _render_html_outputs(*, plan_id: str, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render every game into a static HTML file under `/uploads/games`."""
    GAME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for index, game in enumerate(games):
        payload = MiniGamePayload.model_validate(game)
        filename = f"{plan_id}-{payload.id or index + 1}.html"
        html_path = GAME_OUTPUT_DIR / filename
        html_path.write_text(_render_game_html(payload), encoding="utf-8")
        public_url = _build_public_game_url(filename)
        rendered.append(
            payload.model_copy(update={"html_url": public_url}).model_dump(exclude_none=True)
        )
    return rendered


def _build_public_game_url(filename: str) -> str:
    """Build the externally reachable game URL used by preview and PPT export."""
    return f"{settings.PUBLIC_BASE_URL}/uploads/games/{filename}"


def _render_game_html(game: MiniGamePayload) -> str:
    """Render one HTML page for a mini-game."""
    json_payload = json.dumps(game.model_dump(exclude_none=True), ensure_ascii=False)
    title = html.escape(game.title or "课堂小游戏")
    description = html.escape(game.description or "")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: linear-gradient(135deg, #f7f4ea 0%, #eef7ff 100%);
      --card: rgba(255, 255, 255, 0.86);
      --ink: #183149;
      --muted: #5c6f82;
      --accent: #ff7a18;
      --accent-soft: #ffe2ca;
      --border: rgba(24, 49, 73, 0.12);
      --success: #2d8a52;
      --danger: #c54848;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--ink);
      padding: 24px;
    }}
    .shell {{
      max-width: 980px;
      margin: 0 auto;
      background: var(--card);
      backdrop-filter: blur(14px);
      border: 1px solid var(--border);
      border-radius: 28px;
      box-shadow: 0 18px 50px rgba(24, 49, 73, 0.08);
      overflow: hidden;
    }}
    .hero {{
      padding: 28px 28px 18px;
      background:
        radial-gradient(circle at top right, rgba(255, 122, 24, 0.14), transparent 30%),
        radial-gradient(circle at top left, rgba(40, 122, 168, 0.14), transparent 28%);
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .hero h1 {{
      margin: 14px 0 10px;
      font-size: clamp(28px, 4vw, 42px);
      line-height: 1.08;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
      max-width: 720px;
    }}
    .content {{
      padding: 0 28px 28px;
    }}
    .stack {{
      display: grid;
      gap: 16px;
    }}
    .card {{
      border-radius: 22px;
      background: #fff;
      border: 1px solid var(--border);
      padding: 18px;
    }}
    .question-title {{
      margin: 0 0 12px;
      font-size: 18px;
      line-height: 1.55;
    }}
    .options {{
      display: grid;
      gap: 10px;
    }}
    .option-btn, .judge-btn, .submit-btn {{
      width: 100%;
      border: 0;
      border-radius: 16px;
      padding: 12px 14px;
      background: #f6f8fb;
      color: var(--ink);
      text-align: left;
      font-size: 15px;
      cursor: pointer;
      transition: transform 140ms ease, background 140ms ease, color 140ms ease;
    }}
    .option-btn:hover, .judge-btn:hover, .submit-btn:hover {{
      transform: translateY(-1px);
      background: var(--accent-soft);
    }}
    .option-btn.selected, .judge-btn.selected {{
      background: #183149;
      color: #fff;
    }}
    .option-btn.correct, .judge-btn.correct {{
      background: rgba(45, 138, 82, 0.14);
      color: var(--success);
    }}
    .option-btn.wrong, .judge-btn.wrong {{
      background: rgba(197, 72, 72, 0.12);
      color: var(--danger);
    }}
    .feedback {{
      margin-top: 12px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--muted);
    }}
    .toolbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-top: 8px;
    }}
    .score {{
      font-weight: 700;
      color: var(--ink);
    }}
    .judge-row {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 12px;
    }}
    .flip-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    }}
    .flip-card {{
      position: relative;
      min-height: 160px;
      border: 1px solid var(--border);
      border-radius: 22px;
      background: transparent;
      perspective: 1000px;
      cursor: pointer;
    }}
    .flip-inner {{
      position: absolute;
      inset: 0;
      transition: transform 260ms ease;
      transform-style: preserve-3d;
    }}
    .flip-card.is-open .flip-inner {{
      transform: rotateY(180deg);
    }}
    .flip-face {{
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 18px;
      border-radius: 22px;
      backface-visibility: hidden;
      text-align: center;
      line-height: 1.65;
    }}
    .flip-front {{
      background: linear-gradient(160deg, #fff7ef 0%, #fff 100%);
      font-weight: 700;
    }}
    .flip-back {{
      transform: rotateY(180deg);
      background: linear-gradient(160deg, #ebf5ff 0%, #fff 100%);
      color: var(--muted);
    }}
    @media (max-width: 640px) {{
      body {{ padding: 14px; }}
      .hero, .content {{ padding-left: 18px; padding-right: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="eyebrow">Mini Game</div>
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
    <section class="content">
      <div id="app"></div>
    </section>
  </main>
  <script>
    const game = {json_payload};
    const app = document.getElementById("app");

    function createButton(text, className) {{
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = text;
      return button;
    }}

    function renderSingleChoice() {{
      const wrapper = document.createElement("div");
      wrapper.className = "stack";
      const questions = Array.isArray(game.data?.questions) ? game.data.questions : [];
      const state = new Array(questions.length).fill(null);
      const feedback = [];

      questions.forEach((question, index) => {{
        const card = document.createElement("section");
        card.className = "card";
        const title = document.createElement("h2");
        title.className = "question-title";
        title.textContent = `${{index + 1}}. ${{question.stem || "请选择正确答案"}}`;
        card.appendChild(title);

        const options = document.createElement("div");
        options.className = "options";

        (Array.isArray(question.options) ? question.options : []).forEach((optionText) => {{
          const button = createButton(optionText, "option-btn");
          button.addEventListener("click", () => {{
            state[index] = optionText;
            [...options.children].forEach((item) => item.classList.remove("selected"));
            button.classList.add("selected");
          }});
          options.appendChild(button);
        }});

        const note = document.createElement("p");
        note.className = "feedback";
        feedback[index] = note;
        card.appendChild(options);
        card.appendChild(note);
        wrapper.appendChild(card);
      }});

      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const score = document.createElement("div");
      score.className = "score";
      const submit = createButton("提交答案", "submit-btn");
      submit.addEventListener("click", () => {{
        let correctCount = 0;
        questions.forEach((question, index) => {{
          const answer = question.answer;
          const selected = state[index];
          const card = wrapper.children[index];
          const buttons = card.querySelectorAll(".option-btn");
          buttons.forEach((button) => {{
            button.classList.remove("correct", "wrong");
            if (button.textContent === answer) {{
              button.classList.add("correct");
            }}
            if (selected && button.textContent === selected && selected !== answer) {{
              button.classList.add("wrong");
            }}
          }});
          if (selected === answer) {{
            correctCount += 1;
          }}
          feedback[index].textContent = question.explanation || `正确答案：${{answer}}`;
        }});
        score.textContent = `得分：${{correctCount}} / ${{questions.length}}`;
      }});
      toolbar.appendChild(score);
      toolbar.appendChild(submit);
      wrapper.appendChild(toolbar);
      return wrapper;
    }}

    function renderTrueFalse() {{
      const wrapper = document.createElement("div");
      wrapper.className = "stack";
      const statements = Array.isArray(game.data?.statements) ? game.data.statements : [];
      const state = new Array(statements.length).fill(null);
      const feedback = [];

      statements.forEach((item, index) => {{
        const card = document.createElement("section");
        card.className = "card";
        const title = document.createElement("h2");
        title.className = "question-title";
        title.textContent = `${{index + 1}}. ${{item.statement || "判断对错"}}`;
        card.appendChild(title);

        const row = document.createElement("div");
        row.className = "judge-row";
        [["正确", true], ["错误", false]].forEach(([label, value]) => {{
          const button = createButton(label, "judge-btn");
          button.addEventListener("click", () => {{
            state[index] = value;
            [...row.children].forEach((child) => child.classList.remove("selected"));
            button.classList.add("selected");
          }});
          row.appendChild(button);
        }});

        const note = document.createElement("p");
        note.className = "feedback";
        feedback[index] = note;
        card.appendChild(row);
        card.appendChild(note);
        wrapper.appendChild(card);
      }});

      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const score = document.createElement("div");
      score.className = "score";
      const submit = createButton("核对答案", "submit-btn");
      submit.addEventListener("click", () => {{
        let correctCount = 0;
        statements.forEach((item, index) => {{
          const selected = state[index];
          const answer = Boolean(item.answer);
          const buttons = wrapper.children[index].querySelectorAll(".judge-btn");
          buttons.forEach((button) => {{
            button.classList.remove("correct", "wrong");
            const buttonValue = button.textContent === "正确";
            if (buttonValue === answer) {{
              button.classList.add("correct");
            }}
            if (selected !== null && buttonValue === selected && selected !== answer) {{
              button.classList.add("wrong");
            }}
          }});
          if (selected === answer) {{
            correctCount += 1;
          }}
          feedback[index].textContent = item.explanation || (answer ? "这条说法正确。" : "这条说法错误。");
        }});
        score.textContent = `得分：${{correctCount}} / ${{statements.length}}`;
      }});
      toolbar.appendChild(score);
      toolbar.appendChild(submit);
      wrapper.appendChild(toolbar);
      return wrapper;
    }}

    function renderFlipCards() {{
      const wrapper = document.createElement("div");
      wrapper.className = "flip-grid";
      const cards = Array.isArray(game.data?.cards) ? game.data.cards : [];
      cards.forEach((item) => {{
        const card = document.createElement("button");
        card.type = "button";
        card.className = "flip-card";
        const inner = document.createElement("div");
        inner.className = "flip-inner";
        const front = document.createElement("div");
        front.className = "flip-face flip-front";
        front.textContent = item.front || "提示";
        const back = document.createElement("div");
        back.className = "flip-face flip-back";
        back.textContent = item.back || "";
        inner.appendChild(front);
        inner.appendChild(back);
        card.appendChild(inner);
        card.addEventListener("click", () => card.classList.toggle("is-open"));
        wrapper.appendChild(card);
      }});
      return wrapper;
    }}

    if (game.template === "true_false") {{
      app.appendChild(renderTrueFalse());
    }} else if (game.template === "flip_cards") {{
      app.appendChild(renderFlipCards());
    }} else {{
      app.appendChild(renderSingleChoice());
    }}
  </script>
</body>
</html>"""


def _select_templates(game_count: int, requested_templates: list[str]) -> list[str]:
    """Pick the ordered template list for one generation request."""
    allowed = [item for item in requested_templates if item in GAME_TEMPLATES]
    if not allowed:
        allowed = list(GAME_TEMPLATES)
    return [allowed[index % len(allowed)] for index in range(game_count)]


def _section_label(section: dict[str, Any], index: int) -> str:
    """Return the most readable label for a lesson section."""
    return str(
        section.get("type")
        or section.get("title")
        or section.get("name")
        or section.get("heading")
        or f"章节 {index + 1}"
    ).strip()


def _section_points(section: dict[str, Any]) -> list[str]:
    """Extract concise knowledge points from one section."""
    raw_content = str(section.get("content") or "").strip()
    if not raw_content:
        return []
    normalized = re.sub(r"[•·●]\s*", "", raw_content)
    parts = re.split(r"[\n。！？；]+", normalized)
    points = [part.strip(" -:：，,。") for part in parts if part.strip(" -:：，,。")]
    return points[:6]


def _first_point(section: dict[str, Any]) -> str:
    """Return the first usable point from one section."""
    points = _section_points(section)
    return points[0] if points else ""


def _get_llm_client() -> OpenAI:
    """Build an OpenAI-compatible client for mini-game generation."""
    return OpenAI(
        api_key=require_llm_api_key("小游戏生成"),
        base_url=settings.DEEPSEEK_BASE_URL,
    )


__all__ = ["GameGenerationError", "generate_games_for_plan"]
