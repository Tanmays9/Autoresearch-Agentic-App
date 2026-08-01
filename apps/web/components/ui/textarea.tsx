import * as React from "react";

import { cn } from "@/lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "min-h-28 w-full resize-y rounded-xl border border-border bg-white px-4 py-3 text-sm leading-6 outline-none placeholder:text-slate-400 focus:border-primary focus:ring-2 focus:ring-primary/10",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
