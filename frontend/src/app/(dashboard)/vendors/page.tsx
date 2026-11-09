"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Building2, Plus, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { apiErrorMessage } from "@/lib/api";
import type { VendorDirectoryEntry } from "@/lib/api-types";
import { useCreateVendor } from "@/hooks/useInvoiceMutations";
import { useVendorsDirectory } from "@/hooks/useVendors";

function formatSpend(entry: VendorDirectoryEntry): string {
  if (entry.total_spend === null) return "—";
  const value = Number(entry.total_spend);
  if (!Number.isFinite(value)) return entry.total_spend;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: entry.default_currency ?? "USD",
    }).format(value);
  } catch {
    return `${entry.default_currency ?? ""} ${entry.total_spend}`.trim();
  }
}

export default function VendorsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [newVendor, setNewVendor] = useState({ name: "", tax_identifier: "", email: "" });
  const directory = useVendorsDirectory(search);
  const createVendor = useCreateVendor();

  const handleCreate = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!newVendor.name.trim()) return;
    try {
      const vendor = await createVendor.mutateAsync({
        name: newVendor.name.trim(),
        tax_identifier: newVendor.tax_identifier.trim() || null,
        email: newVendor.email.trim() || null,
      });
      queryClient.invalidateQueries({ queryKey: ["vendors-directory"] });
      setModalOpen(false);
      setNewVendor({ name: "", tax_identifier: "", email: "" });
      toast.success(`Vendor "${vendor.name}" created`);
    } catch (error) {
      toast.error(apiErrorMessage(error));
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Vendors</h1>
          <p className="text-sm text-muted-foreground">
            Supplier directory with approved spend and invoice history.
          </p>
        </div>
        <Button onClick={() => setModalOpen(true)}>
          <Plus className="size-4" aria-hidden />
          Add vendor
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search
          className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search by name or tax ID…"
          className="pl-8"
        />
      </div>

      {directory.isPending ? (
        <div className="py-10 text-center text-sm text-muted-foreground">Loading vendors…</div>
      ) : directory.isError ? (
        <div className="py-10 text-center text-sm text-destructive">
          {apiErrorMessage(directory.error)}
        </div>
      ) : directory.data.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed py-16 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-muted">
            <Building2 className="size-5 text-muted-foreground" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-medium">No vendors yet</p>
            <p className="text-sm text-muted-foreground">
              Vendors are created automatically during invoice review, or add one manually.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setModalOpen(true)}>
            <Plus className="size-4" aria-hidden />
            Add vendor
          </Button>
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Vendor</TableHead>
                <TableHead>Tax ID / Reg #</TableHead>
                <TableHead className="text-right">Invoices</TableHead>
                <TableHead className="text-right">Approved spend</TableHead>
                <TableHead>Last invoice</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {directory.data.map((vendor) => (
                <TableRow key={vendor.id}>
                  <TableCell className="font-medium">{vendor.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {vendor.tax_identifier ?? "—"}
                  </TableCell>
                  <TableCell className="text-right">{vendor.invoice_count}</TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {formatSpend(vendor)}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {vendor.last_invoice_date ?? "—"}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/vendors/${vendor.id}`}>View invoices</Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={modalOpen} onOpenChange={setModalOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Add vendor</DialogTitle>
            <DialogDescription>
              Created in this workspace. Duplicate names are rejected — suffixes like
              GmbH/Ltd are ignored when checking.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCreate} className="flex flex-col gap-3">
            <Input
              placeholder="Vendor name"
              value={newVendor.name}
              onChange={(event) =>
                setNewVendor((previous) => ({ ...previous, name: event.target.value }))
              }
              required
            />
            <Input
              placeholder="Tax ID / Reg # (optional)"
              value={newVendor.tax_identifier}
              onChange={(event) =>
                setNewVendor((previous) => ({ ...previous, tax_identifier: event.target.value }))
              }
            />
            <Input
              type="email"
              placeholder="Email (optional)"
              value={newVendor.email}
              onChange={(event) =>
                setNewVendor((previous) => ({ ...previous, email: event.target.value }))
              }
            />
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setModalOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={createVendor.isPending}>
                {createVendor.isPending ? "Creating…" : "Create vendor"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
