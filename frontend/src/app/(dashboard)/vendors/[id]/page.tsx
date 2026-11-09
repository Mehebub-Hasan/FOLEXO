"use client";

import { ArrowLeft, Loader2, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/invoices/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiErrorMessage } from "@/lib/api";
import { useUpdateVendor, useVendorDetail } from "@/hooks/useVendors";

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

export default function VendorDetailPage() {
  const params = useParams<{ id: string }>();
  const vendorId = params?.id ?? null;
  const detail = useVendorDetail(vendorId);
  const updateVendor = useUpdateVendor(vendorId);

  const [contact, setContact] = useState({ email: "", address: "", tax_identifier: "", notes: "" });
  const loadedForRef = useRef<string | null>(null);

  useEffect(() => {
    const data = detail.data;
    if (!data || loadedForRef.current === data.id) return;
    loadedForRef.current = data.id;
    setContact({
      email: data.email ?? "",
      address: data.address ?? "",
      tax_identifier: data.tax_identifier ?? "",
      notes: data.notes ?? "",
    });
  }, [detail.data]);

  const handleSave = async () => {
    if (!vendorId) return;
    try {
      await updateVendor.mutateAsync({
        email: contact.email || null,
        address: contact.address || null,
        tax_identifier: contact.tax_identifier || null,
        notes: contact.notes || null,
      });
      toast.success("Vendor updated");
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  if (detail.isPending) {
    return <div className="py-16 text-center text-sm text-muted-foreground">Loading vendor…</div>;
  }
  if (detail.isError || !detail.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Could not load vendor</CardTitle>
          <CardDescription>{apiErrorMessage(detail.error)}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <Link href="/vendors">Back to vendors</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const vendor = detail.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button asChild variant="ghost" size="icon" aria-label="Back to vendors">
            <Link href="/vendors">
              <ArrowLeft className="size-4" />
            </Link>
          </Button>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{vendor.name}</h1>
            <p className="font-mono text-xs text-muted-foreground">{vendor.normalized_name}</p>
          </div>
        </div>
        <div className="flex items-center gap-6 text-sm">
          <div className="text-right">
            <p className="text-xs text-muted-foreground">Invoices</p>
            <p className="font-semibold">{vendor.invoice_count}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-muted-foreground">Approved spend</p>
            <p className="font-mono font-semibold">{formatAmount(vendor.total_spend, vendor.default_currency)}</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-muted-foreground">Last invoice</p>
            <p className="font-semibold">{vendor.last_invoice_date ?? "—"}</p>
          </div>
        </div>
      </div>

      {/* Editable contact card */}
      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>Contact information</CardTitle>
            <CardDescription>Extracted values, refined by your team.</CardDescription>
          </div>
          <Button onClick={handleSave} disabled={updateVendor.isPending}>
            {updateVendor.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Save className="size-4" aria-hidden />
            )}
            Save
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Email</label>
            <Input
              type="email"
              value={contact.email}
              onChange={(event) =>
                setContact((previous) => ({ ...previous, email: event.target.value }))
              }
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-muted-foreground">Tax ID / Reg #</label>
            <Input
              value={contact.tax_identifier}
              onChange={(event) =>
                setContact((previous) => ({ ...previous, tax_identifier: event.target.value }))
              }
            />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-muted-foreground">Address</label>
            <Input
              value={contact.address}
              onChange={(event) =>
                setContact((previous) => ({ ...previous, address: event.target.value }))
              }
            />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-muted-foreground">Notes</label>
            <Input
              value={contact.notes}
              onChange={(event) =>
                setContact((previous) => ({ ...previous, notes: event.target.value }))
              }
              placeholder="Payment terms, billing quirks…"
            />
          </div>
        </CardContent>
      </Card>

      {/* Invoice history */}
      <Card>
        <CardHeader>
          <CardTitle>Invoices from this vendor</CardTitle>
          <CardDescription>
            {vendor.pagination.total} total · showing {vendor.invoices.length} (newest first)
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice #</TableHead>
                <TableHead>Date</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {vendor.invoices.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-medium">
                    {row.invoice_number ?? "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {row.invoice_date ?? row.created_at.slice(0, 10)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {formatAmount(row.total_amount, row.currency_code)}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={row.status} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/invoices/${row.id}`}>
                        {row.status === "needs_review" || row.status === "failed" ? "Review" : "View"}
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {vendor.invoices.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                    No invoices from this vendor yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
