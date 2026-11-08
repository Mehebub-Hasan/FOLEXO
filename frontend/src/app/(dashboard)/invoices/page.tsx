"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { FileUp, Inbox, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { InvoiceUploadModal } from "@/components/invoices/InvoiceUploadModal";
import { StatusBadge } from "@/components/invoices/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, apiErrorMessage } from "@/lib/api";
import { type Paginated, type InvoiceListItem } from "@/lib/api-types";

const PAGE_SIZE = 25;

const STATUS_TABS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "failed", label: "Failed" },
];

function formatAmount(amount: string | null, currency: string | null): string {
  if (amount === null) return "—";
  const value = Number(amount);
  if (!Number.isFinite(value)) return amount;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency ?? "USD",
    }).format(value);
  } catch {
    return `${currency ?? ""} ${amount}`.trim();
  }
}

function InvoicesTable({ items }: { items: InvoiceListItem[] }) {
  return (
    <Card>
      <CardContent className="px-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Vendor</TableHead>
              <TableHead>Invoice #</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const actionLabel = item.status === "needs_review" || item.status === "failed" ? "Review" : "View";
              return (
                <TableRow key={item.id}>
                  <TableCell className="max-w-56 truncate font-medium">{item.original_filename}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.vendor_name ?? "Unassigned"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{item.invoice_number ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {item.invoice_date ?? item.created_at.slice(0, 10)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {formatAmount(item.total_amount, item.currency_code)}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={item.status} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/invoices/${item.id}`}>{actionLabel}</Link>
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export default function InvoicesPage() {
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [uploadOpen, setUploadOpen] = useState(false);

  const invoicesQuery = useInfiniteQuery({
    queryKey: ["invoices", statusFilter],
    queryFn: async ({ pageParam }): Promise<Paginated<InvoiceListItem>> => {
      const response = await api.get<Paginated<InvoiceListItem>>("/api/v1/invoices", {
        params: {
          limit: PAGE_SIZE,
          ...(statusFilter ? { status: statusFilter } : {}),
          ...(pageParam ? { cursor: pageParam } : {}),
        },
      });
      return response.data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.pagination.has_more ? lastPage.pagination.next_cursor : undefined,
    // Live status transitions: refetch while any row is still in the
    // pipeline (uploaded / processing); idle polling otherwise.
    refetchInterval: (query) => {
      const pages = query.state.data?.pages ?? [];
      const hasPending = pages.some((page) =>
        page.data.some(
          (item) => item.status === "uploaded" || item.status === "processing"
        )
      );
      return hasPending ? 2000 : false;
    },
  });

  const items = invoicesQuery.data?.pages.flatMap((page) => page.data) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Invoices</h1>
          <p className="text-sm text-muted-foreground">
            Every supplier invoice in this workspace, newest first.
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <FileUp className="size-4" aria-hidden />
          Upload invoice
        </Button>
      </div>

      <Tabs value={statusFilter} onValueChange={setStatusFilter}>
        <TabsList>
          {STATUS_TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {invoicesQuery.isPending ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Loading invoices…
          </CardContent>
        </Card>
      ) : invoicesQuery.isError ? (
        <Card>
          <CardHeader>
            <CardTitle>Could not load invoices</CardTitle>
            <CardDescription>{apiErrorMessage(invoicesQuery.error)}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => invoicesQuery.refetch()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          </CardContent>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <span className="flex size-10 items-center justify-center rounded-full bg-muted">
              <Inbox className="size-5 text-muted-foreground" aria-hidden />
            </span>
            <div>
              <p className="text-sm font-medium">No invoices yet</p>
              <p className="text-sm text-muted-foreground">
                {statusFilter
                  ? "Nothing matches this filter yet."
                  : "Upload your first supplier invoice to start the pipeline."}
              </p>
            </div>
            <Button onClick={() => setUploadOpen(true)} disabled={Boolean(statusFilter)}>
              <FileUp className="size-4" aria-hidden />
              Upload your first invoice
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <InvoicesTable items={items} />
          {invoicesQuery.hasNextPage && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                onClick={() => invoicesQuery.fetchNextPage()}
                disabled={invoicesQuery.isFetchingNextPage}
              >
                {invoicesQuery.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}

      <InvoiceUploadModal open={uploadOpen} onOpenChange={setUploadOpen} />
    </div>
  );
}
