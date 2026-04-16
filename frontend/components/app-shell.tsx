"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const navigation = [
  { href: "/documents", label: "文档工作台" },
  { href: "/knowledge", label: "知识库" },
  { href: "/preferences", label: "偏好预设" },
  { href: "/profile", label: "个人中心" }
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { status, user, logout } = useAuth();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace(`/login?redirect=${encodeURIComponent(pathname || "/documents")}`);
    }
  }, [pathname, router, status]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-dashboard-glow">
        <div className="rounded-[32px] border border-white/80 bg-white/90 px-8 py-10 shadow-panel">
          <p className="font-serif text-3xl text-ink">正在载入你的教学工作台</p>
          <p className="mt-3 text-sm text-steel">同步认证状态、会话与项目列表中。</p>
        </div>
      </div>
    );
  }

  if (status === "unauthenticated") {
    return null;
  }

  return (
    <div className="min-h-screen bg-dashboard-glow">
      <div className="mx-auto flex min-h-screen max-w-[1600px] gap-6 px-4 py-4 lg:px-6">
        <aside className="grain hidden w-80 shrink-0 overflow-hidden rounded-[36px] bg-ink px-6 py-7 text-white shadow-panel lg:block">
          <h1 className="mt-4 font-serif text-4xl leading-tight">
            教学协助
            <br />
            智能体
          </h1>
          <nav className="mt-10 space-y-2">
            {navigation.map((item) => {
              const active = pathname?.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "block rounded-2xl px-4 py-3 text-sm font-semibold transition",
                    active ? "bg-white text-ink" : "text-white/72 hover:bg-white/10 hover:text-white"
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="mt-10 rounded-[28px] bg-white/8 p-5">
            <p className="text-xs uppercase tracking-[0.25em] text-white/55">当前账号</p>
            <p className="mt-3 text-lg font-semibold">{user?.username}</p>
            <Button
              variant="secondary"
              className="mt-5 w-full bg-white text-ink hover:bg-slate-100"
              onClick={async () => {
                await logout();
                router.replace("/login");
              }}
            >
              退出登录
            </Button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <main className="min-h-[calc(100vh-3rem)]">{children}</main>
        </div>
      </div>
    </div>
  );
}
