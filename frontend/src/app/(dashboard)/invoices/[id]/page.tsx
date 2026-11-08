"use client";

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { StatusBadge } from "@/components/invoices/status-badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useInvoiceStatus } from "@/hooks/useInvoiceStatus";

/**
 * Invoice detail placeholder with live pipeline status. Phase 5 replaces the
 * body with the real review experience (document viewer + bounding-box
 * overlays + field corrections); the polling wiring is production-ready.
 */
export default function InvoiceDetailPage() {
  const params = useParams<{ id: string }>();
  const invoiceId = params?.id ?? null;
  const statusQuery = useInvoiceStatus(invoiceId);
  const status = statusQuery.data?.status;
  const pending = status === "uploaded" || status === "processing";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="Back to invoices">
          <Link href="/invoices">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Invoice review</h1>
          <p className="font-mono text-xs text-muted-foreground">{invoiceId}</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Pipeline status
            {statusQuery.isFetching && (
              <RefreshCw className="size-4 animate-spin text-muted-foreground" aria-hidden />
            )}
          </CardTitle>
          <CardDescription>
            Polled every 2 seconds while the invoice is in the pipeline.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {statusQuery.isError ? (
            <p className="text-sm text-destructive">Could not load invoice status.</p>
          ) : statusQuery.isPending || !status ? (
            <p className="text-sm text-muted-foreground">Loading status…</p>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <StatusBadge status={status} />
                <span className="text-sm text-muted-foreground">
                  {statusQuery.data?.progress_percent}% through the pipeline
                </span>
              </div>
              {pending && <Progress value={statusQuery.data?.progress_percent ?? 0} className="h-2" />}
              {statusQuery.data?.error_message && (
                <p className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-destructive">
                  {statusQuery.data.error_message}
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Review experience</CardTitle>
          <CardDescription>
            The document viewer with confidence flags and bounding-box overlays ships in
            Phase 5, after the extraction pipeline runs.
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
