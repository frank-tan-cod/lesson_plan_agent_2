import { cn } from "@/lib/utils";
import type { HTMLAttributes } from "react";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full bg-sand px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-steel",
        className
      )}
      {...props}
    />
  );
}
