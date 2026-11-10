"use client";

import { AlertTriangle, BarChart3, Clock, FileText } from "lucide-react";
import Link from "next/link";

import { MetricCard } from "@/components/dashboard/metric-card";
import { StatusBadge } from "@/components/invoices/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { apiErrorMessage } from "@/lib/api";
import { useDashboardSummary } from "@/hooks/useDashboard";

function formatMoney(value: string | null, currency: string): string {
  if (value === null) return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(parsed);
  } catch {
    return `${currency} ${value}`;
  }
}

const STATIC_METRICS = [
  { label: "Total invoices", value: "0", hint: "All statuses", icon: FileText },
  { label: "Pending review", value: "0", hint: "Awaiting a human", icon: Clock },
  { label: "Approved invoices", value: "0", hint: "Ready for export", icon: BarChart3 },
  { label: "Extraction errors", value: "0", hint: "Failed pipeline runs", icon: AlertTriangle },
];

export default function DashboardPage() {
  const summary = useDashboardSummary();
  const data = summary.data;

  const metrics = data
    ? [
        {
          label: "Total spend",
          value: formatMoney(data.total_approved_spend, data.base_currency),
          hint: `Approved, in ${data.base_currency}`,
          icon: BarChart3,
        },
        {
          label: "Pending review",
          value: String(data.pending_review_count),
          hint: "Awaiting a human",
          icon: Clock,
        },
        {
          label: "Approved invoices",
          value: String(data.approved_invoices_count),
          hint: `of ${data.total_invoices_count} total`,
          icon: FileText,
        },
        {
          label: "Extraction errors",
          value: String(data.failed_invoices_count),
          hint: "Failed pipeline runs",
          icon: AlertTriangle,
        },
      ]
    : STATIC_METRICS;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Overview of your accounts-payable pipeline.
        </p>
      </div>

      {summary.isError && (
        <p className="text-sm text-destructive">{apiErrorMessage(summary.error)}</p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((metric) => (
          <MetricCard key={metric.label} {...metric} />
        ))}
      </div>

      {data && data.unpriced_currencies.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          Invoices in {data.unpriced_currencies.join(", ")} have no exchange rate yet and
          are excluded from total spend.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent invoices</CardTitle>
            <CardDescription>Last 5 documents in this workspace.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {data && data.recent_activity.length > 0 ? (
              data.recent_activity.map((item) => (
                <Link
                  key={item.id}
                  href={`/invoices/${item.id}`}
                  className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm hover:bg-muted"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium">
                      {item.invoice_number ?? "Unnumbered"}
                      <span className="ml-2 text-muted-foreground">
                        {item.vendor_name ?? "Unassigned"}
                      </span>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {item.created_at.slice(0, 10)}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm">
                      {formatMoney(item.total_amount, item.currency_code ?? data.base_currency)}
                    </span>
                    <StatusBadge status={item.status} />
                  </div>
                </Link>
              ))
            ) : (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No invoices yet — upload one from the inbox.
              </p>
            )}
            <Button asChild variant="ghost" size="sm" className="mt-1 self-start">
              <Link href="/invoices">Open the invoice inbox →</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Top suppliers</CardTitle>
            <CardDescription>
              By approved spend, in {data?.base_currency ?? "base currency"}.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {data && data.spend_by_vendor.length > 0 ? (
              data.spend_by_vendor.map((entry) => (
                <div
                  key={entry.vendor_name}
                  className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm"
                >
                  <span className="truncate font-medium">{entry.vendor_name}</span>
                  <span className="font-mono">
                    {formatMoney(entry.total_spend, entry.currency)}
                  </span>
                </div>
              ))
            ) : (
              <p className="py-4 text-center text-sm text-muted-foreground">
                Approved spend will rank suppliers here.
              </p>
            )}
            <Button asChild variant="ghost" size="sm" className="mt-1 self-start">
              <Link href="/vendors">Open the vendor directory →</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
