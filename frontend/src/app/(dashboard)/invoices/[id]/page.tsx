import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { StatusBadge } from "@/components/invoices/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Placeholder invoice detail route (Phase 5 builds the real review
 * experience: document viewer + bounding-box overlays + field corrections).
 *
 * Cache Components pattern: the static shell prerenders, and the dynamic
 * `params` read is deferred into the Suspense boundary below.
 */
export default function InvoiceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  return (
    <Suspense fallback={<DetailFallback />}>
      <InvoiceDetail params={params} />
    </Suspense>
  );
}

function DetailFallback() {
  return <div className="py-16 text-center text-sm text-muted-foreground">Loading invoice…</div>;
}

async function InvoiceDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

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
          <p className="font-mono text-xs text-muted-foreground">{id}</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            Review experience <StatusBadge status="processing" />
          </CardTitle>
          <CardDescription>
            The document viewer with confidence flags and bounding-box overlays ships in
            Phase 5, after the extraction pipeline runs.
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
