import { Inbox } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Recent-invoice activity panel. Phase 2 shows the empty state only; Phase 5/6
 * replace it with live data from the invoice queue (react-query + polling).
 */
export function RecentActivity() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent activity</CardTitle>
        <CardDescription>Invoices uploaded, extracted, and approved in this workspace.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed py-12 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-muted">
            <Inbox className="size-5 text-muted-foreground" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-medium">No invoices yet</p>
            <p className="text-sm text-muted-foreground">
              Upload a supplier invoice and it will show up here once extraction completes.
            </p>
          </div>
          <Button variant="outline" size="sm" disabled title="Invoice upload lands in Phase 5">
            Upload invoice — coming soon
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
