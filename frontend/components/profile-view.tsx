"use client";

import { useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export function ProfileView() {
  const router = useRouter();
  const { user, logout } = useAuth();

  return (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <Card className="bg-ink text-white">
        <p className="text-xs uppercase tracking-[0.28em] text-white/60">Profile</p>
        <h1 className="mt-3 font-serif text-4xl">个人中心</h1>
        <p className="mt-4 text-sm leading-6 text-white/72">
          当前已经接入 `/api/user/me`，可以稳定展示登录用户信息。修改密码接口目前后端尚未提供，这里先明确标注为开发中。
        </p>
      </Card>

      <div className="space-y-6">
        <Card>
          <div className="flex flex-wrap items-center gap-3">
            <Badge className="bg-lagoon/10 text-lagoon">Authenticated</Badge>
            <span className="text-sm text-steel">当前用户</span>
          </div>
          <h2 className="mt-4 font-serif text-3xl text-ink">{user?.username || "未知用户"}</h2>
          <p className="mt-3 text-sm text-steel">用户 ID：{user?.id || "未获取"}</p>
        </Card>

        <Card>
          <p className="text-sm font-semibold text-ink">功能状态</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="rounded-[24px] bg-sand/60 p-4">
              <p className="font-semibold text-ink">资料展示</p>
              <p className="mt-2 text-sm text-steel">已完成，与 JWT 鉴权联动。</p>
            </div>
            <div className="rounded-[24px] bg-sand/60 p-4">
              <p className="font-semibold text-ink">修改密码</p>
              <p className="mt-2 text-sm text-steel">后端接口暂未实现，前端保留占位提示。</p>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              variant="secondary"
              onClick={async () => {
                await logout();
                router.replace("/login");
              }}
            >
              退出登录
            </Button>
            <Button variant="ghost" onClick={() => router.push("/documents")}>
              返回文档工作台
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
