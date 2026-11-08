"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { DocumentViewer } from "@/components/invoices/DocumentViewer";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useApproveInvoice,
  useCreateVendor,
  useReprocessInvoice,
  useSaveInvoice,
  useVendors,
} from "@/hooks/useInvoiceMutations";
import { useInvoiceDetail } from "@/hooks/useInvoiceDetail";
import { useInvoiceStatus } from "@/hooks/useInvoiceStatus";
import { apiErrorMessage } from "@/lib/api";
import type { BoundingBox, ValidationIssue } from "@/lib/api-types";

/** Review-form field → Azure DI bounding-box key (field_boxes map). */
const HEADER_FIELD_BOX_KEY: Record<string, string> = {
  vendor_name: "VendorName",
  invoice_number: "InvoiceId",
  invoice_date: "InvoiceDate",
  due_date: "DueDate",
  subtotal_amount: "SubTotal",
  tax_amount: "TotalTaxAmount",
  total_amount: "InvoiceTotal",
};

const EDITABLE_STATUSES = new Set(["needs_review", "ready"]);
const REPROCESSABLE_STATUSES = new Set(["needs_review", "ready", "failed"]);

interface ItemRow {
  key: string;
  description: string;
  quantity: string;
  unit_price: string;
  line_total: string;
  bbox: BoundingBox | null;
}

function money(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(2) : value;
}

export default function InvoiceReviewPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const params = useParams<{ id: string }>();
  const invoiceId = params?.id ?? null;

  const detail = useInvoiceDetail(invoiceId);
  const statusQuery = useInvoiceStatus(invoiceId);
  const vendorsQuery = useVendors(Boolean(invoiceId));
  const saveInvoice = useSaveInvoice(invoiceId);
  const approveInvoice = useApproveInvoice(invoiceId);
  const reprocessInvoice = useReprocessInvoice(invoiceId);
  const createVendor = useCreateVendor();

  const [form, setForm] = useState({
    vendor_id: "",
    invoice_number: "",
    invoice_date: "",
    due_date: "",
    currency: "USD",
    subtotal: "0.00",
    tax_amount: "0.00",
    total_amount: "0.00",
  });
  const [rows, setRows] = useState<ItemRow[]>([]);
  const [discount, setDiscount] = useState("0.00");
  const [shipping, setShipping] = useState("0.00");
  const [activeBox, setActiveBox] = useState<BoundingBox | null>(null);
  const [vendorModalOpen, setVendorModalOpen] = useState(false);
  const [newVendorName, setNewVendorName] = useState("");
  const loadedForRef = useRef<string | null>(null);

  // Initialize the editable form once per invoice (server refetches after
  // save/reprocess must not clobber in-progress edits).
  useEffect(() => {
    const data = detail.data;
    if (!data || loadedForRef.current === data.invoice.id) return;
    loadedForRef.current = data.invoice.id;
    setForm({
      vendor_id: data.invoice.vendor_id ?? "",
      invoice_number: data.invoice.invoice_number ?? "",
      invoice_date: data.invoice.invoice_date ?? "",
      due_date: data.invoice.due_date ?? "",
      currency: data.invoice.currency_code ?? "USD",
      subtotal: data.invoice.subtotal_amount ?? "0.00",
      tax_amount: data.invoice.tax_amount ?? "0.00",
      total_amount: data.invoice.total_amount ?? "0.00",
    });
    setRows(
      data.line_items.map((item) => ({
        key: item.id,
        description: item.description ?? "",
        quantity: item.quantity ?? "",
        unit_price: item.unit_price ?? "",
        line_total: item.line_total ?? "",
        bbox: item.bbox,
      }))
    );
  }, [detail.data]);

  // After a reprocess finishes (terminal status), refresh the extracted data.
  const liveStatus = statusQuery.data?.status;
  useEffect(() => {
    if (liveStatus && !["uploaded", "queued", "processing"].includes(liveStatus)) {
      queryClient.invalidateQueries({ queryKey: ["invoice-detail", invoiceId] });
    }
  }, [liveStatus, invoiceId, queryClient]);

  const canEdit = detail.data
    ? EDITABLE_STATUSES.has(detail.data.invoice.status)
    : false;
  const canReprocess = detail.data
    ? REPROCESSABLE_STATUSES.has(detail.data.invoice.status)
    : false;
  const busy = saveInvoice.isPending || approveInvoice.isPending || reprocessInvoice.isPending;

  const viewerBoxes = useMemo(() => {
    const headerBoxes = Object.values(detail.data?.field_boxes ?? {});
    const itemBoxes = rows.map((row) => row.bbox).filter((box): box is BoundingBox => box !== null);
    return [...headerBoxes, ...itemBoxes];
  }, [detail.data?.field_boxes, rows]);

  const focusField = (field: string) => {
    const lineMatch = /^line_items\[(\d+)\]$/.exec(field);
    if (lineMatch) {
      setActiveBox(rows[Number(lineMatch[1]) - 1]?.bbox ?? null);
      return;
    }
    const boxKey = HEADER_FIELD_BOX_KEY[field];
    setActiveBox((boxKey && detail.data?.field_boxes?.[boxKey]) || null);
  };

  const updateRow = (key: string, patch: Partial<ItemRow>) => {
    setRows((previous) =>
      previous.map((row) => {
        if (row.key !== key) return row;
        const next = { ...row, ...patch };
        const quantity = Number(next.quantity);
        const unitPrice = Number(next.unit_price);
        if (Number.isFinite(quantity) && Number.isFinite(unitPrice)) {
          next.line_total = (quantity * unitPrice).toFixed(2);
        }
        return next;
      })
    );
  };

  const addRow = () => {
    setRows((previous) => [
      ...previous,
      { key: `new-${Date.now()}`, description: "", quantity: "", unit_price: "", line_total: "", bbox: null },
    ]);
  };

  const removeRow = (key: string) => {
    setRows((previous) => previous.filter((row) => row.key !== key));
  };

  const handleSave = async () => {
    if (!invoiceId) return;
    try {
      await saveInvoice.mutateAsync({
        vendor_id: form.vendor_id || null,
        invoice_number: form.invoice_number || null,
        invoice_date: form.invoice_date || null,
        due_date: form.due_date || null,
        currency: form.currency || null,
        subtotal: form.subtotal || null,
        tax_amount: form.tax_amount || null,
        total_amount: form.total_amount || null,
        line_items: rows.map((row) => ({
          description: row.description || null,
          quantity: row.quantity || null,
          unit_price: row.unit_price || null,
          line_total: row.line_total || null,
        })),
      });
      toast.success("Changes saved");
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  const handleApprove = async () => {
    if (!invoiceId) return;
    try {
      await approveInvoice.mutateAsync();
      toast.success("Invoice approved");
      router.push("/invoices");
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  const handleReprocess = async () => {
    if (!invoiceId) return;
    try {
      await reprocessInvoice.mutateAsync();
      toast.success("Reprocessing started");
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  const handleCreateVendor = async () => {
    if (!newVendorName.trim()) return;
    try {
      const vendor = await createVendor.mutateAsync({ name: newVendorName.trim() });
      setForm((previous) => ({ ...previous, vendor_id: vendor.id }));
      setVendorModalOpen(false);
      setNewVendorName("");
      toast.success(`Vendor "${vendor.name}" created`);
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  if (detail.isPending) {
    return (
      <div className="py-16 text-center text-sm text-muted-foreground">Loading invoice…</div>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Could not load invoice</CardTitle>
          <CardDescription>{apiErrorMessage(detail.error)}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <Link href="/invoices">Back to invoices</Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const { invoice, document } = detail.data;
  const issues: ValidationIssue[] = invoice.validation_errors ?? [];

  return (
    <div className="flex flex-col gap-4">
      {/* Header row: back, identity, status */}
      <div className="flex flex-wrap items-center gap-3">
        <Button asChild variant="ghost" size="icon" aria-label="Back to invoices">
          <Link href="/invoices">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <span className="truncate">{document.original_filename}</span>
            <StatusBadge status={invoice.status} />
          </h1>
          <p className="font-mono text-xs text-muted-foreground">{invoice.id}</p>
        </div>
      </div>

      {/* Split screen: document | review form (stacked below lg) */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="lg:w-1/2">
          <DocumentViewer
            fileUrl={detail.data.file_url}
            mimeType={document.mime_type}
            boxes={viewerBoxes}
            activeBox={activeBox}
          />
        </div>

        <div className="flex flex-col gap-4 lg:w-1/2">
          {/* Actions */}
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={handleApprove} disabled={!canEdit || busy}>
              {approveInvoice.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <CheckCircle2 className="size-4" aria-hidden />
              )}
              Approve invoice
            </Button>
            <Button variant="outline" onClick={handleSave} disabled={!canEdit || busy}>
              {saveInvoice.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Save className="size-4" aria-hidden />
              )}
              Save changes
            </Button>
            <Button
              variant="outline"
              onClick={handleReprocess}
              disabled={!canReprocess || busy}
              title={canReprocess ? undefined : "Reprocess is available for failed or review-ready invoices"}
            >
              {reprocessInvoice.isPending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="size-4" aria-hidden />
              )}
              Reprocess
            </Button>
          </div>

          {/* Duplicate banner */}
          {invoice.is_possible_duplicate && (
            <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
              <p>
                <span className="font-medium">Possible duplicate.</span> A very similar
                invoice already exists in this workspace (same file, or same vendor /
                number / amount within ±3 days). Double-check before approving.
              </p>
            </div>
          )}

          {/* Validation alerts */}
          {issues.length > 0 && (
            <div className="flex flex-col gap-2">
              {issues.map((issue, index) => (
                <button
                  key={`${issue.code}-${issue.field}-${index}`}
                  type="button"
                  onClick={() => focusField(issue.field)}
                  className={`flex items-start gap-3 rounded-md border p-3 text-left text-sm ${
                    issue.severity === "ERROR"
                      ? "border-red-500/40 bg-red-500/10"
                      : "border-amber-500/40 bg-amber-500/10"
                  }`}
                >
                  <AlertTriangle
                    className={`mt-0.5 size-4 shrink-0 ${
                      issue.severity === "ERROR" ? "text-red-600" : "text-amber-600"
                    }`}
                    aria-hidden
                  />
                  <span>
                    <span className="font-medium">[{issue.code}]</span> {issue.message}
                    <span className="ml-1 underline underline-offset-2">locate field</span>
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Header fields */}
          <Card>
            <CardHeader>
              <CardTitle>Invoice details</CardTitle>
              <CardDescription>
                Extracted by {invoice.extraction_engine ?? "unknown engine"}
                {invoice.confidence_avg !== null &&
                  ` · average confidence ${Number(invoice.confidence_avg).toFixed(2)}`}
                . Focus a field to locate it on the document.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <div className="col-span-2 flex items-end gap-2">
                <div className="flex-1">
                  <label className="mb-1 block text-xs text-muted-foreground">Vendor</label>
                  <select
                    className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                    value={form.vendor_id}
                    onChange={(event) =>
                      setForm((previous) => ({ ...previous, vendor_id: event.target.value }))
                    }
                    onFocus={() => focusField("vendor_name")}
                    disabled={!canEdit}
                  >
                    <option value="">Unassigned</option>
                    {(vendorsQuery.data ?? []).map((vendor) => (
                      <option key={vendor.id} value={vendor.id}>
                        {vendor.name}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setVendorModalOpen(true)}
                  disabled={!canEdit}
                >
                  <Plus className="size-4" aria-hidden />
                  New vendor
                </Button>
              </div>
              <div className="col-span-2">
                <label className="mb-1 block text-xs text-muted-foreground">Invoice #</label>
                <Input
                  value={form.invoice_number}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, invoice_number: event.target.value }))
                  }
                  onFocus={() => focusField("invoice_number")}
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Invoice date</label>
                <Input
                  type="date"
                  value={form.invoice_date}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, invoice_date: event.target.value }))
                  }
                  onFocus={() => focusField("invoice_date")}
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Due date</label>
                <Input
                  type="date"
                  value={form.due_date}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, due_date: event.target.value }))
                  }
                  onFocus={() => focusField("due_date")}
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Currency</label>
                <Input
                  maxLength={3}
                  value={form.currency}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      currency: event.target.value.toUpperCase(),
                    }))
                  }
                  disabled={!canEdit}
                />
              </div>
            </CardContent>
          </Card>

          {/* Line items */}
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <div>
                <CardTitle>Line items</CardTitle>
                <CardDescription>Editing a cell auto-computes its total.</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={addRow} disabled={!canEdit}>
                <Plus className="size-4" aria-hidden />
                Add row
              </Button>
            </CardHeader>
            <CardContent className="px-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Description</TableHead>
                    <TableHead className="w-20">Qty</TableHead>
                    <TableHead className="w-24">Unit price</TableHead>
                    <TableHead className="w-24">Total</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell>
                        <Input
                          value={row.description}
                          onChange={(event) =>
                            updateRow(row.key, { description: event.target.value })
                          }
                          onFocus={() => setActiveBox(row.bbox)}
                          disabled={!canEdit}
                          className="h-8"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={row.quantity}
                          onChange={(event) => updateRow(row.key, { quantity: event.target.value })}
                          onFocus={() => setActiveBox(row.bbox)}
                          disabled={!canEdit}
                          className="h-8"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={row.unit_price}
                          onChange={(event) => updateRow(row.key, { unit_price: event.target.value })}
                          onFocus={() => setActiveBox(row.bbox)}
                          disabled={!canEdit}
                          className="h-8"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={row.line_total}
                          onChange={(event) => updateRow(row.key, { line_total: event.target.value })}
                          onFocus={() => setActiveBox(row.bbox)}
                          disabled={!canEdit}
                          className="h-8"
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Remove line"
                          onClick={() => removeRow(row.key)}
                          disabled={!canEdit}
                        >
                          <Trash2 className="size-4" aria-hidden />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                  {rows.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                        No line items — add one above.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* Financial summary */}
          <Card>
            <CardHeader>
              <CardTitle>Summary</CardTitle>
              <CardDescription>Discount and shipping are advisory (not persisted).</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Subtotal</label>
                <Input
                  value={form.subtotal}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, subtotal: event.target.value }))
                  }
                  onFocus={() => focusField("subtotal_amount")}
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Tax amount</label>
                <Input
                  value={form.tax_amount}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, tax_amount: event.target.value }))
                  }
                  onFocus={() => focusField("tax_amount")}
                  disabled={!canEdit}
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Discount</label>
                <Input value={discount} onChange={(event) => setDiscount(event.target.value)} disabled={!canEdit} />
              </div>
              <div>
                <label className="mb-1 block text-xs text-muted-foreground">Shipping</label>
                <Input value={shipping} onChange={(event) => setShipping(event.target.value)} disabled={!canEdit} />
              </div>
              <div className="col-span-2 flex items-center justify-between rounded-md bg-muted p-3">
                <span className="text-sm font-medium">Total amount</span>
                <span className="font-mono text-sm">
                  {money(form.total_amount)} {form.currency}
                </span>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Quick vendor creation */}
      <Dialog open={vendorModalOpen} onOpenChange={setVendorModalOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>New vendor</DialogTitle>
            <DialogDescription>Created in this workspace and linked to the invoice.</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <Input
              placeholder="Vendor name"
              value={newVendorName}
              onChange={(event) => setNewVendorName(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVendorModalOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateVendor} disabled={createVendor.isPending || !newVendorName.trim()}>
              {createVendor.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
