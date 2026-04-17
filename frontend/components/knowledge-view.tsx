"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useToast } from "@/components/toast-provider";
import { SafeImage } from "@/components/ui/safe-image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  answerWithKnowledge,
  deleteKnowledgeFile,
  fetchKnowledgeFiles,
  searchKnowledge,
  updateKnowledgeFile,
  uploadKnowledgeDocument,
  uploadKnowledgeImage
} from "@/lib/api";
import { buildKnowledgeImageUrl, resolveImageAssetUrl } from "@/lib/image-assets";
import type {
  KnowledgeAnswerResponse,
  KnowledgeFile,
  DocType,
  KnowledgeFileMetadata,
  KnowledgeSearchResult
} from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

type UploadFileType = "document" | "image";
type FileFilterType = "all" | UploadFileType;
type OriginFilterType = "all" | "manual" | "auto" | "snapshot";

const SEARCH_LIMIT_OPTIONS = [3, 5, 8, 10];

export function KnowledgeView() {
  const router = useRouter();
  const { push } = useToast();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadType, setUploadType] = useState<UploadFileType>("document");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [description, setDescription] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isRefreshingFiles, setIsRefreshingFiles] = useState(false);
  const [deletingFileId, setDeletingFileId] = useState<string | null>(null);
  const [savingFileId, setSavingFileId] = useState<string | null>(null);
  const [files, setFiles] = useState<KnowledgeFile[]>([]);
  const [fileQuery, setFileQuery] = useState("");
  const [fileFilterType, setFileFilterType] = useState<FileFilterType>("all");
  const [fileOriginFilter, setFileOriginFilter] = useState<OriginFilterType>("all");
  const [highlightedFileId, setHighlightedFileId] = useState<string | null>(null);
  const [previewingFileId, setPreviewingFileId] = useState<string | null>(null);
  const [managingFileId, setManagingFileId] = useState<string | null>(null);
  const [confirmingDeleteFileId, setConfirmingDeleteFileId] = useState<string | null>(null);
  const [draftFilename, setDraftFilename] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftTags, setDraftTags] = useState("");

  const [query, setQuery] = useState("");
  const [searchFileType, setSearchFileType] = useState<FileFilterType>("all");
  const [searchLimit, setSearchLimit] = useState(5);
  const [enableLlmRerank, setEnableLlmRerank] = useState(true);
  const [isSearching, setIsSearching] = useState(false);
  const [isAnswering, setIsAnswering] = useState(false);
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);
  const [answer, setAnswer] = useState<KnowledgeAnswerResponse | null>(null);
  const [hasRunQuery, setHasRunQuery] = useState(false);

  const totalFiles = files.length;
  const documentCount = files.filter((item) => item.file_type === "document").length;
  const imageCount = files.filter((item) => item.file_type === "image").length;
  const autoIngestedCount = files.filter((item) => isSystemGenerated(item.metadata)).length;
  const snapshotCount = files.filter((item) => isEditorSnapshot(item.metadata)).length;
  const pendingIndexCount = files.filter((item) => item.metadata.indexed === false).length;
  const previewingFile = files.find((item) => item.id === previewingFileId) ?? null;
  const managingFile = files.find((item) => item.id === managingFileId) ?? null;

  const visibleFiles = files.filter((item) => {
    if (fileFilterType !== "all" && item.file_type !== fileFilterType) {
      return false;
    }
    if (fileOriginFilter === "auto" && !isSystemGenerated(item.metadata)) {
      return false;
    }
    if (fileOriginFilter === "manual" && !isManualUpload(item.metadata)) {
      return false;
    }
    if (fileOriginFilter === "snapshot" && !isEditorSnapshot(item.metadata)) {
      return false;
    }
    const normalizedQuery = fileQuery.trim().toLowerCase();
    if (!normalizedQuery) {
      return true;
    }
    return [
      item.filename,
      item.description ?? "",
      getKnowledgeTags(item.metadata).join(" "),
      getSourceLabel(item.metadata.source),
      getTriggerLabel(item.metadata.trigger),
      getDocTypeLabel(item.metadata.doc_type),
      getLinkedPlanTitle(item),
      getSnapshotLabel(item.metadata)
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedQuery);
  });

  const loadFiles = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setIsRefreshingFiles(true);
    }
    try {
      const response = await fetchKnowledgeFiles();
      setFiles(response.items);
    } catch (error) {
      push({
        title: "知识库加载失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      if (!options?.silent) {
        setIsRefreshingFiles(false);
      }
    }
  }, [push]);

  async function runSearch() {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setResults([]);
      setAnswer(null);
      setHasRunQuery(false);
      return;
    }

    setIsSearching(true);
    setHasRunQuery(true);
    setAnswer(null);

    try {
      const response = await searchKnowledge({
        query: normalizedQuery,
        top_k: searchLimit,
        file_type: searchFileType === "all" ? undefined : searchFileType,
        enable_llm_rerank: enableLlmRerank
      });
      setResults(response);
    } catch (error) {
      setResults([]);
      push({
        title: "知识搜索失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setIsSearching(false);
    }
  }

  async function runAnswer() {
    const normalizedQuery = query.trim();
    if (!normalizedQuery) {
      setResults([]);
      setAnswer(null);
      setHasRunQuery(false);
      return;
    }

    setIsAnswering(true);
    setHasRunQuery(true);

    try {
      const response = await answerWithKnowledge({
        query: normalizedQuery,
        top_k: searchLimit,
        file_type: searchFileType === "all" ? undefined : searchFileType,
        enable_llm_rerank: enableLlmRerank
      });
      setAnswer(response);
      setResults(response.results);
    } catch (error) {
      setAnswer(null);
      setResults([]);
      push({
        title: "知识问答失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setIsAnswering(false);
    }
  }

  async function handleUpload() {
    if (!selectedFile) {
      return;
    }

    setIsUploading(true);
    try {
      if (uploadType === "image") {
        await uploadKnowledgeImage(selectedFile, description.trim());
      } else {
        await uploadKnowledgeDocument(selectedFile);
      }
      setSelectedFile(null);
      setDescription("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      push({
        title: "上传成功",
        description: "知识库文件已写入并完成可检索处理。",
        tone: "success"
      });
      await loadFiles();
    } catch (error) {
      push({
        title: "上传失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setIsUploading(false);
    }
  }

  async function handleDelete(file: KnowledgeFile) {
    setDeletingFileId(file.id);
    try {
      await deleteKnowledgeFile(file.id);
      push({
        title: "文件已删除",
        description: `${file.filename} 已移出知识库。`,
        tone: "success"
      });
      setResults((current) => current.filter((item) => item.file_id !== file.id));
      setAnswer((current) =>
        current
          ? {
            ...current,
            citations: current.citations.filter((item) => item.file_id !== file.id),
            results: current.results.filter((item) => item.file_id !== file.id)
          }
          : null
      );
      if (highlightedFileId === file.id) {
        setHighlightedFileId(null);
      }
      if (previewingFileId === file.id) {
        setPreviewingFileId(null);
      }
      if (managingFileId === file.id) {
        closeFileManager();
      }
      await loadFiles();
    } catch (error) {
      push({
        title: "删除失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setDeletingFileId(null);
    }
  }

  function openImagePreview(file: KnowledgeFile) {
    setPreviewingFileId(file.id);
  }

  function closeImagePreview() {
    setPreviewingFileId(null);
  }

  function openFileManager(file: KnowledgeFile) {
    setManagingFileId(file.id);
    setConfirmingDeleteFileId(null);
    setDraftFilename(file.filename);
    setDraftDescription(file.description ?? "");
    setDraftTags(getKnowledgeTags(file.metadata).join(", "));
  }

  function closeFileManager() {
    setManagingFileId(null);
    setConfirmingDeleteFileId(null);
    setDraftFilename("");
    setDraftDescription("");
    setDraftTags("");
  }

  async function handleSaveFile(file: KnowledgeFile) {
    setSavingFileId(file.id);
    try {
      const updated = await updateKnowledgeFile(file.id, {
        filename: draftFilename.trim(),
        description: draftDescription.trim() || null,
        tags: parseTagInput(draftTags)
      });
      setFiles((current) => current.map((item) => (item.id === file.id ? updated : item)));
      closeFileManager();
      push({
        title: "文件信息已更新",
        description: `${updated.filename} 的名称、说明和标签已保存。`,
        tone: "success"
      });
    } catch (error) {
      push({
        title: "更新失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setSavingFileId(null);
    }
  }

  function requestDelete(file: KnowledgeFile) {
    setConfirmingDeleteFileId((current) => (current === file.id ? null : file.id));
  }

  function locateFile(fileId: string, fileType: string) {
    setHighlightedFileId(fileId);
    if (fileType === "document" || fileType === "image") {
      setFileFilterType(fileType);
    }
    window.setTimeout(() => {
      document.getElementById(`knowledge-file-${fileId}`)?.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
    }, 80);
  }

  function clearQueryState() {
    setQuery("");
    setResults([]);
    setAnswer(null);
    setHasRunQuery(false);
  }

  useEffect(() => {
    void loadFiles();
  }, [loadFiles]);

  const layoutCardClass = "flex h-full min-h-0 flex-col overflow-hidden p-4 md:p-5";
  const sectionTitleClass = "font-serif text-[1.72rem] leading-tight text-ink md:text-[1.95rem]";
  const controlClass = "h-10 text-[13px]";
  const scrollAreaClass = "app-scroll h-0 min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1";
  const scrollAreaStyle = { scrollbarGutter: "stable" as const };
  const dashboardGridClass =
    "grid gap-5 xl:h-[calc(140vh-0.1rem)] xl:grid-cols-2 xl:grid-rows-[minmax(19.25rem,_0.7fr)_minmax(0,_7fr)]";

  return (
    <div className="space-y-5">
      <div className={dashboardGridClass}>
        <Card
          className={cn(
            layoutCardClass,
            "bg-[radial-gradient(circle_at_top_left,_rgba(35,112,116,0.14),_transparent_42%),linear-gradient(135deg,_rgba(247,239,224,0.72),_rgba(255,255,255,0.94))]"
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className={sectionTitleClass}>知识库管理</h1>
              <p className="mt-2 text-[13px] leading-6 text-steel">
                汇总当前知识库沉淀情况，保持资料规模、来源和索引状态一眼可见。
              </p>
            </div>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="资料总数" value={String(totalFiles)} hint="" />
            <StatTile label="系统沉淀" value={String(autoIngestedCount)} hint="" />
            <StatTile label="文档 / 图片" value={`${documentCount} / ${imageCount}`} hint="" />
            <StatTile
              label="编辑器快照"
              value={`${snapshotCount}${pendingIndexCount ? ` / 待索引 ${pendingIndexCount}` : ""}`}
              hint=""
            />
          </div>
        </Card>

        <Card className={layoutCardClass}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className={sectionTitleClass}>新增文件</h2>
            </div>
          </div>

          <div className="mt-4 grid flex-1 content-start gap-3">
            <label className="block text-[13px] font-medium text-ink">
              上传类型
              <Select
                className={cn("mt-2", controlClass)}
                value={uploadType}
                onChange={(event) => setUploadType(event.target.value as UploadFileType)}
              >
                <option value="document">文档</option>
                <option value="image">图片</option>
              </Select>
            </label>

            <label className="block text-[13px] font-medium text-ink">
              选择文件
              <Input
                ref={fileInputRef}
                className={cn("mt-2", controlClass)}
                type="file"
                accept={uploadType === "image" ? ".png,.jpg,.jpeg,.webp,.gif,.bmp" : ".pdf,.docx,.md,.markdown"}
                onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                required
              />
            </label>

            {uploadType === "image" ? (
              <label className="block text-[13px] font-medium text-ink">
                图片描述
                <Textarea
                  className="mt-2 min-h-[88px] rounded-[22px] px-4 py-3 text-[13px]"
                  placeholder="例如：浮力实验装置照片，可辅助解释沉浮现象。"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
            ) : (
              <div >

              </div>
            )}

            <Button
              className="mt-1 h-10 text-sm"
              disabled={isUploading || !selectedFile || (uploadType === "image" && !description.trim())}
              onClick={() => void handleUpload()}
            >
              {isUploading ? "上传中..." : "上传并入库"}
            </Button>
          </div>
        </Card>

        <Card className={cn(layoutCardClass, "min-w-0 xl:max-h-full xl:min-h-0")}>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h2 className={sectionTitleClass}>知识检索</h2>
              <p className="mt-2 text-[13px] leading-6 text-steel">
                先做混合检索和文件聚合；如果你点“基于资料回答”，会在检索结果之上再读取命中资料内容，生成自然语言回答。
              </p>
            </div>
            <div className="rounded-[22px] border border-slate-200 bg-white px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.18em] text-steel">LLM 整理</p>
              <div className="mt-2 flex items-center gap-3">
                <Switch
                  checked={enableLlmRerank}
                  onCheckedChange={setEnableLlmRerank}
                  disabled={isSearching || isAnswering}
                />
                <span className="text-[13px] text-ink">{enableLlmRerank ? "已开启" : "已关闭"}</span>
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_repeat(2,minmax(0,0.58fr))]">
            <Input
              className={controlClass}
              placeholder="输入问题，例如：给我一份 LangChain Agent 入门示例"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <Select
              className={controlClass}
              value={searchFileType}
              onChange={(event) => setSearchFileType(event.target.value as FileFilterType)}
            >
              <option value="all">全部类型</option>
              <option value="document">仅文档</option>
              <option value="image">仅图片</option>
            </Select>
            <Select
              className={controlClass}
              value={String(searchLimit)}
              onChange={(event) => setSearchLimit(Number(event.target.value))}
            >
              {SEARCH_LIMIT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  返回 {option} 条
                </option>
              ))}
            </Select>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2.5">
            <Button
              className="h-10 px-4 text-[13px]"
              disabled={isSearching || isAnswering || !query.trim()}
              onClick={() => void runSearch()}
            >
              {isSearching ? "搜索中..." : "搜索结果"}
            </Button>
            <Button
              variant="secondary"
              className="h-10 px-4 text-[13px]"
              disabled={isSearching || isAnswering || !query.trim()}
              onClick={() => void runAnswer()}
            >
              {isAnswering ? "整理回答中..." : "基于资料回答"}
            </Button>
            {hasRunQuery ? (
              <Button variant="ghost" className="h-10 px-4 text-[13px]" onClick={clearQueryState}>
                清空结果
              </Button>
            ) : null}
            <p className="text-[11px] text-steel">
              当前策略：{enableLlmRerank ? "混合检索 + LLM 结果整理" : "混合检索"}
            </p>
          </div>

          <div className="mt-4 flex min-h-0 flex-1 flex-col overflow-hidden">
            {answer ? (
              <div className="rounded-[24px] border border-lagoon/20 bg-[linear-gradient(135deg,_rgba(35,112,116,0.08),_rgba(255,255,255,0.92))] p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className="bg-white">知识回答</Badge>
                  <Badge className="bg-white">{answer.used_llm ? "LLM 已参与" : "规则回退"}</Badge>
                  <span className="text-[12px] text-steel">基于命中文件内容生成，不是只看标题和摘要。</span>
                </div>
                <p className="mt-3 whitespace-pre-wrap text-[13px] leading-6 text-ink">{answer.answer}</p>
                {answer.citations.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {answer.citations.map((citation) => (
                      <Button
                        key={citation.file_id}
                        variant="ghost"
                        className="rounded-full bg-white/80 px-3 py-2 text-left text-[12px]"
                        onClick={() => locateFile(citation.file_id, citation.file_type)}
                      >
                        依据：{citation.filename}
                      </Button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className={cn(scrollAreaClass, answer ? "mt-4" : "mt-1")} style={scrollAreaStyle}>
              <div className="space-y-3 pb-1">
                {isSearching || isAnswering ? (
                  <SearchStatusCard
                    title={isAnswering ? "正在整理回答" : "正在检索资料"}
                    description={isAnswering ? "正在读取命中文档并组织自然语言回答..." : "正在整理最相关的知识资料..."}
                  />
                ) : results.length ? (
                  results.map((item, index) => (
                    <div
                      key={item.file_id}
                      className="rounded-[24px] border border-slate-200 bg-sand/60 p-4 shadow-soft"
                    >
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge>{item.file_type}</Badge>
                            {item.doc_type ? <Badge className="bg-white">{getDocTypeLabel(item.doc_type)}</Badge> : null}
                            {item.source ? <Badge className="bg-white">{getSourceLabel(item.source)}</Badge> : null}
                            <span className="truncate text-[13px] font-semibold text-ink">
                              #{index + 1} {item.filename}
                            </span>
                          </div>
                          {item.summary ? <p className="mt-3 text-[13px] font-medium leading-6 text-ink">{item.summary}</p> : null}
                          {item.match_reason ? <p className="mt-2 text-[13px] leading-6 text-steel">{item.match_reason}</p> : null}
                        </div>
                        <Button
                          variant="secondary"
                          className="h-10 shrink-0 px-4 text-[13px]"
                          onClick={() => locateFile(item.file_id, item.file_type)}
                        >
                          在列表中定位
                        </Button>
                      </div>

                      <div className="mt-4 rounded-[20px] bg-white/80 p-4">
                        <p className="text-[11px] uppercase tracking-[0.16em] text-steel">主要片段</p>
                        <p className="mt-2 text-[13px] leading-6 text-steel">{item.text_snippet}</p>
                        {item.matched_snippets && item.matched_snippets.length > 1 ? (
                          <div className="mt-4 space-y-2">
                            <p className="text-[11px] uppercase tracking-[0.16em] text-steel">补充证据</p>
                            {item.matched_snippets.slice(1).map((snippet) => (
                              <p key={`${item.file_id}-${snippet.slice(0, 24)}`} className="text-[12px] leading-5 text-steel/90">
                                {snippet}
                              </p>
                            ))}
                          </div>
                        ) : null}
                      </div>

                      <div className="mt-4 flex flex-wrap gap-3 text-[11px] text-steel">
                        <span>相关度：{item.relevance_score.toFixed(3)}</span>
                        {item.search_strategy ? <span>检索方式：{item.search_strategy}</span> : null}
                        {item.trigger ? <span>入库触发：{getTriggerLabel(item.trigger)}</span> : null}
                      </div>
                    </div>
                  ))
                ) : hasRunQuery ? (
                  <SearchStatusCard title="暂无命中结果" description="可以换个关键词、调整类型筛选，或先把相关资料放进知识库。" />
                ) : (
                  <SearchStatusCard title="等待查询" description="" />
                )}
              </div>
            </div>
          </div>
        </Card>

        <Card className={cn(layoutCardClass, "xl:max-h-full xl:min-h-0")}>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h2 className={sectionTitleClass}>资料列表</h2>
            </div>
            <Button
              variant="secondary"
              className="h-10 px-4 text-[13px]"
              disabled={isRefreshingFiles}
              onClick={() => void loadFiles()}
            >
              {isRefreshingFiles ? "刷新中..." : "刷新列表"}
            </Button>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_repeat(2,minmax(0,0.7fr))]">
            <Input
              className={controlClass}
              placeholder="按文件名、标签、来源、触发方式筛选"
              value={fileQuery}
              onChange={(event) => setFileQuery(event.target.value)}
            />
            <Select
              className={controlClass}
              value={fileFilterType}
              onChange={(event) => setFileFilterType(event.target.value as FileFilterType)}
            >
              <option value="all">全部类型</option>
              <option value="document">仅文档</option>
              <option value="image">仅图片</option>
            </Select>
            <Select
              className={controlClass}
              value={fileOriginFilter}
              onChange={(event) => setFileOriginFilter(event.target.value as OriginFilterType)}
            >
              <option value="all">全部来源</option>
              <option value="manual">手动上传</option>
              <option value="auto">系统沉淀</option>
              <option value="snapshot">编辑器快照</option>
            </Select>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge className="bg-white">全部 {totalFiles}</Badge>
            <Badge className="bg-white">文档 {documentCount}</Badge>
            <Badge className="bg-white">图片 {imageCount}</Badge>
            <Badge className="bg-white">系统沉淀 {autoIngestedCount}</Badge>
            <Badge className="bg-white">编辑器快照 {snapshotCount}</Badge>
          </div>

          <div className="mt-4 flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className={scrollAreaClass} style={scrollAreaStyle}>
              <div className="space-y-3 pb-1">
                {visibleFiles.length ? (
                  visibleFiles.map((item) => {
                    const isHighlighted = highlightedFileId === item.id;
                    const sourceLabel = getSourceLabel(item.metadata.source);
                    const triggerLabel = getTriggerLabel(item.metadata.trigger);
                    const docTypeLabel = getDocTypeLabel(item.metadata.doc_type);
                    const snapshotLabel = getSnapshotLabel(item.metadata);
                    const tags = getKnowledgeTags(item.metadata);
                    const linkedPlan = getLinkedEditorTarget(item);
                    const linkedPlanTitle = getLinkedPlanTitle(item);
                    const indexedLabel =
                      item.metadata.indexed === false ? "仅关键词检索" : item.file_type === "image" ? "图片索引已启用" : "文档索引已启用";

                    return (
                      <div
                        id={`knowledge-file-${item.id}`}
                        key={item.id}
                        className={cn(
                          "flex flex-col gap-3 rounded-[24px] border border-slate-200 bg-white p-4 transition md:flex-row md:items-center md:justify-between",
                          isHighlighted && "border-lagoon bg-lagoon/5 shadow-soft"
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge>{item.file_type}</Badge>
                            {docTypeLabel ? <Badge className="bg-white">{docTypeLabel}</Badge> : null}
                            {sourceLabel ? <Badge className="bg-white">{sourceLabel}</Badge> : null}
                            {snapshotLabel ? <Badge className="bg-amber-100 text-amber-900">{snapshotLabel}</Badge> : null}
                            {tags.map((tag) => (
                              <Badge key={`${item.id}-${tag}`} className="bg-sand text-steel">
                                {tag}
                              </Badge>
                            ))}
                            <span className="truncate text-[13px] font-semibold text-ink">{item.filename}</span>
                          </div>
                          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-steel">
                            <span>上传时间：{formatDateTime(item.created_at)}</span>
                            <span>{indexedLabel}</span>
                            {triggerLabel ? <span>触发方式：{triggerLabel}</span> : null}
                            {linkedPlanTitle ? <span>关联文档：{linkedPlanTitle}</span> : null}
                          </div>
                          {item.description ? (
                            <p className="mt-2 text-[13px] leading-6 text-steel">{item.description}</p>
                          ) : (
                            <p className="mt-2 text-[13px] leading-6 text-steel">当前还没有文件说明。</p>
                          )}
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-2 md:max-w-[18rem] md:justify-end">
                          {linkedPlan ? (
                            <Button
                              variant="secondary"
                              className="h-10 px-4 text-[13px]"
                              onClick={() => router.push(`/documents/${linkedPlan.planId}/editor?type=${linkedPlan.docType}`)}
                            >
                              打开编辑器
                            </Button>
                          ) : item.file_type === "document" ? (
                            <Button
                              variant="secondary"
                              className="h-10 px-4 text-[13px]"
                              onClick={() => router.push(`/documents/create?knowledge=${item.id}`)}
                            >
                              用于新建文档
                            </Button>
                          ) : null}
                          {item.file_type === "image" ? (
                            <Button variant="secondary" className="h-10 px-4 text-[13px]" onClick={() => openImagePreview(item)}>
                              预览
                            </Button>
                          ) : null}
                          {isHighlighted ? (
                            <Button variant="ghost" className="h-10 px-4 text-[13px]" onClick={() => setHighlightedFileId(null)}>
                              取消定位
                            </Button>
                          ) : null}
                          <Button variant="ghost" className="h-10 px-4 text-[13px]" onClick={() => openFileManager(item)}>
                            管理文件
                          </Button>
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <SearchStatusCard
                    title={files.length ? "当前筛选下没有资料" : "还没有任何知识资料"}
                    description={files.length ? "可以放宽类型或来源筛选。" : "上传文档、图片，或先生成教案 / PPT 初稿来自动沉淀资料。"}
                  />
                )}
              </div>
            </div>
          </div>
        </Card>
      </div>

      <Modal
        open={Boolean(previewingFile)}
        title={previewingFile?.filename ?? "图片预览"}
        description="这里直接预览知识库中的图片资源，不需要先下载到本地。"
        onClose={closeImagePreview}
        className="max-w-4xl"
        bodyClassName="max-h-[calc(100vh-10rem)]"
      >
        {previewingFile ? (
          <div className="space-y-4">
            <div className="overflow-hidden rounded-[28px] bg-[#eef3f8] p-4">
              <div className="flex min-h-[280px] items-center justify-center overflow-hidden rounded-[24px] bg-slate-100">
                <SafeImage
                  src={resolveImageAssetUrl(buildKnowledgeImageUrl(previewingFile))}
                  alt={previewingFile.filename}
                  width={1600}
                  height={1200}
                  sizes="90vw"
                  className="max-h-[70vh] w-auto max-w-full rounded-[20px] object-contain"
                />
              </div>
            </div>
            <div className="grid gap-3 rounded-[24px] bg-sand/45 p-4 md:grid-cols-2">
              <div>
                <p className="text-xs uppercase tracking-[0.16em] text-steel">图片说明</p>
                <p className="mt-2 text-sm leading-6 text-ink">
                  {previewingFile.description?.trim() || "当前还没有图片说明。"}
                </p>
              </div>
              <div className="space-y-2 text-sm text-steel">
                <p>上传时间：{formatDateTime(previewingFile.created_at)}</p>
                <p>分辨率：{getImageDimensions(previewingFile.metadata)}</p>
                <p>文件大小：{formatFileSize(previewingFile.metadata.size_bytes)}</p>
              </div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal
        open={Boolean(managingFile)}
        title={managingFile ? `管理文件 · ${managingFile.filename}` : "管理文件"}
        description="统一编辑文件名、说明和标签；删除操作也收在这里，避免拉长列表。"
        onClose={() => {
          if (!savingFileId && !deletingFileId) {
            closeFileManager();
          }
        }}
      >
        {managingFile ? (
          <div className="space-y-4">
            <div className="rounded-[24px] bg-sand/50 p-4 text-sm leading-6 text-steel">
              <p>文件类型：{managingFile.file_type === "image" ? "图片" : "文档"}</p>
              <p className="mt-1">上传时间：{formatDateTime(managingFile.created_at)}</p>
              {getLinkedPlanTitle(managingFile) ? <p className="mt-1">关联文档：{getLinkedPlanTitle(managingFile)}</p> : null}
            </div>

            <label className="block text-sm font-medium text-ink">
              文件名
              <Input className="mt-2" value={draftFilename} onChange={(event) => setDraftFilename(event.target.value)} />
            </label>

            <label className="block text-sm font-medium text-ink">
              文件说明
              <Textarea
                className="mt-2 min-h-[120px]"
                value={draftDescription}
                onChange={(event) => setDraftDescription(event.target.value)}
              />
            </label>

            <label className="block text-sm font-medium text-ink">
              文件标签
              <Input
                className="mt-2"
                placeholder="例如：回退点, 初稿, 浮力"
                value={draftTags}
                onChange={(event) => setDraftTags(event.target.value)}
              />
            </label>

            <div className="rounded-[24px] border border-rose-200 bg-rose-50/80 p-4">
              <p className="text-sm font-semibold text-rose-900">危险操作</p>
              <p className="mt-2 text-sm leading-6 text-rose-800">
                删除后会同时移除文件与索引内容，搜索结果、知识回答和关联引用都将不再命中。
              </p>
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                {confirmingDeleteFileId === managingFile.id ? (
                  <p className="text-sm font-semibold text-rose-900">再次点击“确认删除”后立即执行，且不可恢复。</p>
                ) : (
                  <p className="text-sm text-rose-800">如果只是改标题、标签或说明，直接保存即可。</p>
                )}
                <Button
                  variant="danger"
                  disabled={savingFileId === managingFile.id || deletingFileId === managingFile.id}
                  onClick={() =>
                    confirmingDeleteFileId === managingFile.id ? void handleDelete(managingFile) : requestDelete(managingFile)
                  }
                >
                  {deletingFileId === managingFile.id
                    ? "删除中..."
                    : confirmingDeleteFileId === managingFile.id
                      ? "确认删除"
                      : "删除文件"}
                </Button>
              </div>
            </div>

            <div className="flex flex-wrap justify-end gap-3">
              <Button
                variant="secondary"
                disabled={savingFileId === managingFile.id || deletingFileId === managingFile.id}
                onClick={closeFileManager}
              >
                取消
              </Button>
              <Button
                disabled={savingFileId === managingFile.id || deletingFileId === managingFile.id}
                onClick={() => void handleSaveFile(managingFile)}
              >
                {savingFileId === managingFile.id ? "保存中..." : "保存信息"}
              </Button>
            </div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}

function StatTile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="flex min-h-[5.4rem] flex-col justify-between rounded-[22px] border border-white/70 bg-white/82 p-3.5">
      <p className="text-[10px] uppercase tracking-[0.14em] text-steel">{label}</p>
      <p className="mt-1 font-serif text-[1.45rem] leading-tight text-ink sm:text-[1.6rem]">{value}</p>
      {hint ? <p className="mt-1 text-[12px] leading-5 text-steel">{hint}</p> : null}
    </div>
  );
}

function SearchStatusCard({ title, description }: { title: string; description: string }) {
  return (
    <div className="rounded-[24px] border border-dashed border-slate-300 bg-white/70 p-4">
      <p className="text-[13px] font-semibold text-ink">{title}</p>
      <p className="mt-2 text-[13px] leading-6 text-steel">{description}</p>
    </div>
  );
}

function isSystemGenerated(metadata: KnowledgeFileMetadata | undefined) {
  return metadata?.source === "plan_auto_ingest";
}

function isEditorSnapshot(metadata: KnowledgeFileMetadata | undefined) {
  return metadata?.source === "editor_snapshot";
}

function isManualUpload(metadata: KnowledgeFileMetadata | undefined) {
  return !metadata?.source || metadata.source === "manual_upload";
}

function parseTagInput(value: string) {
  return value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function getKnowledgeTags(metadata: KnowledgeFileMetadata | undefined) {
  return Array.isArray(metadata?.tags)
    ? metadata.tags.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
}

function getSnapshotLabel(metadata: KnowledgeFileMetadata | undefined) {
  return typeof metadata?.savepoint_label === "string" && metadata.savepoint_label.trim() ? metadata.savepoint_label.trim() : "";
}

function getLinkedPlanTitle(file: KnowledgeFile) {
  const title = file.metadata.plan_title;
  return typeof title === "string" ? title.trim() : "";
}

function getLinkedEditorTarget(file: KnowledgeFile): { planId: string; docType: DocType } | null {
  const planId =
    (typeof file.metadata.plan_id === "string" && file.metadata.plan_id.trim()) ||
    (typeof file.metadata.source_plan_id === "string" && file.metadata.source_plan_id.trim()) ||
    "";
  const docType = file.metadata.doc_type;
  if (!planId || (docType !== "lesson" && docType !== "presentation")) {
    return null;
  }
  return {
    planId,
    docType
  };
}

function getSourceLabel(source?: string | null) {
  if (source === "plan_auto_ingest") {
    return "系统沉淀";
  }
  if (source === "editor_snapshot") {
    return "编辑器快照";
  }
  if (source === "manual_upload") {
    return "手动上传";
  }
  if (!source) {
    return "手动上传";
  }
  return source;
}

function getTriggerLabel(trigger?: string | null) {
  switch (trigger) {
    case "create":
      return "教案创建";
    case "export":
      return "教案导出";
    case "savepoint":
      return "旧版回退点入库";
    case "save_to_knowledge":
      return "保存进知识库";
    case "presentation_create":
      return "PPT 创建";
    case "generate_presentation":
      return "生成 PPT 初稿";
    case "presentation_export":
      return "导出 PPT";
    case "manual_upload":
      return "手动上传";
    default:
      return trigger ?? "";
  }
}

function getDocTypeLabel(docType?: string | null) {
  switch (docType) {
    case "lesson":
      return "教案";
    case "presentation":
      return "PPT 初稿";
    default:
      return docType ?? "";
  }
}

function getImageDimensions(metadata: KnowledgeFileMetadata | undefined) {
  const width = typeof metadata?.width === "number" ? metadata.width : null;
  const height = typeof metadata?.height === "number" ? metadata.height : null;
  return width && height ? `${width} × ${height}` : "未记录";
}

function formatFileSize(sizeBytes: unknown) {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes) || sizeBytes <= 0) {
    return "未记录";
  }
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}
