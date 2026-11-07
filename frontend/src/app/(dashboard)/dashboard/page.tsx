import { AlertTriangle, BarChart3, Clock, FileText } from "lucide-react";

import { MetricCard } from "@/components/dashboard/metric-card";
import { RecentActivity } from "@/components/dashboard/recent-activity";

const METRICS = [
  { label: "Total invoices", value: "0", hint: "This month", icon: FileText },
  { label: "Pending review", value: "0", hint: "Awaiting a human", icon: Clock },
  { label: "Total spend", value: "$0.00", hint: "Approved invoices", icon: BarChart3 },
  { label: "Extraction errors", value: "0", hint: "Failed pipeline runs", icon: AlertTriangle },
];

/**
 * Dashboard overview — Phase 2 shows static placeholders. The values wire up
 * to GET /api/v1/stats/summary in Phase 7 via react-query.
 */
export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Overview of your accounts-payable pipeline.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {METRICS.map((metric) => (
          <MetricCard key={metric.label} {...metric} />
        ))}
      </div>

      <RecentActivity />
    </div>
  );
}
