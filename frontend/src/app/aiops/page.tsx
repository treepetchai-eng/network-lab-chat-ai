import { DashboardClient } from "@/components/aiops/dashboard-client";
import { fetchDashboard, fetchLogs } from "@/lib/aiops-api";

export default async function AIOpsDashboardPage() {
  const [dashboard, logs] = await Promise.all([
    fetchDashboard(),
    fetchLogs(),
  ]);

  return <DashboardClient initialDashboard={dashboard} initialLogs={logs} />;
}
