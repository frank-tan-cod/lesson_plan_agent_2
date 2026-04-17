"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/auth-provider";
import { useToast } from "@/components/toast-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { login as loginRequest } from "@/lib/api";

type QuickPanel = "notice" | "guide" | "contact" | null;
type PasswordStep = "verify-old" | "set-new";

const quickActions: Array<{
  key: Exclude<QuickPanel, null>;
  eyebrow: string;
  title: string;
  description: string;
  actionLabel: string;
}> = [
    {
      key: "notice",
      eyebrow: "",
      title: "消息通知",
      description: "",
      actionLabel: "查看消息"
    },
    {
      key: "guide",
      eyebrow: "",
      title: "使用指南",
      description: "快速了解系统的主要用法。",
      actionLabel: "查看指南"
    },
    {
      key: "contact",
      eyebrow: "",
      title: "联系我们",
      description: "遇到问题或有建议时，可以通过邮箱联系。",
      actionLabel: "查看邮箱"
    }
  ];

export function ProfileView() {
  const router = useRouter();
  const { push } = useToast();
  const { user, logout } = useAuth();
  const [activePanel, setActivePanel] = useState<QuickPanel>(null);
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [passwordStep, setPasswordStep] = useState<PasswordStep>("verify-old");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [verifyingPassword, setVerifyingPassword] = useState(false);

  function resetPasswordModal() {
    setPasswordModalOpen(false);
    setPasswordStep("verify-old");
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setPasswordError(null);
    setVerifyingPassword(false);
  }

  function openPasswordModal() {
    setPasswordModalOpen(true);
    setPasswordStep("verify-old");
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setPasswordError(null);
    setVerifyingPassword(false);
  }

  async function handleVerifyOldPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const normalizedPassword = oldPassword.trim();

    if (!normalizedPassword) {
      setPasswordError("请先输入旧密码，再进入下一步。");
      return;
    }

    if (normalizedPassword.length < 6) {
      setPasswordError("旧密码至少需要 6 位字符。");
      return;
    }

    if (!user?.username) {
      setPasswordError("当前用户信息缺失，请重新登录后再试。");
      return;
    }

    setPasswordError(null);
    setVerifyingPassword(true);

    try {
      await loginRequest(user.username, normalizedPassword);
      setPasswordStep("set-new");
      push({
        title: "旧密码校验通过",
        description: "现在可以继续输入新密码。",
        tone: "success"
      });
    } catch (error) {
      setPasswordError(error instanceof Error ? error.message : "旧密码校验失败，请稍后重试。");
    } finally {
      setVerifyingPassword(false);
    }
  }

  function handleUpdatePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const normalizedNewPassword = newPassword.trim();
    const normalizedConfirmPassword = confirmPassword.trim();

    if (!normalizedNewPassword) {
      setPasswordError("请输入新密码。");
      return;
    }

    if (normalizedNewPassword.length < 6) {
      setPasswordError("新密码至少需要 6 位字符。");
      return;
    }

    if (normalizedNewPassword === oldPassword.trim()) {
      setPasswordError("新密码不能与旧密码相同。");
      return;
    }

    if (normalizedNewPassword !== normalizedConfirmPassword) {
      setPasswordError("两次输入的新密码不一致，请重新确认。");
      return;
    }

    push({
      title: "密码修改流程已提交",
      description: "当前版本先完成前端演示交互，后续可直接接入后端接口。",
      tone: "success"
    });
    resetPasswordModal();
  }

  return (
    <>
      <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
        <div className="space-y-4">

          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            {quickActions.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setActivePanel(item.key)}
                className="w-full text-left"
              >
                <Card className="h-full transition-transform duration-300 hover:-translate-y-1 hover:shadow-panel">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs uppercase tracking-[0.24em] text-steel">{item.eyebrow}</p>
                      <h2 className="mt-2 font-serif text-2xl text-ink">{item.title}</h2>
                    </div>
                    {item.key === "notice" ? (
                      <Badge className="bg-lagoon/10 text-lagoon">1 条</Badge>
                    ) : null}
                  </div>
                  <p className="mt-3 text-sm leading-6 text-steel">{item.description}</p>
                  <p className="mt-5 text-sm font-semibold text-lagoon">{item.actionLabel}</p>
                </Card>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-6">
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-steel">当前用户</span>
            </div>
            <h2 className="mt-4 font-serif text-3xl text-ink">{user?.username || "未知用户"}</h2>

          </Card>

          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-ink">账号安全</p>
                <h3 className="mt-2 font-serif text-3xl text-ink">修改密码</h3>
              </div>
              <Button onClick={openPasswordModal}>去修改</Button>
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

      <Modal
        open={activePanel === "notice"}
        title="消息通知"
        description="最近一次版本更新消息"
        onClose={() => setActivePanel(null)}
        className="max-w-xl"
      >
        <div className="rounded-[24px] bg-sand/60 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <Badge className="bg-lagoon/10 text-lagoon">v2026.04</Badge>
            <span className="text-sm text-steel">发布时间：2026-04-16</span>
          </div>
          <p className="mt-4 text-base font-semibold text-ink">新版内容</p>
          <p className="mt-2 text-sm leading-6 text-steel">
            新增语音对话功能，修复了一些 bug，并进一步优化了整体用户体验。
          </p>
        </div>
      </Modal>

      <Modal
        open={activePanel === "guide"}
        title="使用指南"
        description="从前端工作台视角快速了解系统使用方式"
        onClose={() => setActivePanel(null)}
      >
        <div className="grid gap-3">
          {[
            "1. 先通过登录或注册进入系统，认证成功后会自动跳转到文档工作台。",
            "2. 在文档工作台中创建教案或 PPT，并按学科、年级或关键词筛选内容。",
            "3. 进入编辑器后，可边预览边通过对话方式调整内容，并结合知识库资料继续完善。",
            "4. 如需统一输出风格，可前往偏好预设页面管理个性化设置，再返回文档继续编辑。"
          ].map((item) => (
            <div key={item} className="rounded-[22px] bg-sand/60 p-4 text-sm leading-6 text-steel">
              {item}
            </div>
          ))}
        </div>
      </Modal>

      <Modal
        open={activePanel === "contact"}
        title="联系我们"
        description="遇到问题、建议或合作需求时，可以通过以下方式联系"
        onClose={() => setActivePanel(null)}
        className="max-w-lg"
      >
        <div className="rounded-[24px] bg-sand/60 p-5">
          <p className="text-sm text-steel">联系邮箱</p>
          <a
            href="mailto:123456@qq.com"
            className="mt-2 inline-block font-serif text-3xl text-ink underline decoration-lagoon/30 underline-offset-4"
          >
            123456@qq.com
          </a>
          <p className="mt-4 text-sm leading-6 text-steel">
            发送邮件时可附上问题截图、账号名称和复现步骤，便于更快协助处理。
          </p>
        </div>
      </Modal>

      <Modal
        open={passwordModalOpen}
        title={passwordStep === "verify-old" ? "验证旧密码" : "设置新密码"}
        description={
          passwordStep === "verify-old"
            ? "请先输入旧密码完成身份校验，再进入下一步。"
            : "旧密码已通过校验，现在请输入新的密码并再次确认。"
        }
        onClose={resetPasswordModal}
        className="max-w-xl"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={passwordStep === "verify-old" ? "bg-lagoon/10 text-lagoon" : ""}>
            步骤 1
          </Badge>
          <Badge className={passwordStep === "set-new" ? "bg-lagoon/10 text-lagoon" : ""}>
            步骤 2
          </Badge>
        </div>

        {passwordError ? (
          <div className="mt-4 rounded-[20px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {passwordError}
          </div>
        ) : null}

        {passwordStep === "verify-old" ? (
          <form className="mt-5 space-y-4" onSubmit={handleVerifyOldPassword}>
            <label className="block text-sm font-medium text-ink">
              旧密码
              <Input
                className="mt-2"
                type="password"
                placeholder="请输入当前密码"
                value={oldPassword}
                onChange={(event) => {
                  setOldPassword(event.target.value);
                  if (passwordError) {
                    setPasswordError(null);
                  }
                }}
                minLength={6}
                required
              />
            </label>

            <div className="flex flex-wrap justify-end gap-3">
              <Button type="button" variant="secondary" onClick={resetPasswordModal} disabled={verifyingPassword}>
                取消
              </Button>
              <Button type="submit" disabled={verifyingPassword}>
                {verifyingPassword ? "校验中..." : "下一步"}
              </Button>
            </div>
          </form>
        ) : (
          <form className="mt-5 space-y-4" onSubmit={handleUpdatePassword}>
            <div className="rounded-[20px] bg-sand/60 px-4 py-3 text-sm text-steel">
              已完成旧密码校验，现在可以设置新的密码。
            </div>

            <label className="block text-sm font-medium text-ink">
              新密码
              <Input
                className="mt-2"
                type="password"
                placeholder="请输入至少 6 位的新密码"
                value={newPassword}
                onChange={(event) => {
                  setNewPassword(event.target.value);
                  if (passwordError) {
                    setPasswordError(null);
                  }
                }}
                minLength={6}
                required
              />
            </label>

            <label className="block text-sm font-medium text-ink">
              确认新密码
              <Input
                className="mt-2"
                type="password"
                placeholder="请再次输入新密码"
                value={confirmPassword}
                onChange={(event) => {
                  setConfirmPassword(event.target.value);
                  if (passwordError) {
                    setPasswordError(null);
                  }
                }}
                minLength={6}
                required
              />
            </label>

            <div className="flex flex-wrap justify-between gap-3">
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setPasswordStep("verify-old");
                  setPasswordError(null);
                }}
              >
                返回上一步
              </Button>
              <div className="flex flex-wrap gap-3">
                <Button type="button" variant="secondary" onClick={resetPasswordModal}>
                  取消
                </Button>
                <Button type="submit">确认修改</Button>
              </div>
            </div>
          </form>
        )}
      </Modal>
    </>
  );
}
