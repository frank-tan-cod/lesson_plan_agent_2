"use client";

import { cn } from "@/lib/utils";
import { createContext, useContext, useMemo, useState } from "react";

type ToastTone = "info" | "success" | "error";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastContextValue {
  push: (toast: Omit<ToastItem, "id">) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const value = useMemo<ToastContextValue>(
    () => ({
      push(toast) {
        const item = { ...toast, id: Date.now() + Math.floor(Math.random() * 1000) };
        setItems((current) => [...current, item]);
        window.setTimeout(() => {
          setItems((current) => current.filter((entry) => entry.id !== item.id));
        }, 3200);
      }
    }),
    []
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-full max-w-sm flex-col gap-3">
        {items.map((item) => (
          <div
            key={item.id}
            className={cn(
              "pointer-events-auto rounded-2xl border px-4 py-3 shadow-panel backdrop-blur",
              item.tone === "success" && "border-emerald-200 bg-emerald-50/95 text-emerald-900",
              item.tone === "error" && "border-rose-200 bg-rose-50/95 text-rose-900",
              item.tone === "info" && "border-white/60 bg-white/95 text-ink"
            )}
          >
            <p className="text-sm font-semibold">{item.title}</p>
            {item.description ? <p className="mt-1 text-sm opacity-80">{item.description}</p> : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return context;
}
