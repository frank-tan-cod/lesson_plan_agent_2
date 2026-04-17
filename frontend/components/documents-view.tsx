"use client";

import Link from "next/link";
import { useCallback, useDeferredValue, useEffect, useState } from "react";

import { useToast } from "@/components/toast-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { deletePlan, fetchPlans } from "@/lib/api";
import type { DocType, Plan } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

const docTypeOptions: Array<{ value: DocType; label: string }> = [
  { value: "lesson", label: "教案" },
  { value: "presentation", label: "演示文稿" }
];

interface DocumentsViewProps {
  initialDocType: DocType;
}

export function DocumentsView({ initialDocType }: DocumentsViewProps) {
  const { push } = useToast();
  const [docType, setDocType] = useState<DocType>(initialDocType);
  const [subject, setSubject] = useState("");
  const [grade, setGrade] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Plan[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  const deferredQuery = useDeferredValue(query);

  const loadPlans = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchPlans({
        docType,
        subject: subject.trim(),
        grade: grade.trim(),
        query: deferredQuery
      });
      setItems(result.items);
      setTotal(result.total);
    } catch (error) {
      push({
        title: "加载文档失败",
        description: error instanceof Error ? error.message : "请稍后重试。",
        tone: "error"
      });
    } finally {
      setLoading(false);
    }
  }, [deferredQuery, docType, grade, push, subject]);

  useEffect(() => {
    void loadPlans();
  }, [loadPlans]);

  useEffect(() => {
    setDocType(initialDocType);
  }, [initialDocType]);

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="mt-2 font-serif text-4xl text-ink">文档管理</h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-steel">
              支持您在同一套编辑框架下，制作教案、演示文稿
            </p>
          </div>
          <Link href="/documents/create">
            <Button className="h-12 px-6">新建文档</Button>
          </Link>
        </div>

        <div className="mt-6 flex flex-wrap gap-3">
          {docTypeOptions.map((option) => {
            const active = docType === option.value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => setDocType(option.value)}
                className={`rounded-full px-4 py-2 text-sm font-semibold transition ${active ? "bg-ink text-white" : "bg-sand text-steel hover:bg-slate-200"
                  }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Input placeholder="搜索标题关键词" value={query} onChange={(event) => setQuery(event.target.value)} />
          <Input
            placeholder="按学科筛选，例如：数学 / 信息技术"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
          />
          <Input
            placeholder="按年级筛选，例如：高一 / 七年级"
            value={grade}
            onChange={(event) => setGrade(event.target.value)}
          />
          <Button variant="secondary" className="h-11" onClick={() => void loadPlans()}>
            刷新列表
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {loading ? (
          Array.from({ length: 4 }).map((_, index) => (
            <Card key={index} className="animate-pulse">
              <div className="h-6 w-2/3 rounded-full bg-slate-200" />
              <div className="mt-4 h-4 w-full rounded-full bg-slate-100" />
              <div className="mt-2 h-4 w-4/5 rounded-full bg-slate-100" />
            </Card>
          ))
        ) : items.length ? (
          items.map((item) => (
            <Card key={item.id} className="group transition hover:-translate-y-0.5 hover:shadow-panel">
              <div className="flex items-start gap-4">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge>{item.doc_type === "lesson" ? "lesson" : "presentation"}</Badge>
                    {item.subject ? <Badge className="bg-lagoon/10 text-lagoon">{item.subject}</Badge> : null}
                    {item.grade ? <Badge className="bg-amber-100 text-amber-900">{item.grade}</Badge> : null}
                  </div>
                  <h2 className="mt-4 font-serif text-3xl text-ink">{item.title}</h2>
                  <p className="mt-2 text-sm text-steel">最后更新：{formatDateTime(item.updated_at)}</p>
                </div>
              </div>

              <div className="mt-8 flex flex-wrap gap-3">
                <Link href={`/documents/${item.id}/editor?type=${item.doc_type}`}>
                  <Button>进入编辑器</Button>
                </Link>
                <Button
                  variant="secondary"
                  onClick={async () => {
                    if (!window.confirm(`确认删除《${item.title}》吗？`)) {
                      return;
                    }
                    try {
                      await deletePlan(item.id);
                      push({
                        title: "文档已删除",
                        description: `《${item.title}》已从列表移除。`,
                        tone: "success"
                      });
                      await loadPlans();
                    } catch (error) {
                      push({
                        title: "删除失败",
                        description: error instanceof Error ? error.message : "请稍后重试。",
                        tone: "error"
                      });
                    }
                  }}
                  className="group-hover:border-slate-300"
                >
                  删除
                </Button>
              </div>
            </Card>
          ))
        ) : (
          <Card className="xl:col-span-2">
            <p className="text-xs uppercase tracking-[0.3em] text-steel">Empty State</p>
            <h2 className="mt-2 font-serif text-3xl text-ink">还没有匹配的文档</h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-steel">
              当前筛选条件下没有找到结果。可以尝试清空搜索、切换文档类型，或者先创建一个新的项目。
            </p>
          </Card>
        )}
      </div>

      <Card className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-ink">当前共 {total} 条记录</p>
        </div>
      </Card>
    </div>
  );
}
