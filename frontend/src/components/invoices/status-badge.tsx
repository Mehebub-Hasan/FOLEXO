import type { InvoiceStatus } from "@/lib/api-types";
import { Badge } from "@/components/ui/badge";

interface StatusConfig {
  label: string;
  className: string;
}

/** Color-coded presentation for every invoice lifecycle state. */
const STATUS_CONFIG: Record<InvoiceStatus, StatusConfig> = {
  uploaded: { label: "UPLOADED", className: "bg-slate-500/15 text-slate-700 dark:text-slate-300" },
  queued: { label: "QUEUED", className: "bg-sky-500/15 text-sky-700 dark:text-sky-300" },
  processing: { label: "PROCESSING", className: "bg-blue-500/15 text-blue-700 dark:text-blue-300" },
  needs_review: { label: "NEEDS REVIEW", className: "bg-amber-500/15 text-amber-800 dark:text-amber-300" },
  ready: { label: "READY", className: "bg-teal-500/15 text-teal-700 dark:text-teal-300" },
  approved: { label: "APPROVED", className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" },
  rejected: { label: "REJECTED", className: "bg-rose-500/15 text-rose-700 dark:text-rose-300" },
  exported: { label: "EXPORTED", className: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300" },
  archived: { label: "ARCHIVED", className: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-400" },
  failed: { label: "FAILED", className: "bg-red-500/15 text-red-700 dark:text-red-300" },
};

const FALLBACK: StatusConfig = { label: "UNKNOWN", className: "bg-muted text-muted-foreground" };

export function StatusBadge({ status }: { status: InvoiceStatus }) {
  const config = STATUS_CONFIG[status] ?? FALLBACK;
  return (
    <Badge variant="outline" className={config.className}>
      {config.label}
    </Badge>
  );
}
