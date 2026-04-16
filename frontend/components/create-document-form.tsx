"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/toast-provider";
import {
  createPlan,
  fetchKnowledgeFiles,
  uploadKnowledgeDocument,
  uploadKnowledgeImage
} from "@/lib/api";
import type { DocType, KnowledgeFile } from "@/lib/types";
import { cn } from "@/lib/utils";

type UploadFileType = "document" | "image";

export function CreateDocumentForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { push } = useToast();
  const [isPending, startTransition] = useTransition();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [docType, setDocType] = useState<DocType>("lesson");
  const [title, setTitle] = useState("");
  const [subject, setSubject] = useState("");
  const [grade, setGrade] = useState("");
  const [requirements, setRequirements] = useState("");
  const [courseContext, setCourseContext] = useState("");
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [selectedKnowledgeFileIds, setSelectedKnowledgeFileIds] = useState<string[]>([]);
  const [loadingKnowledgeFiles, setLoadingKnowledgeFiles] = useState(false);
  const [uploadType, setUploadType] = useState<UploadFileType>("document");
  const [selectedUploadFile, setSelectedUploadFile] = useState<File | null>(null);
  const [uploadDescription, setUploadDescription] = useState("");
  const [uploadingReference, setUploadingReference] = useState(false);
  const preselectedKnowledgeId = searchParams.get("knowledge") || "";

  const loadKnowledgeFiles = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoadingKnowledgeFiles(true);
    }
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
      if (!options?.silent) {
        setLoadingKnowledgeFiles(false);
      }
    }
  }, [push]);

  async function handleUploadReference() {
    if (!selectedUploadFile) {
      return;
    }

    setUploadingReference(true);
    try {
      const uploaded =
        uploadType === "image"
          ? await uploadKnowledgeImage(selectedUploadFile, uploadDescription.trim())
          : await uploadKnowledgeDocument(selectedUploadFile);
      setSelectedKnowledgeFileIds((current) => (current.includes(uploaded.id) ? current : [uploaded.id, ...current]));
      setSelectedUploadFile(null);
      setUploadDescription("");
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      push({
        title: "参考资料已入库",
        description: `${uploaded.filename} 已加入知识库，并自动勾选到当前新建流程。`,
        tone: "success"
      });
      await loadKnowledgeFiles({ silent: true });
    } catch (error) {
      push({
        title: "上传失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setUploadingReference(false);
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

  useEffect(() => {
    void loadKnowledgeFiles();
  }, [loadKnowledgeFiles]);

  useEffect(() => {
    if (!preselectedKnowledgeId || !knowledgeFiles.length) {
      return;
    }
    if (knowledgeFiles.some((item) => item.id === preselectedKnowledgeId)) {
      setSelectedKnowledgeFileIds((current) =>
        current.includes(preselectedKnowledgeId) ? current : [preselectedKnowledgeId, ...current]
      );
    }
  }, [knowledgeFiles, preselectedKnowledgeId]);

  const selectedReferences = useMemo(
    () => knowledgeFiles.filter((item) => selectedKnowledgeFileIds.includes(item.id)),
    [knowledgeFiles, selectedKnowledgeFileIds]
  );

  const showLessonReferences = docType === "lesson";

  return (
    <div className="space-y-6">
      <Card>
        <h1 className="mt-2 font-serif text-4xl text-ink">新建文档</h1>
      </Card>

      <Card>
        <form
          className="grid gap-4 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            startTransition(async () => {
              try {
                const plan = await createPlan({
                  title,
                  doc_type: docType,
                  subject: subject.trim() || undefined,
                  grade: grade.trim() || undefined,
                  requirements: docType === "lesson" ? requirements || undefined : undefined,
                  additional_files: docType === "lesson" ? selectedKnowledgeFileIds : undefined,
                  course_context: docType === "lesson" ? courseContext.trim() || undefined : undefined
                });
                push({
                  title: "创建成功",
                  description: `《${plan.title}》已创建，正在进入编辑器。`,
                  tone: "success"
                });
                router.push(`/documents/${plan.id}/editor?type=${plan.doc_type}`);
              } catch (error) {
                push({
                  title: "创建失败",
                  description: error instanceof Error ? error.message : "请稍后重试。",
                  tone: "error"
                });
              }
            });
          }}
        >
          <label className="block text-sm font-medium text-ink">
            文档类型
            <Select className="mt-2" value={docType} onChange={(event) => setDocType(event.target.value as DocType)}>
              <option value="lesson">教案</option>
              <option value="presentation">演示文稿</option>
            </Select>
          </label>

          <label className="block text-sm font-medium text-ink">
            标题
            <Input className="mt-2" value={title} onChange={(event) => setTitle(event.target.value)} required />
          </label>

          <label className="block text-sm font-medium text-ink">
            学科
            <Input
              className="mt-2"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="例如：数学、信息技术、校本课程"
            />
          </label>

          <label className="block text-sm font-medium text-ink">
            年级
            <Input
              className="mt-2"
              value={grade}
              onChange={(event) => setGrade(event.target.value)}
              placeholder="例如：高一、七年级、五年级"
            />
          </label>

          {docType === "lesson" ? (
            <>
              <label className="block text-sm font-medium text-ink md:col-span-2">
                教学要求
                <Textarea
                  className="mt-2"
                  placeholder="例如：围绕浮力设计 40 分钟课堂，导入更生活化，增加实验观察。"
                  value={requirements}
                  onChange={(event) => setRequirements(event.target.value)}
                />
              </label>

              <label className="block text-sm font-medium text-ink md:col-span-2">
                补充课程内容
                <Textarea
                  className="mt-2"
                  placeholder="可选填写真实素材、例题文本、课堂活动说明、希望强调的结论。"
                  value={courseContext}
                  onChange={(event) => setCourseContext(event.target.value)}
                />
              </label>
            </>
          ) : null}

          {showLessonReferences ? (
            <div className="md:col-span-2 space-y-4 rounded-[28px] border border-slate-200 bg-sand/35 p-5">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-steel">References</p>
                  <h2 className="mt-2 font-serif text-3xl text-ink">参考资料</h2>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-steel">
                    这里的资料会参与教案初稿生成。你也可以先上传到知识库，再直接勾选加入本次创建。
                  </p>
                </div>
                <Button variant="secondary" type="button" disabled={loadingKnowledgeFiles} onClick={() => void loadKnowledgeFiles()}>
                  {loadingKnowledgeFiles ? "加载中..." : "刷新资料"}
                </Button>
              </div>

              <div className="grid gap-4 xl:grid-cols-[0.92fr_1.08fr]">
                <div className="rounded-[24px] bg-white p-4 ring-1 ring-slate-200">
                  <p className="text-sm font-semibold text-ink">上传并加入本次创建</p>
                  <div className="mt-4 grid gap-4">
                    <label className="block text-sm font-medium text-ink">
                      上传类型
                      <Select className="mt-2" value={uploadType} onChange={(event) => setUploadType(event.target.value as UploadFileType)}>
                        <option value="document">文档</option>
                        <option value="image">图片</option>
                      </Select>
                    </label>

                    <label className="block text-sm font-medium text-ink">
                      选择文件
                      <Input
                        ref={fileInputRef}
                        className="mt-2"
                        type="file"
                        accept={uploadType === "image" ? ".png,.jpg,.jpeg,.webp,.gif,.bmp" : ".pdf,.docx,.md,.markdown"}
                        onChange={(event) => setSelectedUploadFile(event.target.files?.[0] || null)}
                      />
                    </label>

                    {uploadType === "image" ? (
                      <label className="block text-sm font-medium text-ink">
                        图片说明
                        <Textarea
                          className="mt-2 min-h-[108px]"
                          placeholder="例如：浮力实验装置照片，可辅助解释沉浮现象。"
                          value={uploadDescription}
                          onChange={(event) => setUploadDescription(event.target.value)}
                        />
                      </label>
                    ) : (
                      <div className="rounded-[20px] bg-sand/55 p-4 text-sm leading-6 text-steel">
                        文档会抽取正文进入知识库，本次新建教案会把勾选资料一起作为参考上下文。
                      </div>
                    )}

                    <Button
                      type="button"
                      disabled={uploadingReference || !selectedUploadFile || (uploadType === "image" && !uploadDescription.trim())}
                      onClick={() => void handleUploadReference()}
                    >
                      {uploadingReference ? "上传中..." : "上传并加入当前创建"}
                    </Button>
                  </div>
                </div>

                <div className="rounded-[24px] bg-white p-4 ring-1 ring-slate-200">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-ink">从知识库选择</p>
                    <Badge className="bg-sand text-steel">已选 {selectedKnowledgeFileIds.length}</Badge>
                  </div>

                  {selectedReferences.length ? (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {selectedReferences.map((file) => (
                        <Badge key={file.id} className="bg-[#183149] text-white">
                          {file.filename}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-4 text-sm text-steel">还没选任何资料，系统会只根据标题和教学要求生成。</p>
                  )}

                  <div className="mt-4 max-h-[320px] space-y-3 overflow-y-auto pr-1">
                    {loadingKnowledgeFiles ? (
                      <div className="rounded-[20px] bg-sand/45 p-4 text-sm text-steel">正在加载知识库文件…</div>
                    ) : knowledgeFiles.length ? (
                      knowledgeFiles.map((file) => {
                        const checked = selectedKnowledgeFileIds.includes(file.id);
                        const tags = Array.isArray(file.metadata.tags) ? file.metadata.tags.filter(Boolean).slice(0, 3) : [];

                        return (
                          <label
                            key={file.id}
                            className={cn(
                              "flex cursor-pointer items-start gap-3 rounded-[22px] border p-4 transition",
                              checked ? "border-ink bg-sand/70" : "border-slate-200 bg-white"
                            )}
                          >
                            <input
                              type="checkbox"
                              className="mt-1 h-4 w-4 accent-[#183149]"
                              checked={checked}
                              onChange={(event) => toggleKnowledgeFileSelection(file.id, event.target.checked)}
                            />
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge>{file.file_type}</Badge>
                                {file.metadata.doc_type ? <Badge className="bg-sand text-steel">{String(file.metadata.doc_type)}</Badge> : null}
                                <span className="truncate text-sm font-semibold text-ink">{file.filename}</span>
                              </div>
                              {file.description ? <p className="mt-2 text-sm leading-6 text-steel">{file.description}</p> : null}
                              {tags.length ? (
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {tags.map((tag) => (
                                    <Badge key={`${file.id}-${tag}`} className="bg-white text-steel">
                                      {tag}
                                    </Badge>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          </label>
                        );
                      })
                    ) : (
                      <div className="rounded-[20px] bg-sand/45 p-4 text-sm text-steel">
                        当前还没有知识库文件。可以先在左侧上传，再直接勾选。
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          <div className="md:col-span-2 flex flex-wrap gap-3 pt-2">
            <Button className="h-12 px-6" disabled={isPending}>
              {isPending ? "创建中..." : "创建并进入编辑器"}
            </Button>
            <Button variant="secondary" type="button" className="h-12 px-6" onClick={() => router.push("/documents")}>
              返回列表
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
