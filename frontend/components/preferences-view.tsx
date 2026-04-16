"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

import { useToast } from "@/components/toast-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  createPreference,
  deletePreference,
  listPreferences,
  parsePreferenceText,
  togglePreference,
  updatePreference
} from "@/lib/api";
import {
  buildPreferencePromptInjection,
  DETAIL_LEVEL_OPTIONS,
  INTERACTION_LEVEL_OPTIONS,
  LANGUAGE_STYLE_OPTIONS,
  normalizeTempPreferencesPayload,
  parsePreferencePromptInjection,
  summarizeTempPreferences,
  TEACHING_PACE_OPTIONS,
  VISUAL_FOCUS_OPTIONS
} from "@/lib/temp-preferences";
import type { PreferencePreset, PreferenceSuggestion, TempPreferencesPayload } from "@/lib/types";
import { formatDateTime, normalizeLines } from "@/lib/utils";

type PreferenceFormState = TempPreferencesPayload & {
  name: string;
  description: string;
  tags: string;
  is_active: boolean;
};

function emptyForm(): PreferenceFormState {
  return {
    name: "",
    description: "",
    tags: "",
    is_active: true
  };
}

function buildFormFromPreset(
  item: Pick<
    PreferencePreset,
    "name" | "description" | "prompt_injection" | "structured_preferences" | "tags" | "is_active"
  >
) {
  const structuredPreferences =
    item.structured_preferences
      ? normalizeTempPreferencesPayload(item.structured_preferences)
      : parsePreferencePromptInjection(item.prompt_injection);
  return {
    ...emptyForm(),
    ...structuredPreferences,
    name: item.name,
    description: item.description || "",
    tags: item.tags.join("\n"),
    is_active: item.is_active
  };
}

export function PreferencesView() {
  const { push } = useToast();
  const [isPending, startTransition] = useTransition();
  const [items, setItems] = useState<PreferencePreset[]>([]);
  const [form, setForm] = useState<PreferenceFormState>(emptyForm());
  const [draftText, setDraftText] = useState("");
  const [suggestions, setSuggestions] = useState<PreferenceSuggestion[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [presetValidationErrors, setPresetValidationErrors] = useState<string[]>([]);
  const [draftValidationError, setDraftValidationError] = useState<string | null>(null);

  const structuredPreferences = useMemo(
    () =>
      normalizeTempPreferencesPayload({
        teaching_pace: form.teaching_pace,
        interaction_level: form.interaction_level,
        detail_level: form.detail_level,
        language_style: form.language_style,
        visual_focus: form.visual_focus,
        other_notes: form.other_notes
      }),
    [form]
  );

  const promptPreview = useMemo(
    () => buildPreferencePromptInjection(structuredPreferences),
    [structuredPreferences]
  );

  const summaryLines = useMemo(() => summarizeTempPreferences(structuredPreferences), [structuredPreferences]);

  const loadPreferences = useCallback(async () => {
    try {
      setItems(await listPreferences());
    } catch (error) {
      push({
        title: "偏好加载失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    }
  }, [push]);

  useEffect(() => {
    void loadPreferences();
  }, [loadPreferences]);

  function resetForm() {
    setForm(emptyForm());
    setEditingId(null);
    setPresetValidationErrors([]);
  }

  function updateFormField<K extends keyof PreferenceFormState>(key: K, value: PreferenceFormState[K]) {
    setPresetValidationErrors([]);
    setForm((current) => ({ ...current, [key]: value }));
  }

  function fillFromSuggestion(item: PreferenceSuggestion) {
    setEditingId(null);
    setPresetValidationErrors([]);
    setForm({
      ...emptyForm(),
      ...normalizeTempPreferencesPayload(item.structured_preferences),
      name: item.name,
      description: item.description,
      tags: item.tags.join("\n"),
      is_active: true
    });
  }

  function validatePresetForm() {
    const errors: string[] = [];
    if (!form.name.trim()) {
      errors.push("请填写预设名称。");
    }
    if (!promptPreview.trim()) {
      errors.push("请至少选择一项偏好维度，或填写“其他要求”。");
    }
    return errors;
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
      <div className="space-y-6">
        <Card>
          <p className="text-xs uppercase tracking-[0.28em] text-steel">Preference Presets</p>
          <h1 className="mt-2 font-serif text-4xl text-ink">全局偏好设置</h1>
          <p className="mt-3 text-sm leading-6 text-steel">
            预设会长期注入到编辑器提示词里。现在可以按几个常见维度选填，再补充其他自由要求。
          </p>
        </Card>

        <Card>
          <div className="space-y-4">
            <Input
              placeholder="预设名称"
              value={form.name}
              onChange={(event) => updateFormField("name", event.target.value)}
            />
            <Input
              placeholder="描述"
              value={form.description}
              onChange={(event) => updateFormField("description", event.target.value)}
            />

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <p className="text-sm font-semibold text-ink">教学节奏</p>
                <Select
                  value={form.teaching_pace ?? ""}
                  onChange={(event) =>
                    updateFormField(
                      "teaching_pace",
                      event.target.value ? (event.target.value as PreferenceFormState["teaching_pace"]) : undefined
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
                  {TEACHING_PACE_OPTIONS.find((option) => option.value === form.teaching_pace)?.helper ||
                    "例如默认更紧凑，或关键内容更适合放慢讲透。"}
                </p>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-semibold text-ink">互动强度</p>
                <Select
                  value={form.interaction_level ?? ""}
                  onChange={(event) =>
                    updateFormField(
                      "interaction_level",
                      event.target.value ? (event.target.value as PreferenceFormState["interaction_level"]) : undefined
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
                  {INTERACTION_LEVEL_OPTIONS.find((option) => option.value === form.interaction_level)?.helper ||
                    "例如更偏讲授，或希望整体多一些提问互动。"}
                </p>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-semibold text-ink">内容展开</p>
                <Select
                  value={form.detail_level ?? ""}
                  onChange={(event) =>
                    updateFormField(
                      "detail_level",
                      event.target.value ? (event.target.value as PreferenceFormState["detail_level"]) : undefined
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
                  {DETAIL_LEVEL_OPTIONS.find((option) => option.value === form.detail_level)?.helper ||
                    "例如只保留概览，或把过程步骤完整展开。"}
                </p>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-semibold text-ink">表达风格</p>
                <Select
                  value={form.language_style ?? ""}
                  onChange={(event) =>
                    updateFormField(
                      "language_style",
                      event.target.value ? (event.target.value as PreferenceFormState["language_style"]) : undefined
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
                  {LANGUAGE_STYLE_OPTIONS.find((option) => option.value === form.language_style)?.helper ||
                    "例如更专业严谨，或更贴近真实课堂口语。"}
                </p>
              </div>

              <div className="space-y-2 md:col-span-2">
                <p className="text-sm font-semibold text-ink">视觉呈现</p>
                <Select
                  value={form.visual_focus ?? ""}
                  onChange={(event) =>
                    updateFormField(
                      "visual_focus",
                      event.target.value ? (event.target.value as PreferenceFormState["visual_focus"]) : undefined
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
                  {VISUAL_FOCUS_OPTIONS.find((option) => option.value === form.visual_focus)?.helper ||
                    "适合用于控制 PPT 是否更偏文字、图片、案例图或截图位。"}
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-semibold text-ink">其他要求</p>
              <Textarea
                className="min-h-[140px]"
                placeholder="例如：默认保留课堂追问；多用生活化例子；避免整页大段文字。"
                value={form.other_notes ?? ""}
                onChange={(event) => updateFormField("other_notes", event.target.value || undefined)}
              />
            </div>

            <Input
              placeholder="标签，换行或逗号分隔"
              value={form.tags}
              onChange={(event) => updateFormField("tags", event.target.value)}
            />

            <div className="rounded-[24px] bg-sand/60 p-4">
              <p className="text-sm font-semibold text-ink">偏好摘要</p>
              <div className="mt-3 space-y-2 text-sm text-steel">
                {summaryLines.length ? summaryLines.map((line) => <p key={line}>{line}</p>) : <p>当前还没有选择任何偏好维度。</p>}
              </div>
            </div>

            <div className="rounded-[24px] border border-slate-200 bg-white p-4">
              <p className="text-sm font-semibold text-ink">系统注入预览</p>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-ink/80">
                {promptPreview || "请选择至少一项偏好，或填写其他要求。"}
              </p>
            </div>

            <div className="flex items-center justify-between rounded-[24px] bg-sand/60 px-4 py-3">
              <span className="text-sm font-semibold text-ink">创建后立即激活</span>
              <Switch checked={form.is_active} onCheckedChange={(checked) => updateFormField("is_active", checked)} />
            </div>

            <div className="flex flex-wrap gap-3">
              <Button
                disabled={isPending}
                onClick={() => {
                  const errors = validatePresetForm();
                  if (errors.length) {
                    setPresetValidationErrors(errors);
                    push({
                      title: editingId ? "暂时还不能保存" : "暂时还不能创建",
                      description: errors.join(" "),
                      tone: "error"
                    });
                    return;
                  }

                  startTransition(async () => {
                    try {
                      const payload = {
                        name: form.name.trim(),
                        description: form.description.trim() || undefined,
                        prompt_injection: promptPreview.trim(),
                        structured_preferences: structuredPreferences,
                        tags: normalizeLines(form.tags.replaceAll(",", "\n")),
                        is_active: form.is_active
                      };
                      if (editingId) {
                        await updatePreference(editingId, payload);
                        push({ title: "预设已更新", tone: "success" });
                      } else {
                        await createPreference(payload);
                        push({ title: "预设已创建", tone: "success" });
                      }
                      resetForm();
                      await loadPreferences();
                    } catch (error) {
                      push({
                        title: "保存失败",
                        description: error instanceof Error ? error.message : "请稍后重试。",
                        tone: "error"
                      });
                    }
                  });
                }}
              >
                {editingId ? "保存修改" : "新增预设"}
              </Button>
              <Button variant="secondary" onClick={resetForm}>
                清空表单
              </Button>
            </div>
            {presetValidationErrors.length ? (
              <div className="rounded-[20px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                {presetValidationErrors.map((message) => (
                  <p key={message}>{message}</p>
                ))}
              </div>
            ) : null}
          </div>
        </Card>

        <Card>
          <p className="text-sm font-semibold text-ink">自然语言添加</p>
          <Textarea
            className="mt-3 min-h-[160px]"
            placeholder="例如：希望所有教案更偏向探究式教学，语言更口语化，并保留课堂提问。"
            value={draftText}
            onChange={(event) => {
              setDraftValidationError(null);
              setDraftText(event.target.value);
            }}
          />
          {draftValidationError ? <p className="mt-3 text-sm text-rose-700">{draftValidationError}</p> : null}
          <Button
            className="mt-4"
            variant="secondary"
            disabled={isPending}
            onClick={() => {
              if (!draftText.trim()) {
                const message = "请先填写要解析的偏好描述，例如教学节奏、表达风格或其他要求。";
                setDraftValidationError(message);
                push({
                  title: "还没有可解析的内容",
                  description: message,
                  tone: "error"
                });
                return;
              }

              startTransition(async () => {
                try {
                  const response = await parsePreferenceText(draftText.trim());
                  setSuggestions(response.suggestions);
                  push({
                    title: "解析完成",
                    description: `共生成 ${response.suggestions.length} 条建议。`,
                    tone: "success"
                  });
                } catch (error) {
                  push({
                    title: "解析失败",
                    description: error instanceof Error ? error.message : "后端未返回建议。",
                    tone: "error"
                  });
                }
              });
            }}
          >
            解析自然语言
          </Button>
        </Card>
      </div>

      <div className="space-y-6">
        <Card>
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.28em] text-steel">Active Presets</p>
              <h2 className="mt-2 font-serif text-3xl text-ink">预设列表</h2>
            </div>
            <Button variant="secondary" onClick={() => void loadPreferences()}>
              刷新
            </Button>
          </div>
          <div className="mt-5 space-y-4">
            {items.length ? (
              items.map((item) => {
                const presetSummaryLines = summarizeTempPreferences(item.structured_preferences);
                return (
                  <div key={item.id} className="rounded-[28px] border border-slate-200 bg-white p-5">
                    <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                      <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="font-serif text-2xl text-ink">{item.name}</h3>
                        {item.tags.map((tag) => (
                          <Badge key={tag} className="bg-lagoon/10 text-lagoon">
                            {tag}
                          </Badge>
                        ))}
                      </div>
                      {item.description ? <p className="mt-3 text-sm text-steel">{item.description}</p> : null}
                      <div className="mt-3 space-y-2 text-sm text-steel">
                        {presetSummaryLines.length ? (
                          presetSummaryLines.map((line) => <p key={line}>{line}</p>)
                        ) : (
                          <p>暂无结构化偏好摘要。</p>
                        )}
                      </div>
                      <p className="mt-3 text-xs uppercase tracking-[0.24em] text-steel">System Preview</p>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-ink/80">{item.prompt_injection}</p>
                      <p className="mt-3 text-xs text-steel">创建于 {formatDateTime(item.created_at)}</p>
                      </div>
                      <div className="flex items-center gap-3">
                        <Switch
                          checked={item.is_active}
                          onCheckedChange={async () => {
                            try {
                              await togglePreference(item.id);
                              await loadPreferences();
                            } catch (error) {
                              push({
                                title: "切换失败",
                                description: error instanceof Error ? error.message : "请稍后重试。",
                                tone: "error"
                              });
                            }
                          }}
                        />
                        <Button
                          variant="secondary"
                          onClick={() => {
                            setEditingId(item.id);
                            setForm(buildFormFromPreset(item));
                          }}
                        >
                          编辑
                        </Button>
                        <Button
                          variant="danger"
                          onClick={async () => {
                            if (!window.confirm(`确认删除预设 ${item.name} 吗？`)) {
                              return;
                            }
                            try {
                              await deletePreference(item.id);
                              push({ title: "预设已删除", tone: "success" });
                              await loadPreferences();
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
                );
              })
            ) : (
              <p className="text-sm text-steel">当前还没有任何偏好预设。</p>
            )}
          </div>
        </Card>

        <Card>
          <p className="text-sm font-semibold text-ink">解析建议</p>
          <div className="mt-4 space-y-4">
            {suggestions.length ? (
              suggestions.map((item, index) => {
                const suggestionSummaryLines = summarizeTempPreferences(item.structured_preferences);
                return (
                  <div key={`${item.name}-${index}`} className="rounded-[24px] bg-sand/60 p-4">
                  <div className="flex items-center gap-2">
                    <Badge>Suggestion</Badge>
                    <span className="font-semibold text-ink">{item.name}</span>
                  </div>
                  <p className="mt-3 text-sm text-steel">{item.description}</p>
                  <div className="mt-3 space-y-2 text-sm text-steel">
                    {suggestionSummaryLines.length ? (
                      suggestionSummaryLines.map((line) => <p key={line}>{line}</p>)
                    ) : (
                      <p>暂无结构化偏好摘要。</p>
                    )}
                  </div>
                  <p className="mt-3 text-xs uppercase tracking-[0.24em] text-steel">System Preview</p>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-ink/80">{item.prompt_injection}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {item.tags.map((tag) => (
                      <Badge key={tag} className="bg-white text-steel">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                  <Button className="mt-4" onClick={() => fillFromSuggestion(item)}>
                    填入表单
                  </Button>
                  </div>
                );
              })
            ) : (
              <p className="text-sm text-steel">解析完成后，建议会展示在这里，便于二次编辑后保存。</p>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
