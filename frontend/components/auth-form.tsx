"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

import { useAuth } from "@/components/auth-provider";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

interface AuthFormProps {
  mode: "login" | "register";
}

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login, register, status } = useAuth();
  const { push } = useToast();
  const [isPending, startTransition] = useTransition();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (status === "authenticated") {
      router.replace(searchParams.get("redirect") || "/documents");
    }
  }, [router, searchParams, status]);

  const isLogin = mode === "login";

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="grid w-full max-w-6xl gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="grain overflow-hidden rounded-[36px] bg-ink px-8 py-10 text-white shadow-panel">
          <p className="text-xs uppercase tracking-[0.34em] text-white/60">AI Teaching Workspace</p>
          <h1 className="mt-5 max-w-xl font-serif text-5xl leading-tight">
            教研智能体，
            <br />
            个性化才是真正的高效
          </h1>
          <div className="section-divider mt-8 h-px w-full" />
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {[
              "JWT 登录与多用户隔离",
              "教案 / PPT 双文档工作流",
              "SSE 流式编辑与追问确认"
            ].map((item) => (
              <div key={item} className="rounded-[28px] bg-white/8 p-4 text-sm text-white/80">
                {item}
              </div>
            ))}
          </div>
        </section>

        <Card className="border-white/90 bg-white/92 p-8">
          <p className="text-xs uppercase tracking-[0.28em] text-steel">
            {isLogin ? "欢迎回来" : "创建账号"}
          </p>
          <h2 className="mt-3 font-serif text-4xl text-ink">{isLogin ? "登录工作台" : "注册新账户"}</h2>
          <p className="mt-3 text-sm leading-6 text-steel">
            {isLogin
              ? "登录后即可进入文档面板、知识库和偏好设置。"
              : "注册完成后会自动登录，直接进入文档工作台。"}
          </p>

          <form
            className="mt-8 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              startTransition(async () => {
                try {
                  if (isLogin) {
                    await login(username, password);
                    push({ title: "登录成功", description: "正在进入文档工作台。", tone: "success" });
                  } else {
                    await register(username, password);
                    push({ title: "注册成功", description: "账号已创建并完成登录。", tone: "success" });
                  }
                  router.replace(searchParams.get("redirect") || "/documents");
                } catch (error) {
                  push({
                    title: isLogin ? "登录失败" : "注册失败",
                    description: error instanceof Error ? error.message : "请稍后重试。",
                    tone: "error"
                  });
                }
              });
            }}
          >
            <label className="block text-sm font-medium text-ink">
              用户名
              <Input
                className="mt-2"
                placeholder="例如 teacher_liu"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                minLength={3}
                required
              />
            </label>

            <label className="block text-sm font-medium text-ink">
              密码
              <Input
                className="mt-2"
                type="password"
                placeholder="至少 6 位"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                minLength={6}
                required
              />
            </label>

            <Button className="mt-2 h-12 w-full text-base" disabled={isPending}>
              {isPending ? "处理中..." : isLogin ? "登录" : "注册并进入"}
            </Button>
          </form>

          <p className="mt-5 text-sm text-steel">
            {isLogin ? "还没有账号？" : "已经有账号了？"}{" "}
            <Link
              href={isLogin ? "/register" : "/login"}
              className="font-semibold text-lagoon underline decoration-lagoon/25 underline-offset-4"
            >
              {isLogin ? "去注册" : "去登录"}
            </Link>
          </p>
        </Card>
      </div>
    </div>
  );
}
