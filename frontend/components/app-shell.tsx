"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type MouseEvent } from "react";

import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type NavigationItem = {
  href: string;
  label: string;
  icon: string;
};

const navigation: NavigationItem[] = [
  { href: "/documents", label: "文档工作台", icon: "/icons/work.png" },
  { href: "/knowledge", label: "知识库", icon: "/icons/book.png" },
  { href: "/preferences", label: "偏好预设", icon: "/icons/setting.png" },
  { href: "/profile", label: "个人中心", icon: "/icons/self.png" }
];

function getActiveHref(pathname: string | null) {
  return navigation.find((item) => pathname?.startsWith(item.href))?.href ?? "/documents";
}

function SidebarRail({
  activeHref,
  onItemClick,
  className
}: {
  activeHref: string;
  onItemClick: (href: string) => void;
  className?: string;
}) {
  return (
    <aside
      className={cn(
        "grain flex h-full flex-col items-center justify-start rounded-[30px] bg-ink px-1.5 py-5 text-white shadow-panel",
        className
      )}
    >
      <div className="mt-2 flex flex-col items-center gap-2.5">
        {navigation.map((item) => {
          const active = activeHref === item.href;
          return (
            <button
              key={item.href}
              type="button"
              aria-label={item.label}
              aria-pressed={active}
              onClick={() => onItemClick(item.href)}
              className={cn(
                "group flex h-11 w-11 items-center justify-center rounded-2xl border border-transparent transition-all duration-300 ease-in-out focus:outline-none focus:ring-2 focus:ring-white/25",
                active ? "bg-white shadow-soft" : "bg-transparent hover:-translate-y-0.5 hover:bg-white/12"
              )}
              title={item.label}
            >
              <Image
                src={item.icon}
                alt=""
                width={24}
                height={24}
                className={cn(
                  "h-6 w-6 transition-all duration-300 ease-in-out",
                  active ? "scale-105" : "opacity-95 group-hover:scale-105 group-hover:opacity-100"
                )}
              />
            </button>
          );
        })}
      </div>
    </aside>
  );
}

function SidebarPanel({
  activeHref,
  username,
  onLogout,
  onItemClick,
  onCollapse,
  className
}: {
  activeHref: string;
  username?: string;
  onLogout: () => Promise<void>;
  onItemClick: (href: string) => void;
  onCollapse: () => void;
  className?: string;
}) {
  function handleNavigationClick(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (activeHref !== href) {
      return;
    }

    event.preventDefault();
    onItemClick(href);
  }

  return (
    <div className={cn("grain flex h-full flex-col rounded-[34px] bg-ink px-4 py-5 text-white shadow-panel sm:px-5 sm:py-6", className)}>
      <nav className="space-y-1.5">
        {navigation.map((item) => {
          const active = activeHref === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={(event) => handleNavigationClick(event, item.href)}
              className={cn(
                "group flex items-center gap-3 rounded-[22px] px-3.5 py-2.5 text-[13px] font-semibold leading-5 transition-all duration-300 ease-in-out sm:text-sm",
                active ? "bg-white text-ink shadow-soft" : "text-white/74 hover:bg-white/10 hover:text-white"
              )}
            >
              <span
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl transition-all duration-300 ease-in-out",
                  active ? "bg-ink/8" : "bg-white/8 group-hover:bg-white/12"
                )}
              >
                <Image
                  src={item.icon}
                  alt=""
                  width={20}
                  height={20}
                  className="h-5 w-5"
                />
              </span>
              <span className="min-w-0 flex-1 truncate pt-0.5">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto">
        <div className="px-2 pb-3 text-center">
          <p className="whitespace-nowrap font-serif text-[19px] leading-snug tracking-[0.04em] text-white/64 sm:text-[20px]">
            教学协助智能体
          </p>
        </div>

        <div className="rounded-[26px] bg-white/8 p-4">
          <p className="text-[10px] uppercase tracking-[0.24em] text-white/52">当前账号</p>
          <p className="mt-2 truncate text-sm font-semibold leading-6 text-white sm:text-[15px]">{username}</p>

          <Button
            variant="secondary"
            className="mt-4 w-full bg-white px-4 py-2.5 text-[13px] text-ink hover:bg-slate-100"
            onClick={onLogout}
          >
            退出登录
          </Button>

          <button
            type="button"
            onClick={onCollapse}
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-full border border-white/14 bg-white/10 px-4 py-2.5 text-[13px] font-semibold text-white transition-all duration-300 ease-in-out hover:bg-white/16 hover:text-white focus:outline-none focus:ring-2 focus:ring-white/25"
          >
            <span aria-hidden="true" className="text-sm leading-none">
              ←
            </span>
            收起侧栏
          </button>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { status, user, logout } = useAuth();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace(`/login?redirect=${encodeURIComponent(pathname || "/documents")}`);
    }
  }, [pathname, router, status]);

  const activeHref = getActiveHref(pathname);

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  function handleRailClick(href: string) {
    if (isSidebarOpen && activeHref === href) {
      setIsSidebarOpen(false);
      return;
    }

    setIsSidebarOpen(true);
    if (!pathname?.startsWith(href)) {
      router.push(href);
    }
  }

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
      <div className="mr-auto flex min-h-screen w-full max-w-[1600px] gap-4 py-3 pl-0 pr-3 sm:py-4 sm:pl-1 sm:pr-4 lg:gap-6 lg:pl-1 lg:pr-6">
        <div
          className={cn(
            "relative hidden h-[calc(100vh-2rem)] shrink-0 overflow-visible transition-[width] duration-300 ease-in-out lg:flex",
            isSidebarOpen ? "w-[254px]" : "w-[60px]"
          )}
        >
          <SidebarRail
            className={cn(
              "absolute inset-0 transition-all duration-300 ease-in-out",
              isSidebarOpen ? "pointer-events-none -translate-x-3 opacity-0" : "translate-x-0 opacity-100"
            )}
            activeHref={activeHref}
            onItemClick={handleRailClick}
          />

          <SidebarPanel
            activeHref={activeHref}
            username={user?.username}
            onLogout={handleLogout}
            onItemClick={handleRailClick}
            onCollapse={() => setIsSidebarOpen(false)}
            className={cn(
              "absolute inset-y-0 left-0 w-[254px] transition-all duration-300 ease-in-out",
              isSidebarOpen ? "-translate-x-9 opacity-100" : "pointer-events-none translate-x-3 opacity-0"
            )}
          />
        </div>

        <SidebarRail
          className="h-[calc(100vh-1.5rem)] w-14 sm:h-[calc(100vh-2rem)] sm:w-[60px] lg:hidden"
          activeHref={activeHref}
          onItemClick={handleRailClick}
        />

        <button
          type="button"
          aria-label="关闭侧边栏遮罩"
          className={cn(
            "fixed inset-0 z-30 bg-ink/38 backdrop-blur-[2px] transition-opacity duration-300 ease-in-out lg:hidden",
            isSidebarOpen ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0"
          )}
          onClick={() => setIsSidebarOpen(false)}
        />
        <div
          className={cn(
            "fixed inset-y left-0 z-40 w-[264px] transition-all duration-300 ease-in-out sm:left-0 sm:w-[276px] lg:hidden",
            isSidebarOpen ? "translate-x-0 opacity-100" : "pointer-events-none -translate-x-6 opacity-0"
          )}
          aria-hidden={!isSidebarOpen}
        >
          <SidebarPanel
            activeHref={activeHref}
            username={user?.username}
            onLogout={handleLogout}
            onItemClick={handleRailClick}
            onCollapse={() => setIsSidebarOpen(false)}
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <main className="min-h-[calc(100vh-1.5rem)] sm:min-h-[calc(100vh-2rem)]">{children}</main>
        </div>
      </div>
    </div>
  );
}
