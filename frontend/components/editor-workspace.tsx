"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ImageAssetPicker } from "@/components/image-asset-picker";
import { useToast } from "@/components/toast-provider";
import { SafeImage } from "@/components/ui/safe-image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  createConversation,
  createSavepoint,
  fetchKnowledgeFiles,
  exportLesson,
  exportPresentation,
  generateLessonGames,
  generatePresentationFromPlan,
  getPlan,
  getTempPreferences,
  listConversations,
  listSavepoints,
  replaceTempPreferences,
  restoreSavepoint,
  triggerBrowserDownload,
  deleteSavepoint,
  updatePresentation,
  uploadKnowledgeImage
} from "@/lib/api";
import {
  buildKnowledgeImageUrl as buildUploadedImageUrl,
  resolveImageAssetUrl as resolvePresentationImageUrl,
  resolveUploadAssetUrl
} from "@/lib/image-assets";
import type { ImageCropPreset } from "@/lib/image-processing";
import {
  DEFAULT_PRESENTATION_STYLE,
  DENSITY_LABELS,
  THEME_LABELS,
  extractPresentationStyle,
  getThemePalette,
  normalizePresentationStyle,
  paginateSlidesForPreview,
  resolveFontSizes,
  slideUsesImagePanel
} from "@/lib/presentation-style";
import { streamSse } from "@/lib/sse";
import {
  compactTempPreferencesPayload,
  DETAIL_LEVEL_OPTIONS,
  INTERACTION_LEVEL_OPTIONS,
  LANGUAGE_STYLE_OPTIONS,
  normalizeTempPreferencesPayload,
  summarizeTempPreferences,
  TEACHING_PACE_OPTIONS,
  VISUAL_FOCUS_OPTIONS
} from "@/lib/temp-preferences";
import type {
  ChatMessage,
  Conversation,
  DocType,
  EditorConfirmationEvent,
  EditorConversationEvent,
  EditorDeltaEvent,
  EditorDoneEvent,
  EditorErrorEvent,
  EditorFollowUpEvent,
  EditorPendingTask,
  EditorStatusEvent,
  EditorToolEvent,
  EditorToolResultEvent,
  KnowledgeFile,
  MiniGame,
  Plan,
  PresentationStylePayload,
  Savepoint,
  TempPreferencesPayload
} from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

function createMessage(role: ChatMessage["role"], content: string, kind?: ChatMessage["kind"]): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    role,
    content,
    kind,
    createdAt: Date.now()
  };
}

function normalizeText(value: unknown) {
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeMessageKind(value: unknown): ChatMessage["kind"] {
  if (
    value === "tool" ||
    value === "tool_result" ||
    value === "follow_up" ||
    value === "confirmation" ||
    value === "error" ||
    value === "status"
  ) {
    return value;
  }
  return "text";
}

function messageRoleFromKind(role: unknown, kind: ChatMessage["kind"]): ChatMessage["role"] {
  if (role === "user") {
    return "user";
  }
  if (
    kind === "tool" ||
    kind === "tool_result" ||
    kind === "follow_up" ||
    kind === "confirmation" ||
    kind === "error" ||
    kind === "status"
  ) {
    return "system";
  }
  return "assistant";
}

function formatToolCallMessage(toolName: string, argumentsPayload: Record<string, unknown>) {
  return `工具调用：${toolName}\n${JSON.stringify(argumentsPayload, null, 2)}`;
}

function formatToolResultMessage(payload: EditorToolResultEvent) {
  const fallback =
    (isRecord(payload.result) && typeof payload.result.message === "string" && payload.result.message.trim()) ||
    JSON.stringify(payload.result, null, 2);
  const summary = normalizeText(payload.summary).trim() || fallback;
  return [`工具结果：${payload.tool_name}`, summary].filter(Boolean).join("\n");
}

function buildEditorMessages(conversation: Conversation | null, docType: DocType): ChatMessage[] {
  const turns = conversation?.metadata?.["recent_turns"];
  if (!Array.isArray(turns) || !turns.length) {
    return [
      createMessage("system", `已进入${docType === "lesson" ? "教案" : "演示文稿"}编辑器。你可以直接描述修改需求。`, "text")
    ];
  }

  const messages: ChatMessage[] = [];
  turns.forEach((turn, index) => {
    if (!isRecord(turn)) {
      return;
    }
    const content = normalizeText(turn.content).trim();
    if (!content) {
      return;
    }
    const kind = normalizeMessageKind(turn.kind);
    messages.push({
      id: `history-${conversation?.id || "conversation"}-${index}`,
      role: messageRoleFromKind(turn.role, kind),
      content,
      kind,
      createdAt: Date.now() - (turns.length - index) * 1000
    });
  });

  return messages.length
    ? messages
    : [
        createMessage(
          "system",
          `已进入${docType === "lesson" ? "教案" : "演示文稿"}编辑器。你可以直接描述修改需求。`,
          "text"
        )
      ];
}

function buildPendingFollowUp(conversation: Conversation | null): EditorFollowUpEvent | null {
  const payload = conversation?.metadata?.["pending_follow_up"];
  if (!isRecord(payload) || typeof payload.question !== "string" || !payload.question.trim()) {
    return null;
  }

  return {
    conversation_id: conversation?.id || "",
    type: "follow_up",
    question: payload.question.trim(),
    options: Array.isArray(payload.options) ? payload.options.filter((item): item is string => typeof item === "string") : null,
    previous_user_message: normalizeText(payload.previous_user_message) || null,
    completed_steps: Array.isArray(payload.completed_steps)
      ? payload.completed_steps.filter((item): item is string => typeof item === "string")
      : null,
    remaining_tasks: Array.isArray(payload.remaining_tasks)
      ? payload.remaining_tasks.filter((item): item is EditorPendingTask => isRecord(item) && typeof item.type === "string")
      : null
  };
}

function buildPendingConfirmation(conversation: Conversation | null): EditorConfirmationEvent | null {
  const payload = conversation?.metadata?.["pending_confirmation"];
  if (!isRecord(payload)) {
    return null;
  }

  if (
    typeof payload.operation_description !== "string" ||
    typeof payload.proposed_changes !== "string" ||
    typeof payload.tool_to_confirm !== "string"
  ) {
    return null;
  }

  return {
    conversation_id: conversation?.id || "",
    type: "confirmation_required",
    operation_description: payload.operation_description,
    proposed_changes: payload.proposed_changes,
    tool_to_confirm: payload.tool_to_confirm,
    tool_args: isRecord(payload.tool_args) ? payload.tool_args : {}
  };
}

function describePendingTask(task: EditorPendingTask, index: number) {
  const parts = [task.type, task.tool_name, task.action, task.target].filter(
    (value): value is string => typeof value === "string" && value.trim().length > 0
  );
  const summary = parts.length ? parts.join(" / ") : `任务 ${index + 1}`;
  const detail =
    normalizeText(task.proposed_content).trim() ||
    normalizeText(task.response).trim() ||
    normalizeText(task.parameters?.question).trim();
  return {
    summary,
    detail
  };
}

function renderParagraphs(value: unknown) {
  const text = normalizeText(value);
  const blocks = text.split(/\n{2,}/).filter(Boolean);

  if (!blocks.length) {
    return <p className="text-sm text-steel">暂无内容</p>;
  }

  return (
    <div className="space-y-3 text-sm leading-7 text-ink/85">
      {blocks.map((paragraph, index) => (
        <p key={`${paragraph.slice(0, 20)}-${index}`} className="whitespace-pre-wrap">
          {paragraph}
        </p>
      ))}
    </div>
  );
}

function lessonSections(plan: Plan | null) {
  const sections = plan?.content?.["sections"];
  return Array.isArray(sections) ? (sections as Array<Record<string, unknown>>) : [];
}

function lessonGames(plan: Plan | null) {
  const games = plan?.content?.["games"];
  return Array.isArray(games) ? (games as MiniGame[]) : [];
}

function presentationSlides(plan: Plan | null) {
  const slides = plan?.content?.["slides"];
  return Array.isArray(slides) ? (slides as Array<Record<string, unknown>>) : [];
}

function getPreviewOriginalIndex(slide: Record<string, unknown>, fallbackIndex: number) {
  return typeof slide.preview_original_index === "number" && slide.preview_original_index >= 0
    ? slide.preview_original_index
    : fallbackIndex;
}

function getPresentationSubtitle(slide: Record<string, unknown>) {
  const subtitle = normalizeText(slide.subtitle).trim();
  if (subtitle) {
    return subtitle;
  }
  const template = normalizeText(slide.template || slide.layout).toLowerCase();
  if (template === "title_subtitle") {
    return normalizeText(slide.body).trim();
  }
  return "";
}

function getPresentationBody(slide: Record<string, unknown>) {
  const body = normalizeText(slide.body).trim();
  if (body) {
    return body;
  }
  if (Array.isArray(slide.bullet_points)) {
    return (slide.bullet_points as string[]).filter((item) => typeof item === "string" && item.trim()).join("\n");
  }
  return "";
}

function getLatestGeneratedPresentationId(plan: Plan | null) {
  return normalizeText(plan?.metadata?.["latest_generated_presentation_id"]).trim();
}

function getLatestGeneratedPresentationTitle(plan: Plan | null) {
  return normalizeText(plan?.metadata?.["latest_generated_presentation_title"]).trim();
}

function getGeneratedPresentationCount(plan: Plan | null) {
  const ids = plan?.metadata?.["generated_presentation_ids"];
  if (!Array.isArray(ids)) {
    return getLatestGeneratedPresentationId(plan) ? 1 : 0;
  }
  return ids.filter((item): item is string => typeof item === "string" && item.trim().length > 0).length;
}

function parseTagInput(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function PresentationImagePanel({
  imageUrl,
  imageDescription,
  onManageImage
}: {
  imageUrl: string;
  imageDescription: string;
  onManageImage?: () => void;
}) {
  const resolvedSrc = resolvePresentationImageUrl(imageUrl);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [resolvedSrc]);

  if (resolvedSrc && !failed) {
    return (
      <div className="relative h-full w-full overflow-hidden rounded-[16px]">
        <SafeImage
          src={resolvedSrc}
          alt={imageDescription || "幻灯片配图"}
          fill
          sizes="(max-width: 1024px) 100vw, 30vw"
          className="object-cover"
          onError={() => setFailed(true)}
        />
        {onManageImage ? (
          <button
            type="button"
            onClick={onManageImage}
            className="absolute right-3 top-3 rounded-full bg-white/88 px-3 py-1 text-xs font-medium text-ink shadow-sm transition hover:bg-white"
          >
            更换图片
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col items-center justify-center rounded-[16px] border border-dashed border-[#9db1c9] bg-white/55 px-4 text-center">
      <p className="whitespace-pre-wrap text-sm leading-6 text-[#5a6776]">{imageDescription || "图片占位"}</p>
      {onManageImage ? (
        <button
          type="button"
          onClick={onManageImage}
          className="mt-4 rounded-full bg-[#183149] px-4 py-2 text-xs font-medium text-white transition hover:bg-[#10263a]"
        >
          上传或选择图片
        </button>
      ) : null}
    </div>
  );
}

function MiniGamePreview({ game, index }: { game: MiniGame; index: number }) {
  const htmlUrl = resolveUploadAssetUrl(game.html_url);
  const templateLabel =
    game.template === "single_choice" ? "选择题" : game.template === "true_false" ? "判断题" : "翻卡片";

  return (
    <div className="rounded-[28px] bg-white p-5 ring-1 ring-slate-200 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-steel">Mini Game {index + 1}</p>
          <h3 className="mt-2 font-serif text-2xl text-ink">{game.title || `课堂小游戏 ${index + 1}`}</h3>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge className="bg-lagoon/10 text-lagoon">{templateLabel}</Badge>
          {game.source_section ? <Badge className="bg-white text-steel">来源：{game.source_section}</Badge> : null}
        </div>
      </div>

      {game.description ? <p className="mt-3 text-sm leading-6 text-steel">{game.description}</p> : null}
      {game.learning_goal ? (
        <div className="mt-4 rounded-2xl bg-sand/45 px-4 py-3 text-sm leading-6 text-ink/80">
          巩固目标：{game.learning_goal}
        </div>
      ) : null}

      {htmlUrl ? (
        <div className="mt-4 overflow-hidden rounded-[24px] ring-1 ring-slate-200">
          <iframe
            title={game.title || `课堂小游戏 ${index + 1}`}
            src={htmlUrl}
            className="h-[420px] w-full bg-white"
          />
        </div>
      ) : (
        <div className="mt-4 rounded-[24px] border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center text-sm text-steel">
          暂无可预览的互动页面
        </div>
      )}
    </div>
  );
}

function renderPresentationSlidePreview(
  slide: Record<string, unknown>,
  index: number,
  totalSlides: number,
  style: PresentationStylePayload,
  onManageImage?: (slide: Record<string, unknown>, previewIndex: number) => void
) {
  const title = String(slide.title || `第 ${index + 1} 页`);
  const template = normalizeText(slide.template || slide.layout).trim() || "title_body";
  const subtitle = getPresentationSubtitle(slide);
  const body = getPresentationBody(slide);
  const bodyBlocks = body.split(/\n{2,}/).filter(Boolean);
  const hasImagePanel = slideUsesImagePanel(slide);
  const imageDescription = normalizeText(slide.image_description).trim();
  const imageUrl = normalizeText(slide.image_url).trim();
  const sourceSection = normalizeText(slide.source_section).trim();
  const palette = getThemePalette(style.theme);
  const fontSizes = resolveFontSizes(style.density, hasImagePanel);
  const logoUrl = normalizeText(style.logo_url).trim();

  if (template === "title_subtitle") {
    return (
      <div
        key={`${title}-${index}`}
        className="rounded-[28px] p-3 shadow-sm"
        style={{ backgroundColor: palette.background, boxShadow: `0 0 0 1px ${palette.border}` }}
      >
        <div className="aspect-[4/3] overflow-hidden rounded-[24px]" style={{ backgroundColor: palette.coverBackground }}>
          <div className="flex h-full">
            <div className="w-[12%]" style={{ backgroundColor: palette.header }} />
            <div className="flex flex-1 flex-col items-center justify-center px-10 text-center">
              <h3
                className="font-serif text-[clamp(1.7rem,2.6vw,2.5rem)]"
                style={{ color: palette.titleOnCover, fontSize: `${fontSizes.coverTitle}px` }}
              >
                {title}
              </h3>
              {subtitle ? (
                <p
                  className="mt-5 max-w-[80%] whitespace-pre-wrap leading-7"
                  style={{ color: palette.subtitle, fontSize: `${fontSizes.subtitle}px` }}
                >
                  {subtitle}
                </p>
              ) : null}
              {style.school_name ? (
                <p className="mt-6 text-sm tracking-[0.12em]" style={{ color: palette.subtitle }}>
                  {style.school_name}
                </p>
              ) : null}
              <div className="mt-8 h-[3px] w-24 rounded-full" style={{ backgroundColor: palette.accent }} />
            </div>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 px-1">
          <Badge className="bg-white text-steel">{template}</Badge>
          {sourceSection ? <Badge className="bg-white/70 text-steel">来源：{sourceSection}</Badge> : null}
        </div>
      </div>
    );
  }

  return (
    <div
      key={`${title}-${index}`}
      className="rounded-[28px] p-3 shadow-sm"
      style={{ backgroundColor: palette.background, boxShadow: `0 0 0 1px ${palette.border}` }}
    >
      <div className="aspect-[4/3] overflow-hidden rounded-[24px]" style={{ backgroundColor: palette.background }}>
        <div className="flex h-[18%] items-center justify-between px-6 py-4 text-white" style={{ backgroundColor: palette.header }}>
          <h3
            className="line-clamp-2 font-serif text-[clamp(1.15rem,2vw,1.7rem)]"
            style={{ color: palette.titleOnHeader, fontSize: `${fontSizes.title}px` }}
          >
            {title}
          </h3>
          <div className="ml-4 flex shrink-0 items-center gap-3">
            {style.school_name ? (
              <span className="hidden text-right tracking-[0.16em] text-white/85 md:block" style={{ fontSize: `${fontSizes.branding}px` }}>
                {style.school_name}
              </span>
            ) : null}
            {logoUrl ? (
              <SafeImage
                src={resolvePresentationImageUrl(logoUrl)}
                alt="学校 Logo"
                width={32}
                height={32}
                className="h-8 w-8 rounded-xl bg-white/80 object-contain p-1"
              />
            ) : null}
            <span className="rounded-full px-3 py-1 text-[11px] tracking-[0.18em]" style={{ backgroundColor: "rgba(255,255,255,0.14)" }}>
              {index + 1}/{totalSlides}
            </span>
          </div>
        </div>

        <div className="h-[2.4%]" style={{ backgroundColor: palette.accent, opacity: 0.95 }} />

        <div
          className={cn("grid h-[79.6%] gap-4 p-5", hasImagePanel ? "grid-cols-[1.35fr_1fr]" : "grid-cols-1")}
        >
          <div
            className="overflow-hidden rounded-[20px] px-5 py-4"
            style={{ backgroundColor: `${palette.surface}dd`, boxShadow: `0 0 0 1px ${palette.border}` }}
          >
            {bodyBlocks.length ? (
              <div className="space-y-3 leading-7" style={{ color: palette.body, fontSize: `${fontSizes.body}px` }}>
                {bodyBlocks.map((paragraph, paragraphIndex) => (
                  <p key={`${paragraph.slice(0, 20)}-${paragraphIndex}`} className="whitespace-pre-wrap">
                    {paragraph}
                  </p>
                ))}
              </div>
            ) : (
              <p className="text-sm" style={{ color: palette.subtitle }}>
                （本页暂无正文）
              </p>
            )}
          </div>

          {hasImagePanel ? (
            <div
              className="flex min-h-0 flex-col overflow-hidden rounded-[20px]"
              style={{ backgroundColor: `${palette.surface}e6`, boxShadow: `0 0 0 1px ${palette.border}` }}
            >
              <div className="flex-1 p-4" style={{ background: `linear-gradient(160deg, ${palette.surface} 0%, ${palette.background} 100%)` }}>
                <PresentationImagePanel
                  imageUrl={imageUrl}
                  imageDescription={imageDescription}
                  onManageImage={onManageImage ? () => onManageImage(slide, index) : undefined}
                />
              </div>
            </div>
          ) : null}
        </div>

        <div className="px-6 pb-4 text-right text-[11px] tracking-[0.14em]" style={{ color: palette.subtitle }}>
          {THEME_LABELS[style.theme]} · {DENSITY_LABELS[style.density]}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 px-1">
        <Badge className="bg-white text-steel">{template}</Badge>
        {sourceSection ? <Badge className="bg-white/70 text-steel">来源：{sourceSection}</Badge> : null}
        {hasImagePanel && onManageImage ? (
          <Button variant="secondary" onClick={() => onManageImage(slide, index)}>
            {imageUrl ? "更换配图" : "上传配图"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

interface EditorWorkspaceProps {
  initialDocType?: string;
}

type LogoPickerTarget = "generation" | "editor";

const SLIDE_IMAGE_CROP_PRESETS: ImageCropPreset[] = [
  {
    id: "slide-4x3",
    label: "标准配图 4:3",
    aspectRatio: 4 / 3,
    outputWidth: 1600,
    outputHeight: 1200,
    helper: "适合放进当前 PPT 图片占位区，导出时更稳定。"
  },
  {
    id: "background-16x9",
    label: "宽幅背景板 16:9",
    aspectRatio: 16 / 9,
    outputWidth: 1920,
    outputHeight: 1080,
    helper: "适合后续作为整页背景或宽幅横图使用。"
  }
];

const LOGO_IMAGE_CROP_PRESETS: ImageCropPreset[] = [
  {
    id: "logo-square",
    label: "Logo 方形 1:1",
    aspectRatio: 1,
    outputWidth: 900,
    outputHeight: 900,
    helper: "建议把校徽或机构标志放在裁剪框中央，预览和导出会自动等比缩放。"
  }
];

export function EditorWorkspace({ initialDocType }: EditorWorkspaceProps) {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { push } = useToast();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const planId = params.id;
  const [plan, setPlan] = useState<Plan | null>(null);
  const [docType, setDocType] = useState<DocType>((initialDocType as DocType) || "lesson");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [followUpInput, setFollowUpInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  const [initError, setInitError] = useState<string | null>(null);
  const [previewRatio, setPreviewRatio] = useState(52);
  const [tempPreferences, setTempPreferences] = useState<TempPreferencesPayload>({});
  const [pendingFollowUp, setPendingFollowUp] = useState<EditorFollowUpEvent | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<EditorConfirmationEvent | null>(null);
  const [savepointModalOpen, setSavepointModalOpen] = useState(false);
  const [saveToKnowledgeModalOpen, setSaveToKnowledgeModalOpen] = useState(false);
  const [preferencesModalOpen, setPreferencesModalOpen] = useState(false);
  const [generatePresentationModalOpen, setGeneratePresentationModalOpen] = useState(false);
  const [presentationStyleModalOpen, setPresentationStyleModalOpen] = useState(false);
  const [savepoints, setSavepoints] = useState<Savepoint[]>([]);
  const [savepointLabel, setSavepointLabel] = useState("");
  const [knowledgeSnapshotLabel, setKnowledgeSnapshotLabel] = useState("");
  const [knowledgeSnapshotTitle, setKnowledgeSnapshotTitle] = useState("");
  const [knowledgeSnapshotDescription, setKnowledgeSnapshotDescription] = useState("");
  const [knowledgeSnapshotTags, setKnowledgeSnapshotTags] = useState("");
  const [savingKnowledgeSnapshot, setSavingKnowledgeSnapshot] = useState(false);
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [loadingKnowledgeFiles, setLoadingKnowledgeFiles] = useState(false);
  const [selectedKnowledgeFileIds, setSelectedKnowledgeFileIds] = useState<string[]>([]);
  const [presentationCourseContext, setPresentationCourseContext] = useState("");
  const [generationStyle, setGenerationStyle] = useState<PresentationStylePayload>(DEFAULT_PRESENTATION_STYLE);
  const [editablePresentationStyle, setEditablePresentationStyle] = useState<PresentationStylePayload>(DEFAULT_PRESENTATION_STYLE);
  const [generatingPresentation, setGeneratingPresentation] = useState(false);
  const [generatingGames, setGeneratingGames] = useState(false);
  const [savingPresentationStyle, setSavingPresentationStyle] = useState(false);
  const [slideImageModalOpen, setSlideImageModalOpen] = useState(false);
  const [activeImageSlideIndex, setActiveImageSlideIndex] = useState<number | null>(null);
  const [activeImageSlideTitle, setActiveImageSlideTitle] = useState("");
  const [slideImageDescriptionDraft, setSlideImageDescriptionDraft] = useState("");
  const [selectedImageFileId, setSelectedImageFileId] = useState("");
  const [savingSlideImage, setSavingSlideImage] = useState(false);
  const [uploadingSlideImage, setUploadingSlideImage] = useState(false);
  const [logoImageModalOpen, setLogoImageModalOpen] = useState(false);
  const [activeLogoTarget, setActiveLogoTarget] = useState<LogoPickerTarget | null>(null);
  const [selectedLogoFileId, setSelectedLogoFileId] = useState("");
  const [logoDescriptionDraft, setLogoDescriptionDraft] = useState("");
  const [updatingLogoImage, setUpdatingLogoImage] = useState(false);
  const [uploadingLogoImage, setUploadingLogoImage] = useState(false);

  const initialize = useCallback(async () => {
    setBooting(true);
    setInitError(null);
    try {
      if (!planId || typeof planId !== "string") {
        throw new Error("缺少文档 ID，无法初始化编辑器。");
      }

      const planData = await getPlan(planId).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : "请稍后重试。";
        throw new Error(`加载文档失败：${message}`);
      });
      setPlan(planData);
      setDocType((planData.doc_type || initialDocType || "lesson") as DocType);

      const conversations = await listConversations(planId).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : "请稍后重试。";
        throw new Error(`加载会话失败：${message}`);
      });
      const activeConversation =
        conversations.find((item) => item.status === "active") ??
        [...conversations].sort(
          (left, right) => new Date(right.started_at).getTime() - new Date(left.started_at).getTime()
        )[0] ??
        null;

      let nextConversationId = activeConversation?.id ?? null;
      if (!nextConversationId) {
        const created = await createConversation(planId).catch((error: unknown) => {
          const message = error instanceof Error ? error.message : "请稍后重试。";
          throw new Error(`创建会话失败：${message}`);
        });
        nextConversationId = created.id;
      }
      const restoredConversation = activeConversation?.id === nextConversationId ? activeConversation : null;
      setConversationId(nextConversationId);
      setPendingFollowUp(buildPendingFollowUp(restoredConversation));
      setPendingConfirmation(buildPendingConfirmation(restoredConversation));
      setMessages(buildEditorMessages(restoredConversation, (planData.doc_type || initialDocType || "lesson") as DocType));
    } catch (error) {
      const message = error instanceof Error ? error.message : "请稍后重试。";
      setPlan(null);
      setConversationId(null);
      setMessages([]);
      setPendingFollowUp(null);
      setPendingConfirmation(null);
      setInitError(message);
      push({
        title: "编辑器初始化失败",
        description: message,
        tone: "error"
      });
    } finally {
      setBooting(false);
    }
  }, [initialDocType, planId, push]);

  async function loadTempPreferences(targetConversationId: string) {
    try {
      const payload = await getTempPreferences(targetConversationId);
      setTempPreferences(normalizeTempPreferencesPayload(payload));
    } catch {
      setTempPreferences({});
    }
  }

  async function loadSavepoints() {
    try {
      const response = await listSavepoints(planId);
      setSavepoints(response);
    } catch (error) {
      push({
        title: "回退点加载失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    }
  }

  async function loadKnowledgeFiles() {
    setLoadingKnowledgeFiles(true);
    try {
      const response = await fetchKnowledgeFiles();
      setKnowledgeFiles(response.items);
    } catch (error) {
      push({
        title: "参考资料加载失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setLoadingKnowledgeFiles(false);
    }
  }

  useEffect(() => {
    void initialize();
    return () => abortRef.current?.abort();
  }, [initialize]);

  useEffect(() => {
    if (conversationId) {
      void loadTempPreferences(conversationId);
    }
  }, [conversationId]);

  useEffect(() => {
    setFollowUpInput("");
  }, [pendingFollowUp?.conversation_id, pendingFollowUp?.question]);

  useEffect(() => {
    setSelectedKnowledgeFileIds([]);
    setGeneratePresentationModalOpen(false);
    setPresentationStyleModalOpen(false);
    setSaveToKnowledgeModalOpen(false);
    setGenerationStyle(DEFAULT_PRESENTATION_STYLE);
    setEditablePresentationStyle(DEFAULT_PRESENTATION_STYLE);
    setKnowledgeSnapshotLabel("");
    setKnowledgeSnapshotTitle("");
    setKnowledgeSnapshotDescription("");
    setKnowledgeSnapshotTags("");
    setSlideImageModalOpen(false);
    setActiveImageSlideIndex(null);
    setActiveImageSlideTitle("");
    setSlideImageDescriptionDraft("");
    setSelectedImageFileId("");
    setLogoImageModalOpen(false);
    setActiveLogoTarget(null);
    setSelectedLogoFileId("");
    setLogoDescriptionDraft("");
  }, [planId]);

  useEffect(() => {
    setEditablePresentationStyle(extractPresentationStyle(plan?.metadata));
  }, [plan?.id, plan?.metadata]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth"
    });
  }, [messages, pendingFollowUp, pendingConfirmation, streaming]);

  const previewStyle = useMemo(
    () => ({
      gridTemplateColumns: `${previewRatio}fr ${100 - previewRatio}fr`
    }),
    [previewRatio]
  );
  const tempPreferenceSummary = useMemo(() => summarizeTempPreferences(tempPreferences), [tempPreferences]);
  const latestGeneratedPresentationId = useMemo(() => getLatestGeneratedPresentationId(plan), [plan]);
  const latestGeneratedPresentationTitle = useMemo(() => getLatestGeneratedPresentationTitle(plan), [plan]);
  const generatedPresentationCount = useMemo(() => getGeneratedPresentationCount(plan), [plan]);
  const documentsHref = useMemo(() => `/documents?type=${docType}`, [docType]);
  const activePresentationStyle = useMemo(() => extractPresentationStyle(plan?.metadata), [plan?.metadata]);
  const miniGames = useMemo(() => (docType === "lesson" ? lessonGames(plan) : []), [docType, plan]);
  const previewSlides = useMemo(
    () => (docType === "presentation" ? paginateSlidesForPreview(presentationSlides(plan), activePresentationStyle) : []),
    [docType, plan, activePresentationStyle]
  );
  const imageKnowledgeFiles = useMemo(
    () => knowledgeFiles.filter((file) => normalizeText(file.file_type) === "image"),
    [knowledgeFiles]
  );
  const activeImageSlide = useMemo(() => {
    if (activeImageSlideIndex === null) {
      return null;
    }
    return presentationSlides(plan)[activeImageSlideIndex] || null;
  }, [activeImageSlideIndex, plan]);
  const selectedImageFile = useMemo(
    () => imageKnowledgeFiles.find((file) => file.id === selectedImageFileId) || null,
    [imageKnowledgeFiles, selectedImageFileId]
  );
  const activeLogoStyle = useMemo(() => {
    if (activeLogoTarget === "generation") {
      return generationStyle;
    }
    if (activeLogoTarget === "editor") {
      return editablePresentationStyle;
    }
    return null;
  }, [activeLogoTarget, editablePresentationStyle, generationStyle]);
  const selectedLogoFile = useMemo(
    () => imageKnowledgeFiles.find((file) => file.id === selectedLogoFileId) || null,
    [imageKnowledgeFiles, selectedLogoFileId]
  );
  const activeImagePreviewUrl = useMemo(() => {
    if (selectedImageFile) {
      return buildUploadedImageUrl(selectedImageFile);
    }
    return normalizeText(activeImageSlide?.image_url).trim();
  }, [activeImageSlide, selectedImageFile]);
  const activeLogoPreviewUrl = useMemo(() => {
    if (selectedLogoFile) {
      return buildUploadedImageUrl(selectedLogoFile);
    }
    return normalizeText(activeLogoStyle?.logo_url).trim();
  }, [activeLogoStyle, selectedLogoFile]);

  function appendMessage(message: ChatMessage) {
    setMessages((current) => [...current, message]);
  }

  function upsertAssistantMessage(messageId: string, nextChunk: string) {
    setMessages((current) => {
      const existing = current.find((item) => item.id === messageId);
      if (!existing) {
        return [...current, { id: messageId, role: "assistant", content: nextChunk, createdAt: Date.now() }];
      }
      return current.map((item) => (item.id === messageId ? { ...item, content: item.content + nextChunk } : item));
    });
  }

  async function sendMessage(messageOverride?: string) {
    const content = (messageOverride ?? input).trim();
    if (!content || !plan) {
      return;
    }

    setInput("");
    setStreaming(true);
    setStreamStatus("正在处理...");
    setPendingFollowUp(null);
    setPendingConfirmation(null);
    appendMessage(createMessage("user", content));

    const assistantId = `assistant-${Date.now()}`;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    let sawToolEvent = false;
    let receivedTerminalEvent = false;
    let producedVisibleOutput = false;

    try {
      await streamSse({
        path: docType === "presentation" ? "/api/presentation/chat" : "/api/editor/chat",
        body: {
          plan_id: plan.id,
          conversation_id: conversationId,
          message: content
        },
        signal: controller.signal,
        retries: 0,
        onEvent: ({ event, data }) => {
          switch (event) {
            case "conversation": {
              const payload = data as EditorConversationEvent;
              if (payload.conversation_id) {
                setConversationId(payload.conversation_id);
              }
              break;
            }
            case "tool": {
              const payload = data as EditorToolEvent;
              sawToolEvent = true;
              appendMessage(createMessage("system", formatToolCallMessage(payload.tool_name, payload.arguments), "tool"));
              break;
            }
            case "tool_result": {
              const payload = data as EditorToolResultEvent;
              sawToolEvent = true;
              producedVisibleOutput = true;
              appendMessage(createMessage("system", formatToolResultMessage(payload), "tool_result"));
              break;
            }
            case "status": {
              const payload = data as EditorStatusEvent;
              setStreamStatus(payload.content || "正在处理中...");
              break;
            }
            case "delta": {
              const payload = data as EditorDeltaEvent;
              if ((payload.content || "").trim()) {
                producedVisibleOutput = true;
              }
              upsertAssistantMessage(assistantId, payload.content || "");
              break;
            }
            case "follow_up": {
              const payload = data as EditorFollowUpEvent;
              receivedTerminalEvent = true;
              producedVisibleOutput = true;
              setStreamStatus(null);
              setPendingFollowUp(payload);
              appendMessage(createMessage("system", `需要补充信息：${payload.question}`, "follow_up"));
              break;
            }
            case "confirmation_required": {
              const payload = data as EditorConfirmationEvent;
              receivedTerminalEvent = true;
              producedVisibleOutput = true;
              setStreamStatus(null);
              setPendingConfirmation(payload);
              appendMessage(
                createMessage(
                  "system",
                  `待确认操作：${payload.operation_description}\n${payload.proposed_changes}`,
                  "confirmation"
                )
              );
              break;
            }
            case "done": {
              const payload = data as EditorDoneEvent;
              receivedTerminalEvent = true;
              setStreamStatus(null);
              setConversationId(payload.conversation_id || conversationId);
              if (payload.plan) {
                setPlan(payload.plan);
              }
              if (payload.reply) {
                if (payload.reply.trim()) {
                  producedVisibleOutput = true;
                }
                setMessages((current) => {
                  const existing = current.find((item) => item.id === assistantId);
                  if (existing && existing.content.trim()) {
                    return current;
                  }
                  return [...current, { id: assistantId, role: "assistant", content: payload.reply, createdAt: Date.now() }];
                });
              }
              break;
            }
            case "error": {
              const payload = data as EditorErrorEvent;
              receivedTerminalEvent = true;
              producedVisibleOutput = true;
              setStreamStatus(null);
              appendMessage(createMessage("system", payload.message || "编辑器出现异常。", "error"));
              break;
            }
            default:
              break;
          }
        }
      });

      if (!controller.signal.aborted && sawToolEvent && (!receivedTerminalEvent || !producedVisibleOutput)) {
        appendMessage(
          createMessage("system", "本轮工具调用结束了，但没有返回可见回复。请再试一次；如果问题持续，我可以继续排查。", "error")
        );
      }
    } catch (error) {
      appendMessage(
        createMessage("system", error instanceof Error ? error.message : "消息发送失败，请稍后重试。", "error")
      );
      push({
        title: "流式会话失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setStreaming(false);
      setStreamStatus(null);
    }
  }

  function updateTempPreference<K extends keyof TempPreferencesPayload>(key: K, value: TempPreferencesPayload[K]) {
    setTempPreferences((current) => ({
      ...current,
      [key]: value
    }));
  }

  async function saveTempPreferences() {
    if (!conversationId) {
      return;
    }

    try {
      const payload = compactTempPreferencesPayload(tempPreferences);
      await replaceTempPreferences(conversationId, payload);
      push({
        title: "临时偏好已保存",
        description: "本次会话会继续使用这份偏好。",
        tone: "success"
      });
      setPreferencesModalOpen(false);
    } catch (error) {
      push({
        title: "偏好保存失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    }
  }

  async function handleExport(kind: "docx" | "pdf" | "pptx") {
    if (!plan) {
      return;
    }

    try {
      const file =
        kind === "pptx" ? await exportPresentation(plan.id) : await exportLesson(plan.id, kind as "docx" | "pdf");
      triggerBrowserDownload(file.blob, file.filename);
      push({
        title: "导出成功",
        description: `${file.filename} 已开始下载。`,
        tone: "success"
      });
    } catch (error) {
      push({
        title: "导出失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    }
  }

  async function handleCreateSavepoint() {
    if (!plan || !savepointLabel.trim()) {
      return;
    }
    try {
      await createSavepoint({
        plan_id: plan.id,
        label: savepointLabel.trim(),
        snapshot: (plan.content as Record<string, unknown>) ?? {},
        conversation_id: conversationId
      });
      setSavepointLabel("");
      await loadSavepoints();
      push({
        title: "回退点已保存",
        description: "这次只保存编辑器内快照，可随时在这里恢复，但不会写入知识库。",
        tone: "success"
      });
    } catch (error) {
      push({
        title: "保存失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    }
  }

  function openSaveToKnowledgeModal() {
    if (!plan) {
      return;
    }
    setKnowledgeSnapshotLabel(knowledgeSnapshotLabel || `当前快照 ${formatDateTime(new Date().toISOString())}`);
    setKnowledgeSnapshotTitle(knowledgeSnapshotTitle || `${plan.title} - 当前快照`);
    setSaveToKnowledgeModalOpen(true);
  }

  async function handleSaveSnapshotToKnowledge() {
    if (!plan || !knowledgeSnapshotLabel.trim() || !knowledgeSnapshotTitle.trim()) {
      return;
    }

    setSavingKnowledgeSnapshot(true);
    try {
      await createSavepoint({
        plan_id: plan.id,
        label: knowledgeSnapshotLabel.trim(),
        snapshot: (plan.content as Record<string, unknown>) ?? {},
        conversation_id: conversationId,
        persist_to_knowledge: true,
        knowledge_title: knowledgeSnapshotTitle.trim(),
        knowledge_description: knowledgeSnapshotDescription.trim() || undefined,
        knowledge_tags: parseTagInput(knowledgeSnapshotTags)
      });
      setSaveToKnowledgeModalOpen(false);
      setKnowledgeSnapshotDescription("");
      setKnowledgeSnapshotTags("");
      await loadSavepoints();
      if (knowledgeFiles.length) {
        await loadKnowledgeFiles();
      }
      push({
        title: "当前快照已保存进知识库",
        description: "这份快照现在既能在知识库检索，也能作为回退点恢复。",
        tone: "success"
      });
    } catch (error) {
      push({
        title: "保存进知识库失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setSavingKnowledgeSnapshot(false);
    }
  }

  async function handleGeneratePresentation() {
    if (!plan) {
      return;
    }

    setGeneratingPresentation(true);
    try {
      const response = await generatePresentationFromPlan(
        plan.id,
        selectedKnowledgeFileIds,
        presentationCourseContext.trim(),
        generationStyle
      );
      push({
        title: "PPT 已生成",
        description: "正在跳转到新的演示文稿编辑器。",
        tone: "success"
      });
      setGeneratePresentationModalOpen(false);
      setSelectedKnowledgeFileIds([]);
      setPresentationCourseContext("");
      router.push(`/documents/${response.presentation_id}/editor?type=presentation`);
    } catch (error) {
      push({
        title: "生成 PPT 失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setGeneratingPresentation(false);
    }
  }

  async function handleGenerateGames() {
    if (!plan || docType !== "lesson") {
      return;
    }

    setGeneratingGames(true);
    try {
      const updated = await generateLessonGames(plan.id, {
        game_count: 3,
        replace_existing: true
      });
      setPlan(updated);
      push({
        title: "小游戏已生成",
        description: "已在教案预览中加入互动游戏卡片，也会参与后续 PPT 生成。",
        tone: "success"
      });
    } catch (error) {
      push({
        title: "小游戏生成失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setGeneratingGames(false);
    }
  }

  function toggleKnowledgeFileSelection(fileId: string, checked: boolean) {
    setSelectedKnowledgeFileIds((current) => {
      if (checked) {
        return current.includes(fileId) ? current : [...current, fileId];
      }
      return current.filter((item) => item !== fileId);
    });
  }

  function updateStyleField(
    target: "generation" | "editor",
    field: keyof PresentationStylePayload,
    value: string | null
  ) {
    const setter = target === "generation" ? setGenerationStyle : setEditablePresentationStyle;
    setter((current) =>
      normalizePresentationStyle({
        ...current,
        [field]: value
      })
    );
  }

  function upsertKnowledgeImage(uploaded: KnowledgeFile) {
    setKnowledgeFiles((current) => {
      const deduped = current.filter((item) => item.id !== uploaded.id);
      return [uploaded, ...deduped];
    });
  }

  function updateStyleLogo(target: LogoPickerTarget, fileId: string, uploadedFile?: KnowledgeFile) {
    if (!fileId) {
      const setter = target === "generation" ? setGenerationStyle : setEditablePresentationStyle;
      setter((current) =>
        normalizePresentationStyle({
          ...current,
          logo_file_id: null,
          logo_url: null
        })
      );
      return;
    }

    const file = uploadedFile || imageKnowledgeFiles.find((item) => item.id === fileId);
    const logoUrl = file ? buildUploadedImageUrl(file) : "";
    const setter = target === "generation" ? setGenerationStyle : setEditablePresentationStyle;
    setter((current) =>
      normalizePresentationStyle({
        ...current,
        logo_file_id: fileId,
        logo_url: logoUrl || current.logo_url || null
      })
    );
  }

  async function handleSavePresentationStyle() {
    if (!plan || docType !== "presentation") {
      return;
    }

    setSavingPresentationStyle(true);
    try {
      const updated = await updatePresentation(plan.id, {
        metadata: {
          ...(plan.metadata || {}),
          presentation_style: editablePresentationStyle
        }
      });
      setPlan(updated);
      setPresentationStyleModalOpen(false);
      push({
        title: "风格设置已保存",
        description: "预览和导出都会使用新的视觉规则。",
        tone: "success"
      });
    } catch (error) {
      push({
        title: "保存风格失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setSavingPresentationStyle(false);
    }
  }

  function resetSlideImageDialog() {
    setSlideImageModalOpen(false);
    setActiveImageSlideIndex(null);
    setActiveImageSlideTitle("");
    setSlideImageDescriptionDraft("");
    setSelectedImageFileId("");
  }

  function openSlideImageDialog(slide: Record<string, unknown>, previewIndex: number) {
    const originalIndex = getPreviewOriginalIndex(slide, previewIndex);
    const currentImageUrl = normalizeText(slide.image_url).trim();
    const matchedImage = imageKnowledgeFiles.find((file) => buildUploadedImageUrl(file) === currentImageUrl);

    setActiveImageSlideIndex(originalIndex);
    setActiveImageSlideTitle(normalizeText(slide.title).trim() || `第 ${originalIndex + 1} 页`);
    setSlideImageDescriptionDraft(
      normalizeText(slide.image_description).trim() || normalizeText(slide.title).trim() || "课堂配图"
    );
    setSelectedImageFileId(matchedImage?.id || "");
    setSlideImageModalOpen(true);
    if (!knowledgeFiles.length) {
      void loadKnowledgeFiles();
    }
  }

  async function saveSlideImageToPresentation(imageUrl: string | null) {
    if (!plan || docType !== "presentation" || activeImageSlideIndex === null) {
      return;
    }

    const currentSlides = presentationSlides(plan);
    if (!currentSlides[activeImageSlideIndex]) {
      return;
    }

    const nextSlides = currentSlides.map((slide, index) =>
      index === activeImageSlideIndex
        ? {
            ...slide,
            image_url: imageUrl,
            image_description: slideImageDescriptionDraft.trim() || null
          }
        : slide
    );

    setSavingSlideImage(true);
    try {
      const updated = await updatePresentation(plan.id, {
        content: {
          ...plan.content,
          slides: nextSlides
        }
      });
      setPlan(updated);
      push({
        title: imageUrl ? "图片已更新" : "已恢复图片占位",
        description: imageUrl ? "预览和导出都会使用这张图片。" : "当前页面已移除实图，保留图片占位说明。",
        tone: "success"
      });
      resetSlideImageDialog();
    } catch (error) {
      push({
        title: "保存图片失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setSavingSlideImage(false);
    }
  }

  async function handleUseExistingSlideImage() {
    if (!selectedImageFile) {
      return;
    }
    await saveSlideImageToPresentation(buildUploadedImageUrl(selectedImageFile));
  }

  async function handleUploadSlideImage(file: File) {
    const description = slideImageDescriptionDraft.trim() || activeImageSlideTitle || "课堂配图";
    setUploadingSlideImage(true);
    try {
      const uploaded = await uploadKnowledgeImage(file, description);
      upsertKnowledgeImage(uploaded);
      await saveSlideImageToPresentation(buildUploadedImageUrl(uploaded));
    } catch (error) {
      push({
        title: "上传图片失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setUploadingSlideImage(false);
    }
  }

  function resetLogoImageDialog() {
    setLogoImageModalOpen(false);
    setActiveLogoTarget(null);
    setSelectedLogoFileId("");
    setLogoDescriptionDraft("");
  }

  function openLogoImageDialog(target: LogoPickerTarget) {
    const style = target === "generation" ? generationStyle : editablePresentationStyle;
    const currentLogoUrl = normalizeText(style.logo_url).trim();
    const matchedImage =
      imageKnowledgeFiles.find((file) => file.id === style.logo_file_id) ||
      imageKnowledgeFiles.find((file) => buildUploadedImageUrl(file) === currentLogoUrl);
    const fallbackDescription = `${normalizeText(style.school_name).trim() || "学校"} Logo`;

    setActiveLogoTarget(target);
    setSelectedLogoFileId(matchedImage?.id || style.logo_file_id || "");
    setLogoDescriptionDraft(normalizeText(matchedImage?.description).trim() || fallbackDescription);
    setLogoImageModalOpen(true);
    if (!knowledgeFiles.length) {
      void loadKnowledgeFiles();
    }
  }

  async function handleUseExistingLogoImage() {
    if (!activeLogoTarget || !selectedLogoFileId) {
      return;
    }

    setUpdatingLogoImage(true);
    try {
      updateStyleLogo(activeLogoTarget, selectedLogoFileId);
      push({
        title: "Logo 已更新",
        description: "当前风格会使用这张 Logo。",
        tone: "success"
      });
      resetLogoImageDialog();
    } finally {
      setUpdatingLogoImage(false);
    }
  }

  async function handleClearLogoImage() {
    if (!activeLogoTarget) {
      return;
    }

    setUpdatingLogoImage(true);
    try {
      updateStyleLogo(activeLogoTarget, "");
      push({
        title: "已移除 Logo",
        description: "当前风格将不再显示学校 Logo。",
        tone: "success"
      });
      resetLogoImageDialog();
    } finally {
      setUpdatingLogoImage(false);
    }
  }

  async function handleUploadLogoImage(file: File) {
    if (!activeLogoTarget) {
      return;
    }

    const description = logoDescriptionDraft.trim() || `${normalizeText(activeLogoStyle?.school_name).trim() || "学校"} Logo`;
    setUploadingLogoImage(true);
    try {
      const uploaded = await uploadKnowledgeImage(file, description);
      upsertKnowledgeImage(uploaded);
      updateStyleLogo(activeLogoTarget, uploaded.id, uploaded);
      push({
        title: "Logo 已上传",
        description: "当前风格已切换到新上传的 Logo。",
        tone: "success"
      });
      resetLogoImageDialog();
    } catch (error) {
      push({
        title: "上传 Logo 失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setUploadingLogoImage(false);
    }
  }

  if (booting) {
    return (
      <Card className="flex min-h-[70vh] items-center justify-center">
        <div className="text-center">
          <p className="font-serif text-4xl text-ink">正在装载编辑器</p>
          <p className="mt-3 text-sm text-steel">同步文档详情、会话和临时偏好中。</p>
        </div>
      </Card>
    );
  }

  if (initError) {
    return (
      <Card className="flex min-h-[70vh] items-center justify-center">
        <div className="max-w-xl text-center">
          <p className="font-serif text-4xl text-ink">编辑器暂时没能完成初始化</p>
          <p className="mt-3 text-sm leading-6 text-steel">{initError}</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Button onClick={() => void initialize()}>重试初始化</Button>
            <Button variant="secondary" onClick={() => router.push(documentsHref)}>
              返回列表
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  if (!plan) {
    return (
      <Card className="flex min-h-[70vh] items-center justify-center">
        <div className="max-w-xl text-center">
          <p className="font-serif text-4xl text-ink">没有可展示的文档内容</p>
          <p className="mt-3 text-sm leading-6 text-steel">当前文档数据为空，建议返回列表后重新进入。</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Button variant="secondary" onClick={() => router.push(documentsHref)}>
              返回列表
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <>
      <div className="space-y-4">
        <Card>
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge>{docType}</Badge>
                {plan.subject ? <Badge className="bg-lagoon/10 text-lagoon">{plan.subject}</Badge> : null}
                {plan.grade ? <Badge className="bg-amber-100 text-amber-900">{plan.grade}</Badge> : null}
              </div>
              <h1 className="mt-4 font-serif text-4xl text-ink">{plan.title}</h1>
              <p className="mt-2 text-sm text-steel">会话 ID：{conversationId || "等待创建"}</p>
              {docType === "presentation" ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Badge className="bg-white text-steel">{THEME_LABELS[activePresentationStyle.theme]}</Badge>
                  <Badge className="bg-white text-steel">{DENSITY_LABELS[activePresentationStyle.density]}</Badge>
                  {activePresentationStyle.school_name ? (
                    <Badge className="bg-white text-steel">{activePresentationStyle.school_name}</Badge>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div className="flex flex-wrap gap-3">
              {docType === "lesson" ? (
                <>
                  {latestGeneratedPresentationId ? (
                    <Button
                      variant="secondary"
                      onClick={() => router.push(`/documents/${latestGeneratedPresentationId}/editor?type=presentation`)}
                    >
                      打开已保存 PPT
                    </Button>
                  ) : null}
                  <Button
                    onClick={() => {
                      setGenerationStyle(extractPresentationStyle(plan?.metadata));
                      setGeneratePresentationModalOpen(true);
                      if (!knowledgeFiles.length) {
                        void loadKnowledgeFiles();
                      }
                    }}
                  >
                    {latestGeneratedPresentationId ? "重新生成 PPT" : "生成 PPT"}
                  </Button>
                  <Button variant="secondary" disabled={generatingGames} onClick={() => void handleGenerateGames()}>
                    {generatingGames ? "生成小游戏中..." : miniGames.length ? "重新生成小游戏" : "生成小游戏"}
                  </Button>
                  <Button variant="secondary" onClick={() => void handleExport("docx")}>
                    导出 Word
                  </Button>
                  <Button variant="secondary" onClick={() => void handleExport("pdf")}>
                    导出 PDF
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setEditablePresentationStyle(activePresentationStyle);
                      setPresentationStyleModalOpen(true);
                      if (!knowledgeFiles.length) {
                        void loadKnowledgeFiles();
                      }
                    }}
                  >
                    风格设置
                  </Button>
                  <Button variant="secondary" onClick={() => void handleExport("pptx")}>
                    导出 PPTX
                  </Button>
                </>
              )}
              <Button
                variant="secondary"
                onClick={() => {
                  setSavepointModalOpen(true);
                  void loadSavepoints();
                }}
              >
                回退点
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  if (!knowledgeFiles.length) {
                    void loadKnowledgeFiles();
                  }
                  openSaveToKnowledgeModal();
                }}
              >
                保存进知识库
              </Button>
              <Button variant="secondary" onClick={() => setPreferencesModalOpen(true)}>
                临时偏好
              </Button>
              <Button variant="ghost" onClick={() => router.push(documentsHref)}>
                返回列表
              </Button>
            </div>
          </div>

          <div className="mt-6 flex items-center gap-4">
            <span className="text-sm font-semibold text-steel">预览占比</span>
            <input
              type="range"
              min={35}
              max={70}
              value={previewRatio}
              onChange={(event) => setPreviewRatio(Number(event.target.value))}
              className="w-52 accent-[#ff7a18]"
            />
            <span className="text-sm text-steel">{previewRatio}%</span>
          </div>

          {docType === "lesson" && latestGeneratedPresentationId ? (
            <div className="mt-6 rounded-[24px] bg-lagoon/8 p-4 text-sm leading-6 text-steel ring-1 ring-lagoon/10">
              <p className="font-semibold text-ink">已保存的 PPT 项目</p>
              <p className="mt-2">
                最近一次生成的演示文稿
                {latestGeneratedPresentationTitle ? `：《${latestGeneratedPresentationTitle}》` : ""}
                已经保存，可以直接继续编辑，不需要重新生成。
              </p>
              <p className="mt-1">
                当前教案累计关联 {generatedPresentationCount} 个 PPT 项目。
              </p>
            </div>
          ) : null}
        </Card>

        <div className="grid gap-4 xl:min-h-[72vh]" style={previewStyle}>
          <Card className="min-w-0 overflow-hidden">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.28em] text-steel">Live Preview</p>
                <h2 className="mt-2 font-serif text-3xl text-ink">
                  {docType === "lesson" ? "教案预览" : "PPT 效果预览"}
                </h2>
              </div>
              <Badge className="bg-pine/10 text-pine">实时更新</Badge>
            </div>

            <div className="app-scroll mt-6 max-h-[62vh] space-y-4 overflow-y-auto pr-2">
              {docType === "lesson"
                ? [
                    ...lessonSections(plan).map((section, index) => (
                      <div key={`${String(section.type || section.title)}-${index}`} className="rounded-[28px] bg-sand/45 p-5">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <h3 className="font-serif text-2xl text-ink">
                            {String(section.type || section.title || section.name || `章节 ${index + 1}`)}
                          </h3>
                          <Badge className="bg-white text-steel">
                            {typeof section.duration === "number" ? `${section.duration} 分钟` : "未设时长"}
                          </Badge>
                        </div>
                        <div className="mt-4">{renderParagraphs(section.content)}</div>
                        {Array.isArray(section.elements) && section.elements.length ? (
                          <div className="mt-4 space-y-2">
                            {section.elements.map((element, elementIndex) => {
                              const payload = element as Record<string, unknown>;
                              return (
                                <div key={elementIndex} className="rounded-2xl bg-white px-4 py-3 text-sm text-steel">
                                  {String(payload.type || payload.element_type || "元素")}：{String(payload.content || payload.description || "")}
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                      </div>
                    )),
                    ...(miniGames.length
                      ? [
                          <div key="lesson-games-header" className="rounded-[28px] bg-lagoon/10 p-5 ring-1 ring-lagoon/10">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <div>
                                <p className="text-xs uppercase tracking-[0.24em] text-steel">Interactive Block</p>
                                <h3 className="mt-2 font-serif text-2xl text-ink">课堂小游戏</h3>
                              </div>
                              <Badge className="bg-white text-lagoon">{miniGames.length} 个互动卡片</Badge>
                            </div>
                            <p className="mt-3 text-sm leading-6 text-steel">
                              这里展示的是已为当前教案生成的 HTML5 小游戏，后续生成 PPT 时会自动附上入口页。
                            </p>
                          </div>,
                          ...miniGames.map((game, index) => (
                            <MiniGamePreview key={game.id || `mini-game-${index}`} game={game} index={index} />
                          ))
                        ]
                      : [])
                  ]
                : previewSlides.map((slide, index) =>
                    renderPresentationSlidePreview(
                      slide,
                      index,
                      previewSlides.length,
                      activePresentationStyle,
                      docType === "presentation" ? openSlideImageDialog : undefined
                    )
                  )}
            </div>
          </Card>

          <Card className="min-w-0">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.28em] text-steel">Streaming Chat</p>
                <h2 className="mt-2 font-serif text-3xl text-ink">对话流</h2>
              </div>
              {streaming ? <Badge className="bg-amber-100 text-amber-900">正在生成</Badge> : null}
            </div>

            {streamStatus ? (
              <div className="mt-4 rounded-2xl bg-white px-4 py-3 text-sm text-steel ring-1 ring-slate-200">
                {streamStatus}
              </div>
            ) : null}

            <div ref={scrollRef} className="app-scroll mt-6 flex max-h-[54vh] flex-col gap-3 overflow-y-auto pr-2">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={cn(
                    "max-w-[88%] rounded-[24px] px-4 py-3 text-sm leading-6 shadow-sm",
                    message.role === "user" && "ml-auto bg-ink text-white",
                    message.role === "assistant" && "bg-sand text-ink",
                    message.role === "system" &&
                      "bg-white text-steel ring-1 ring-slate-200",
                    message.kind === "error" && "bg-rose-50 text-rose-900 ring-1 ring-rose-200",
                    message.kind === "tool" && "bg-lagoon/10 text-lagoon",
                    message.kind === "tool_result" && "bg-white text-lagoon ring-1 ring-lagoon/20"
                  )}
                >
                  <p className="whitespace-pre-wrap">{message.content}</p>
                </div>
              ))}
            </div>

            {pendingFollowUp ? (
              <div className="mt-5 rounded-[28px] bg-lagoon/8 p-4">
                <p className="text-sm font-semibold text-ink">需要补充回答</p>
                <p className="mt-2 text-sm text-steel">{pendingFollowUp.question}</p>
                {pendingFollowUp.previous_user_message ? (
                  <div className="mt-3 rounded-2xl bg-white/80 px-3 py-2 text-xs leading-6 text-steel ring-1 ring-lagoon/10">
                    上一轮需求：{pendingFollowUp.previous_user_message}
                  </div>
                ) : null}
                {pendingFollowUp.completed_steps?.length ? (
                  <div className="mt-3 rounded-2xl bg-white/80 px-3 py-3 ring-1 ring-lagoon/10">
                    <p className="text-xs font-semibold tracking-[0.12em] text-steel">已完成内容</p>
                    <div className="mt-2 space-y-2 text-sm text-ink/80">
                      {pendingFollowUp.completed_steps.map((step, index) => (
                        <p key={`${step}-${index}`}>{step}</p>
                      ))}
                    </div>
                  </div>
                ) : null}
                {pendingFollowUp.remaining_tasks?.length ? (
                  <div className="mt-3 rounded-2xl bg-white/80 px-3 py-3 ring-1 ring-lagoon/10">
                    <p className="text-xs font-semibold tracking-[0.12em] text-steel">待继续任务</p>
                    <div className="mt-2 space-y-2">
                      {pendingFollowUp.remaining_tasks.map((task, index) => {
                        const item = describePendingTask(task, index);
                        return (
                          <div key={`${item.summary}-${index}`} className="rounded-2xl bg-sand/60 px-3 py-2 text-sm text-ink/85">
                            <p className="font-medium text-ink">{item.summary}</p>
                            {item.detail ? <p className="mt-1 text-steel">{item.detail}</p> : null}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                {pendingFollowUp.options?.length ? (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {pendingFollowUp.options.map((option) => (
                      <Button
                        key={option}
                        variant="secondary"
                        disabled={streaming}
                        onClick={() => {
                          setFollowUpInput("");
                          void sendMessage(option);
                        }}
                      >
                        {option}
                      </Button>
                    ))}
                  </div>
                ) : null}
                <div className="mt-4 space-y-3">
                  <Textarea
                    className="min-h-[96px] bg-white"
                    placeholder="直接输入补充信息，系统会接着当前待办继续处理。"
                    value={followUpInput}
                    onChange={(event) => setFollowUpInput(event.target.value)}
                  />
                  <div className="flex flex-wrap gap-3">
                    <Button
                      disabled={streaming || !followUpInput.trim()}
                      onClick={() => {
                        const content = followUpInput.trim();
                        setFollowUpInput("");
                        void sendMessage(content);
                      }}
                    >
                      提交补充信息
                    </Button>
                    <Button variant="secondary" disabled={streaming} onClick={() => setInput(followUpInput || input)}>
                      复制到主输入框
                    </Button>
                  </div>
                </div>
              </div>
            ) : null}

            {pendingConfirmation ? (
              <div className="mt-5 rounded-[28px] bg-amber-50 p-4">
                <p className="text-sm font-semibold text-ink">待确认操作</p>
                <p className="mt-2 text-sm text-steel">{pendingConfirmation.operation_description}</p>
                <p className="mt-2 text-sm text-steel">{pendingConfirmation.proposed_changes}</p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <Button onClick={() => void sendMessage("/confirm")}>确认执行</Button>
                  <Button variant="secondary" onClick={() => void sendMessage("/cancel")}>
                    取消本次修改
                  </Button>
                </div>
              </div>
            ) : null}

            <div className="mt-5 space-y-3">
              <Textarea
                className="min-h-[140px]"
                placeholder={
                  docType === "lesson"
                    ? "例如：把导入改成生活化实验，引导学生先做猜想。"
                    : "例如：新增一页总结幻灯片，提炼本课三个关键词。"
                }
                value={input}
                onChange={(event) => setInput(event.target.value)}
              />
              <div className="flex flex-wrap gap-3">
                <Button
                  disabled={streaming || !input.trim()}
                  onClick={() => void sendMessage()}
                  className="h-12 px-6"
                >
                  {streaming ? "生成中..." : "发送消息"}
                </Button>
                <Button variant="secondary" disabled={streaming} onClick={() => setInput("/confirm")}>
                  填入 /confirm
                </Button>
                <Button variant="secondary" disabled={streaming} onClick={() => setInput("/cancel")}>
                  填入 /cancel
                </Button>
              </div>
            </div>
          </Card>
        </div>
      </div>

      <Modal
        open={savepointModalOpen}
        title="回退点管理"
        description="这里只保存当前文件快照，用于编辑器内回退恢复；不会写入知识库。"
        onClose={() => setSavepointModalOpen(false)}
      >
        <div className="space-y-4">
          <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
            如果你希望这份快照也能出现在知识库检索里，请使用顶部的“保存进知识库”。那条链路会同时保留回退点，但这里不会。
          </div>
          <div className="flex flex-col gap-3 md:flex-row">
            <Input
              placeholder="回退点标签，例如：对话改稿前"
              value={savepointLabel}
              onChange={(event) => setSavepointLabel(event.target.value)}
            />
            <Button onClick={() => void handleCreateSavepoint()}>仅保存回退点</Button>
          </div>
          <div className="max-h-[50vh] space-y-3 overflow-y-auto">
            {savepoints.length ? (
              savepoints.map((item) => (
                <div key={item.id} className="rounded-[24px] border border-slate-200 bg-white p-4">
                  <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="font-semibold text-ink">{item.label}</p>
                      <p className="mt-2 text-sm text-steel">{formatDateTime(item.created_at)}</p>
                    </div>
                    <div className="flex flex-wrap gap-3">
                      <Button
                        variant="secondary"
                        onClick={async () => {
                          try {
                            await restoreSavepoint(item.id);
                            const refreshedPlan = await getPlan(planId);
                            setPlan(refreshedPlan);
                            push({ title: "已恢复回退点", tone: "success" });
                          } catch (error) {
                            push({
                              title: "恢复失败",
                              description: error instanceof Error ? error.message : "请稍后重试。",
                              tone: "error"
                            });
                          }
                        }}
                      >
                        恢复
                      </Button>
                      <Button
                        variant="danger"
                        onClick={async () => {
                          try {
                            await deleteSavepoint(item.id);
                            await loadSavepoints();
                            push({ title: "回退点已删除", tone: "success" });
                          } catch (error) {
                            push({
                              title: "删除失败",
                              description: error instanceof Error ? error.message : "请稍后重试。",
                              tone: "error"
                            });
                          }
                        }}
                      >
                        删除
                      </Button>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <p className="text-sm text-steel">当前还没有任何回退点。</p>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        open={saveToKnowledgeModalOpen}
        title="保存进知识库"
        description="会把当前文件内容保存为一份知识库快照，同时自动生成一个可恢复的回退点。"
        onClose={() => {
          if (!savingKnowledgeSnapshot) {
            setSaveToKnowledgeModalOpen(false);
          }
        }}
      >
        <div className="space-y-4">
          <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
            这里保存的是当前文件快照，不会继续跟随后续编辑自动更新。保存后，你可以在知识库搜索到它，也可以在回退点里恢复到这一刻。
          </div>

          <label className="block text-sm font-semibold text-ink">
            回退点标签
            <Input
              className="mt-2"
              placeholder="例如：初稿完成"
              value={knowledgeSnapshotLabel}
              onChange={(event) => setKnowledgeSnapshotLabel(event.target.value)}
            />
          </label>

          <label className="block text-sm font-semibold text-ink">
            知识库文件名
            <Input
              className="mt-2"
              placeholder="例如：浮力教案-初稿快照"
              value={knowledgeSnapshotTitle}
              onChange={(event) => setKnowledgeSnapshotTitle(event.target.value)}
            />
          </label>

          <label className="block text-sm font-semibold text-ink">
            文件说明
            <Textarea
              className="mt-2 min-h-[120px]"
              placeholder="可选填写这份快照适合什么场景、和正式稿的区别等。"
              value={knowledgeSnapshotDescription}
              onChange={(event) => setKnowledgeSnapshotDescription(event.target.value)}
            />
          </label>

          <label className="block text-sm font-semibold text-ink">
            文件标签
            <Input
              className="mt-2"
              placeholder="例如：回退点, 初稿, 浮力"
              value={knowledgeSnapshotTags}
              onChange={(event) => setKnowledgeSnapshotTags(event.target.value)}
            />
          </label>

          <div className="flex flex-wrap justify-end gap-3">
            <Button variant="secondary" disabled={savingKnowledgeSnapshot} onClick={() => setSaveToKnowledgeModalOpen(false)}>
              取消
            </Button>
            <Button disabled={savingKnowledgeSnapshot} onClick={() => void handleSaveSnapshotToKnowledge()}>
              {savingKnowledgeSnapshot ? "保存中..." : "保存当前快照到知识库"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={generatePresentationModalOpen}
        title="根据教案生成 PPT"
        description="系统会先整理课堂展示内容，再按当前可用版式生成可编辑的 PPT 初稿。"
        onClose={() => {
          if (!generatingPresentation) {
            setGeneratePresentationModalOpen(false);
          }
        }}
      >
        <div className="space-y-4">
          <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
            系统会严格沿着当前教案流程，先整理出“课堂上给学生看的内容稿”，再拆成当前已注册版式的幻灯片，并保留每页的图片占位和讲解备注。
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-semibold text-ink">
              主题风格
              <Select
                className="mt-2"
                value={generationStyle.theme}
                onChange={(event) => updateStyleField("generation", "theme", event.target.value)}
              >
                {Object.entries(THEME_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>

            <label className="block text-sm font-semibold text-ink">
              内容密度
              <Select
                className="mt-2"
                value={generationStyle.density}
                onChange={(event) => updateStyleField("generation", "density", event.target.value)}
              >
                {Object.entries(DENSITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>
          </div>

          <div className="grid gap-4 md:grid-cols-[1.2fr_1fr]">
            <label className="block text-sm font-semibold text-ink">
              学校/机构名称
              <Input
                className="mt-2"
                placeholder="可选，例如：XX 实验学校"
                value={generationStyle.school_name || ""}
                onChange={(event) => updateStyleField("generation", "school_name", event.target.value)}
              />
            </label>

            <div className="block text-sm font-semibold text-ink">
              学校 Logo
              <div className="mt-2 rounded-[24px] bg-white p-3 ring-1 ring-slate-200">
                <div className="flex items-center gap-3">
                  {generationStyle.logo_url ? (
                    <SafeImage
                      src={resolvePresentationImageUrl(generationStyle.logo_url)}
                      alt="Logo 预览"
                      width={56}
                      height={56}
                      className="h-14 w-14 rounded-2xl object-contain"
                    />
                  ) : (
                    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-sand text-xs text-steel">
                      Logo
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-ink">
                      {generationStyle.logo_url ? "当前已设置 Logo" : "当前未设置 Logo"}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-steel">
                      可从知识库选图，也可以上传新 Logo 后裁剪成方形。
                    </p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-3">
                  <Button variant="secondary" onClick={() => openLogoImageDialog("generation")}>
                    选择或上传 Logo
                  </Button>
                  <Button variant="ghost" onClick={() => updateStyleLogo("generation", "")}>
                    不使用 Logo
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {generationStyle.logo_url ? (
            <div className="flex items-center gap-3 rounded-[24px] bg-white px-4 py-3 ring-1 ring-slate-200">
              <SafeImage
                src={resolvePresentationImageUrl(generationStyle.logo_url)}
                alt="Logo 预览"
                width={48}
                height={48}
                className="h-12 w-12 rounded-2xl object-contain"
              />
              <div className="text-sm text-steel">
                当前会把 Logo 用于封面和页眉，导出与预览都会同步展示。
              </div>
            </div>
          ) : null}

          <label className="block text-sm font-semibold text-ink">
            补充课程内容
            <Textarea
              className="mt-2 min-h-[120px] bg-white"
              placeholder="可选填写你真实上课想展示的材料，例如例题文本、课堂提问语、活动说明、希望强调的结论。"
              value={presentationCourseContext}
              onChange={(event) => setPresentationCourseContext(event.target.value)}
            />
          </label>

          <div className="flex items-center justify-between">
            <p className="text-sm font-semibold text-ink">补充参考资料</p>
            <Button variant="secondary" disabled={loadingKnowledgeFiles} onClick={() => void loadKnowledgeFiles()}>
              {loadingKnowledgeFiles ? "加载中..." : "刷新资料"}
            </Button>
          </div>

          <div className="max-h-[320px] space-y-3 overflow-y-auto pr-1">
            {loadingKnowledgeFiles ? (
              <div className="rounded-[24px] bg-white p-4 text-sm text-steel">正在加载知识库文件…</div>
            ) : knowledgeFiles.length ? (
              knowledgeFiles.map((file) => {
                const checked = selectedKnowledgeFileIds.includes(file.id);
                return (
                  <label
                    key={file.id}
                    className={`flex cursor-pointer items-start gap-3 rounded-[24px] border p-4 transition ${
                      checked ? "border-ink bg-sand/70" : "border-slate-200 bg-white"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-[#183149]"
                      checked={checked}
                      onChange={(event) => toggleKnowledgeFileSelection(file.id, event.target.checked)}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge>{file.file_type}</Badge>
                        <span className="text-sm font-semibold text-ink">{file.filename}</span>
                      </div>
                      {file.description ? <p className="mt-2 text-sm text-steel">{file.description}</p> : null}
                    </div>
                  </label>
                );
              })
            ) : (
              <div className="rounded-[24px] bg-white p-4 text-sm text-steel">
                还没有可选的知识库文件。也可以直接不勾选，单独根据教案生成。
              </div>
            )}
          </div>

          <div className="flex flex-wrap justify-end gap-3">
            <Button
              variant="secondary"
              disabled={generatingPresentation}
              onClick={() => setGeneratePresentationModalOpen(false)}
            >
              取消
            </Button>
            <Button disabled={generatingPresentation} onClick={() => void handleGeneratePresentation()}>
              {generatingPresentation ? "生成中..." : "开始生成"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={slideImageModalOpen}
        title="页面配图"
        description="这里可以直接为当前图片占位选择知识库已有图片，或上传新图片并立即用于这页。"
        onClose={() => {
          if (!savingSlideImage && !uploadingSlideImage) {
            resetSlideImageDialog();
          }
        }}
        className="max-w-5xl"
        overlayClassName="z-[70]"
        bodyClassName="max-h-[calc(100vh-12rem)]"
      >
        <div className="space-y-4">
          <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
            当前页面：{activeImageSlideTitle || "未命名页"}。保存后，预览和 PPTX 导出都会同步使用这张图片。
          </div>

          <ImageAssetPicker
            assetLabel="页面配图"
            descriptionLabel="图片说明"
            descriptionPlaceholder="例如：浮力实验装置照片"
            descriptionHelper="这段文字会同时作为知识库图片说明，并展示在当前页面的图片说明区。"
            descriptionValue={slideImageDescriptionDraft}
            onDescriptionChange={setSlideImageDescriptionDraft}
            previewHint="支持从知识库选图，或上传本地图片后直接裁剪。预览和 PPTX 导出会使用同一张图。"
            previewImageUrl={activeImagePreviewUrl}
            imageFiles={imageKnowledgeFiles}
            loadingImages={loadingKnowledgeFiles}
            onRefreshImages={() => void loadKnowledgeFiles()}
            selectedFileId={selectedImageFileId}
            onSelectFile={setSelectedImageFileId}
            selectedFileDescription={selectedImageFile?.description || null}
            cropPresets={SLIDE_IMAGE_CROP_PRESETS}
            onUploadCropped={(file) => handleUploadSlideImage(file)}
            uploading={uploadingSlideImage}
            onUseSelected={() => handleUseExistingSlideImage()}
            usingSelected={savingSlideImage}
            onClear={() => saveSlideImageToPresentation(null)}
            clearing={savingSlideImage}
            clearLabel="移除实图"
            useSelectedLabel="使用已选图片"
            uploadLabel="上传裁剪并使用"
          />
        </div>
      </Modal>

      <Modal
        open={logoImageModalOpen}
        title="学校 Logo"
        description="这里可以统一处理 Logo：选知识库已有图片，或上传新图后裁成方形，再回写到当前风格。"
        onClose={() => {
          if (!updatingLogoImage && !uploadingLogoImage) {
            resetLogoImageDialog();
          }
        }}
        className="max-w-5xl"
        overlayClassName="z-[70]"
        bodyClassName="max-h-[calc(100vh-12rem)]"
      >
        <ImageAssetPicker
          assetLabel="学校 Logo"
          descriptionLabel="Logo 名称 / 说明"
          descriptionPlaceholder="例如：XX 实验学校校徽"
          descriptionHelper="上传后会存入知识库，之后生成 PPT 和编辑现有 PPT 时都可以复用。"
          descriptionValue={logoDescriptionDraft}
          onDescriptionChange={setLogoDescriptionDraft}
          previewHint="建议把 Logo 主体放在裁剪框中央，系统会按方形输出，便于封面和页眉统一显示。"
          previewImageUrl={activeLogoPreviewUrl}
          imageFiles={imageKnowledgeFiles}
          loadingImages={loadingKnowledgeFiles}
          onRefreshImages={() => void loadKnowledgeFiles()}
          selectedFileId={selectedLogoFileId}
          onSelectFile={setSelectedLogoFileId}
          selectedFileDescription={selectedLogoFile?.description || null}
          cropPresets={LOGO_IMAGE_CROP_PRESETS}
          onUploadCropped={(file) => handleUploadLogoImage(file)}
          uploading={uploadingLogoImage}
          onUseSelected={() => handleUseExistingLogoImage()}
          usingSelected={updatingLogoImage}
          onClear={() => handleClearLogoImage()}
          clearing={updatingLogoImage}
          clearLabel="不使用 Logo"
          useSelectedLabel="使用已选 Logo"
          uploadLabel="上传裁剪并使用"
        />
      </Modal>

      <Modal
        open={presentationStyleModalOpen}
        title="PPT 风格设置"
        description="这里修改的是当前演示文稿的展示风格，预览和导出会立即使用同一套规则。"
        onClose={() => {
          if (!savingPresentationStyle) {
            setPresentationStyleModalOpen(false);
            setEditablePresentationStyle(activePresentationStyle);
          }
        }}
      >
        <div className="space-y-4">
          <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
            风格设置不会重写整套文稿内容，但会影响预览分页、字号、封面/页眉品牌元素和最终 PPTX 导出效果。
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="block text-sm font-semibold text-ink">
              主题风格
              <Select
                className="mt-2"
                value={editablePresentationStyle.theme}
                onChange={(event) => updateStyleField("editor", "theme", event.target.value)}
              >
                {Object.entries(THEME_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>

            <label className="block text-sm font-semibold text-ink">
              内容密度
              <Select
                className="mt-2"
                value={editablePresentationStyle.density}
                onChange={(event) => updateStyleField("editor", "density", event.target.value)}
              >
                {Object.entries(DENSITY_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </label>
          </div>

          <div className="grid gap-4 md:grid-cols-[1.2fr_1fr]">
            <label className="block text-sm font-semibold text-ink">
              学校/机构名称
              <Input
                className="mt-2"
                placeholder="可选，例如：XX 中学"
                value={editablePresentationStyle.school_name || ""}
                onChange={(event) => updateStyleField("editor", "school_name", event.target.value)}
              />
            </label>

            <div className="block text-sm font-semibold text-ink">
              学校 Logo
              <div className="mt-2 rounded-[24px] bg-white p-3 ring-1 ring-slate-200">
                <div className="flex items-center gap-3">
                  {editablePresentationStyle.logo_url ? (
                    <SafeImage
                      src={resolvePresentationImageUrl(editablePresentationStyle.logo_url)}
                      alt="Logo 预览"
                      width={56}
                      height={56}
                      className="h-14 w-14 rounded-2xl object-contain"
                    />
                  ) : (
                    <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-sand text-xs text-steel">
                      Logo
                    </div>
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-ink">
                      {editablePresentationStyle.logo_url ? "当前已设置 Logo" : "当前未设置 Logo"}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-steel">
                      支持选知识库已有图片，或上传后裁剪成方形 Logo。
                    </p>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-3">
                  <Button variant="secondary" onClick={() => openLogoImageDialog("editor")}>
                    选择或上传 Logo
                  </Button>
                  <Button variant="ghost" onClick={() => updateStyleLogo("editor", "")}>
                    不使用 Logo
                  </Button>
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-[24px] bg-white p-4 ring-1 ring-slate-200">
            <div className="flex items-center gap-3">
              {editablePresentationStyle.logo_url ? (
                <SafeImage
                  src={resolvePresentationImageUrl(editablePresentationStyle.logo_url)}
                  alt="Logo 预览"
                  width={48}
                  height={48}
                  className="h-12 w-12 rounded-2xl object-contain"
                />
              ) : (
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-sand text-xs text-steel">
                  Logo
                </div>
              )}
              <div>
                <p className="text-sm font-semibold text-ink">
                  {editablePresentationStyle.school_name || "未设置学校名"}
                </p>
                <p className="mt-1 text-sm text-steel">
                  {THEME_LABELS[editablePresentationStyle.theme]} · {DENSITY_LABELS[editablePresentationStyle.density]}
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap justify-end gap-3">
            <Button
              variant="secondary"
              disabled={savingPresentationStyle}
              onClick={() => {
                setPresentationStyleModalOpen(false);
                setEditablePresentationStyle(activePresentationStyle);
              }}
            >
              取消
            </Button>
            <Button disabled={savingPresentationStyle} onClick={() => void handleSavePresentationStyle()}>
              {savingPresentationStyle ? "保存中..." : "保存风格"}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal
        open={preferencesModalOpen}
        title="会话级临时偏好"
        description="这些设置只作用于当前会话，会和全局偏好一起注入编辑器。"
        onClose={() => setPreferencesModalOpen(false)}
        className="max-w-3xl"
      >
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">教学节奏</p>
              <Select
                value={tempPreferences.teaching_pace ?? ""}
                onChange={(event) =>
                  updateTempPreference(
                    "teaching_pace",
                    event.target.value ? (event.target.value as TempPreferencesPayload["teaching_pace"]) : undefined
                  )
                }
              >
                <option value="">不指定</option>
                {TEACHING_PACE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-steel">
                {TEACHING_PACE_OPTIONS.find((option) => option.value === tempPreferences.teaching_pace)?.helper ||
                  "例如希望内容更紧凑，或关键知识点讲得更透。"}
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">互动强度</p>
              <Select
                value={tempPreferences.interaction_level ?? ""}
                onChange={(event) =>
                  updateTempPreference(
                    "interaction_level",
                    event.target.value ? (event.target.value as TempPreferencesPayload["interaction_level"]) : undefined
                  )
                }
              >
                <option value="">不指定</option>
                {INTERACTION_LEVEL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-steel">
                {INTERACTION_LEVEL_OPTIONS.find((option) => option.value === tempPreferences.interaction_level)?.helper ||
                  "例如希望讲授为主，或增加更多提问和讨论。"}
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">内容展开</p>
              <Select
                value={tempPreferences.detail_level ?? ""}
                onChange={(event) =>
                  updateTempPreference(
                    "detail_level",
                    event.target.value ? (event.target.value as TempPreferencesPayload["detail_level"]) : undefined
                  )
                }
              >
                <option value="">不指定</option>
                {DETAIL_LEVEL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-steel">
                {DETAIL_LEVEL_OPTIONS.find((option) => option.value === tempPreferences.detail_level)?.helper ||
                  "例如只保留核心结论，或把关键步骤完整展开。"}
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">表达风格</p>
              <Select
                value={tempPreferences.language_style ?? ""}
                onChange={(event) =>
                  updateTempPreference(
                    "language_style",
                    event.target.value ? (event.target.value as TempPreferencesPayload["language_style"]) : undefined
                  )
                }
              >
                <option value="">不指定</option>
                {LANGUAGE_STYLE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-steel">
                {LANGUAGE_STYLE_OPTIONS.find((option) => option.value === tempPreferences.language_style)?.helper ||
                  "例如更专业一些，或更接近真实课堂口语。"}
              </p>
            </div>

            <div className="space-y-2 md:col-span-2">
              <p className="text-sm font-semibold text-ink">视觉呈现</p>
              <Select
                value={tempPreferences.visual_focus ?? ""}
                onChange={(event) =>
                  updateTempPreference(
                    "visual_focus",
                    event.target.value ? (event.target.value as TempPreferencesPayload["visual_focus"]) : undefined
                  )
                }
              >
                <option value="">不指定</option>
                {VISUAL_FOCUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
              <p className="text-xs text-steel">
                {VISUAL_FOCUS_OPTIONS.find((option) => option.value === tempPreferences.visual_focus)?.helper ||
                  "对 PPT、配图和截图位是否偏向图例，可以在这里提前说明。"}
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-semibold text-ink">其他要求</p>
            <Textarea
              className="min-h-[140px]"
              placeholder="例如：多用生活化例子；避免整页大段文字；保留课堂追问。"
              value={tempPreferences.other_notes ?? ""}
              onChange={(event) => updateTempPreference("other_notes", event.target.value || undefined)}
            />
          </div>

          <div className="rounded-[28px] bg-sand/60 p-4">
            <p className="text-sm font-semibold text-ink">当前摘要</p>
            <div className="mt-3 space-y-2 text-sm text-steel">
              {tempPreferenceSummary.length ? (
                tempPreferenceSummary.map((item) => (
                  <p key={item}>{item}</p>
                ))
              ) : (
                <p>当前未设置会话级临时偏好，编辑器会只使用全局偏好和上下文。</p>
              )}
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <Button onClick={() => void saveTempPreferences()}>保存偏好</Button>
            <Button
              variant="secondary"
              onClick={() => {
                if (conversationId) {
                  void loadTempPreferences(conversationId);
                }
              }}
            >
              重新读取
            </Button>
            <Button variant="ghost" onClick={() => setTempPreferences({})}>
              清空当前填写
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}
