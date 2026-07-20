import * as React from "react";

import { cn } from "@/lib/utils";

const colors: Record<string, string> = {
  available: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  supported: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  running: "bg-blue-50 text-blue-700 ring-blue-200",
  fetching: "bg-blue-50 text-blue-700 ring-blue-200",
  leased: "bg-blue-50 text-blue-700 ring-blue-200",
  reviewing: "bg-violet-50 text-violet-700 ring-violet-200",
  queued: "bg-amber-50 text-amber-700 ring-amber-200",
  paused: "bg-amber-50 text-amber-700 ring-amber-200",
  cancel_requested: "bg-amber-50 text-amber-700 ring-amber-200",
  mixed: "bg-amber-50 text-amber-700 ring-amber-200",
  failed: "bg-red-50 text-red-700 ring-red-200",
  cancelled: "bg-slate-100 text-slate-600 ring-slate-200",
  skipped: "bg-slate-100 text-slate-600 ring-slate-200",
  not_installed: "bg-slate-100 text-slate-600 ring-slate-200",
  unsupported: "bg-slate-100 text-slate-600 ring-slate-200",
};

export function Badge({ value, className }: { value: string; className?: string }) {
  return (
    <span className={cn("inline-flex rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide ring-1 ring-inset", colors[value] || "bg-slate-100 text-slate-700 ring-slate-200", className)}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
