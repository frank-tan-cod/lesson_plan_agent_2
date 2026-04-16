import type {
  DetailLevelPreference,
  InteractionLevelPreference,
  LanguageStylePreference,
  TeachingPacePreference,
  TempPreferencesPayload,
  VisualFocusPreference
} from "@/lib/types";

type PreferenceOption<T extends string> = {
  value: T;
  label: string;
  helper: string;
  prompt: string;
};

export const TEACHING_PACE_OPTIONS: PreferenceOption<TeachingPacePreference>[] = [
  {
    value: "compact",
    label: "紧凑推进",
    helper: "更聚焦核心内容，减少延展。",
    prompt: "教学推进尽量紧凑，优先保留核心信息，避免无关展开。"
  },
  {
    value: "balanced",
    label: "节奏均衡",
    helper: "兼顾推进速度和学生理解。",
    prompt: "教学节奏保持均衡，兼顾推进速度与学生理解。"
  },
  {
    value: "thorough",
    label: "放慢讲透",
    helper: "关键内容多做解释和过渡。",
    prompt: "关键内容放慢讲透，适当增加过渡、解释和停顿。"
  }
];

export const INTERACTION_LEVEL_OPTIONS: PreferenceOption<InteractionLevelPreference>[] = [
  {
    value: "lecture",
    label: "讲授为主",
    helper: "互动只保留必要节点。",
    prompt: "整体以教师讲授为主，互动只保留必要节点。"
  },
  {
    value: "balanced",
    label: "适度互动",
    helper: "关键位置加入提问或交流。",
    prompt: "保持适度互动，在关键知识点加入提问或简短交流。"
  },
  {
    value: "interactive",
    label: "高频互动",
    helper: "尽量多安排讨论、表达与回应。",
    prompt: "尽量提高互动频率，多安排提问、讨论或学生表达。"
  }
];

export const DETAIL_LEVEL_OPTIONS: PreferenceOption<DetailLevelPreference>[] = [
  {
    value: "summary",
    label: "结论优先",
    helper: "先给结论，减少过程展开。",
    prompt: "内容呈现偏概览式，结论优先，避免展开过细。"
  },
  {
    value: "balanced",
    label: "详略平衡",
    helper: "兼顾结果和必要步骤。",
    prompt: "内容详略保持平衡，既给结论也保留必要过程。"
  },
  {
    value: "step_by_step",
    label: "步骤展开",
    helper: "重要内容要讲清过程。",
    prompt: "重要内容按步骤展开，不要只给结论，要说明推导或操作过程。"
  }
];

export const LANGUAGE_STYLE_OPTIONS: PreferenceOption<LanguageStylePreference>[] = [
  {
    value: "rigorous",
    label: "专业严谨",
    helper: "表达更规范、准确。",
    prompt: "整体表达保持专业、准确、相对严谨。"
  },
  {
    value: "conversational",
    label: "自然口语",
    helper: "更像真实课堂上的讲述方式。",
    prompt: "整体表达更自然口语化，贴近真实课堂交流。"
  },
  {
    value: "encouraging",
    label: "鼓励引导",
    helper: "语气更有陪伴感和激励感。",
    prompt: "整体表达带有鼓励和引导感，帮助学生建立参与信心。"
  }
];

export const VISUAL_FOCUS_OPTIONS: PreferenceOption<VisualFocusPreference>[] = [
  {
    value: "auto",
    label: "按需判断",
    helper: "由系统按内容自动判断图文比例。",
    prompt: "视觉呈现按内容需要自动判断，不强制偏向文字或图片。"
  },
  {
    value: "text_first",
    label: "文字优先",
    helper: "能用文字讲清时尽量不用图片。",
    prompt: "默认文字信息优先，只有确实必要时再加入图片或截图。"
  },
  {
    value: "visual_first",
    label: "图例优先",
    helper: "适合时优先给图片、案例图或截图位。",
    prompt: "如内容适合展示，优先考虑图片、案例图、示意图或截图占位。"
  }
];

const KNOWN_KEYS = new Set([
  "teaching_pace",
  "interaction_level",
  "detail_level",
  "language_style",
  "visual_focus",
  "other_notes"
]);

function normalizeText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function coerceChoice<T extends string>(value: unknown, options: PreferenceOption<T>[]) {
  return options.some((item) => item.value === value) ? (value as T) : undefined;
}

function renderLegacyField(key: string, value: unknown) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim() ? `历史字段 ${key}: ${value.trim()}` : "";
  }
  return `历史字段 ${key}: ${JSON.stringify(value)}`;
}

export function normalizeTempPreferencesPayload(
  payload: TempPreferencesPayload | Record<string, unknown> | null | undefined
): TempPreferencesPayload {
  const source = (payload ?? {}) as Record<string, unknown>;
  const legacyNotes = Object.entries(source)
    .filter(([key]) => !KNOWN_KEYS.has(key))
    .map(([key, value]) => renderLegacyField(key, value))
    .filter(Boolean);

  const otherNotes = [normalizeText(source.other_notes), ...legacyNotes].filter(Boolean).join("\n");

  return compactTempPreferencesPayload({
    teaching_pace: coerceChoice(source.teaching_pace, TEACHING_PACE_OPTIONS),
    interaction_level: coerceChoice(source.interaction_level, INTERACTION_LEVEL_OPTIONS),
    detail_level: coerceChoice(source.detail_level, DETAIL_LEVEL_OPTIONS),
    language_style: coerceChoice(source.language_style, LANGUAGE_STYLE_OPTIONS),
    visual_focus: coerceChoice(source.visual_focus, VISUAL_FOCUS_OPTIONS),
    other_notes: otherNotes || undefined
  });
}

export function compactTempPreferencesPayload(payload: TempPreferencesPayload): TempPreferencesPayload {
  const next: TempPreferencesPayload = {};

  if (payload.teaching_pace) {
    next.teaching_pace = payload.teaching_pace;
  }
  if (payload.interaction_level) {
    next.interaction_level = payload.interaction_level;
  }
  if (payload.detail_level) {
    next.detail_level = payload.detail_level;
  }
  if (payload.language_style) {
    next.language_style = payload.language_style;
  }
  if (payload.visual_focus) {
    next.visual_focus = payload.visual_focus;
  }
  if (payload.other_notes?.trim()) {
    next.other_notes = payload.other_notes.trim();
  }

  return next;
}

function labelFor<T extends string>(options: PreferenceOption<T>[], value: T | undefined, fallback: string) {
  return options.find((item) => item.value === value)?.label ?? fallback;
}

function promptFor<T extends string>(options: PreferenceOption<T>[], value: T | undefined) {
  return options.find((item) => item.value === value)?.prompt ?? "";
}

function coerceOtherNotes(line: string) {
  return line.replace(/^-+\s*/, "").replace(/^其他要求[:：]\s*/, "").trim();
}

export function summarizeTempPreferences(payload: TempPreferencesPayload) {
  const normalized = compactTempPreferencesPayload(payload);
  const lines: string[] = [];

  if (normalized.teaching_pace) {
    lines.push(`教学节奏：${labelFor(TEACHING_PACE_OPTIONS, normalized.teaching_pace, normalized.teaching_pace)}`);
  }
  if (normalized.interaction_level) {
    lines.push(
      `互动强度：${labelFor(INTERACTION_LEVEL_OPTIONS, normalized.interaction_level, normalized.interaction_level)}`
    );
  }
  if (normalized.detail_level) {
    lines.push(`内容展开：${labelFor(DETAIL_LEVEL_OPTIONS, normalized.detail_level, normalized.detail_level)}`);
  }
  if (normalized.language_style) {
    lines.push(`表达风格：${labelFor(LANGUAGE_STYLE_OPTIONS, normalized.language_style, normalized.language_style)}`);
  }
  if (normalized.visual_focus) {
    lines.push(`视觉呈现：${labelFor(VISUAL_FOCUS_OPTIONS, normalized.visual_focus, normalized.visual_focus)}`);
  }
  if (normalized.other_notes) {
    lines.push(`其他要求：${normalized.other_notes}`);
  }

  return lines;
}

export function buildPreferencePromptInjection(payload: TempPreferencesPayload) {
  const normalized = compactTempPreferencesPayload(payload);
  const lines: string[] = [];

  const teachingPacePrompt = promptFor(TEACHING_PACE_OPTIONS, normalized.teaching_pace);
  if (teachingPacePrompt) {
    lines.push(teachingPacePrompt);
  }

  const interactionLevelPrompt = promptFor(INTERACTION_LEVEL_OPTIONS, normalized.interaction_level);
  if (interactionLevelPrompt) {
    lines.push(interactionLevelPrompt);
  }

  const detailLevelPrompt = promptFor(DETAIL_LEVEL_OPTIONS, normalized.detail_level);
  if (detailLevelPrompt) {
    lines.push(detailLevelPrompt);
  }

  const languageStylePrompt = promptFor(LANGUAGE_STYLE_OPTIONS, normalized.language_style);
  if (languageStylePrompt) {
    lines.push(languageStylePrompt);
  }

  const visualFocusPrompt = promptFor(VISUAL_FOCUS_OPTIONS, normalized.visual_focus);
  if (visualFocusPrompt) {
    lines.push(visualFocusPrompt);
  }

  if (normalized.other_notes?.trim()) {
    lines.push(`其他要求：${normalized.other_notes.trim()}`);
  }

  return lines.join("\n");
}

export function parsePreferencePromptInjection(prompt: string): TempPreferencesPayload {
  const lines = prompt
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const remaining: string[] = [];
  const next: TempPreferencesPayload = {};

  lines.forEach((line) => {
    const teachingPace = TEACHING_PACE_OPTIONS.find((item) => item.prompt === line);
    if (teachingPace) {
      next.teaching_pace = teachingPace.value;
      return;
    }

    const interactionLevel = INTERACTION_LEVEL_OPTIONS.find((item) => item.prompt === line);
    if (interactionLevel) {
      next.interaction_level = interactionLevel.value;
      return;
    }

    const detailLevel = DETAIL_LEVEL_OPTIONS.find((item) => item.prompt === line);
    if (detailLevel) {
      next.detail_level = detailLevel.value;
      return;
    }

    const languageStyle = LANGUAGE_STYLE_OPTIONS.find((item) => item.prompt === line);
    if (languageStyle) {
      next.language_style = languageStyle.value;
      return;
    }

    const visualFocus = VISUAL_FOCUS_OPTIONS.find((item) => item.prompt === line);
    if (visualFocus) {
      next.visual_focus = visualFocus.value;
      return;
    }

    if (line.startsWith("其他要求：") || line.startsWith("其他要求:")) {
      remaining.push(coerceOtherNotes(line));
      return;
    }

    remaining.push(line);
  });

  if (remaining.length) {
    next.other_notes = remaining.join("\n");
  }

  return compactTempPreferencesPayload(next);
}
