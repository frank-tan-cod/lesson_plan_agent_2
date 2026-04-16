import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  overlayClassName?: string;
  bodyClassName?: string;
}

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  className,
  overlayClassName,
  bodyClassName
}: ModalProps) {
  if (!open) {
    return null;
  }

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/45 p-4 backdrop-blur-sm sm:items-center sm:p-6",
        overlayClassName
      )}
    >
      <div
        className={cn(
          "my-auto flex max-h-[calc(100vh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-[32px] bg-mist p-6 shadow-panel sm:max-h-[calc(100vh-3rem)]",
          className
        )}
      >
        <div className="flex shrink-0 items-start justify-between gap-4">
          <div>
            <h3 className="font-serif text-2xl text-ink">{title}</h3>
            {description ? <p className="mt-2 text-sm text-steel">{description}</p> : null}
          </div>
          <button type="button" onClick={onClose} className="text-sm font-semibold text-steel">
            关闭
          </button>
        </div>
        <div className={cn("mt-5 overflow-y-auto pr-1", bodyClassName)}>{children}</div>
      </div>
    </div>
  );
}
