import { LogsClient } from "@/components/aiops/logs-client";
import { fetchLogs } from "@/lib/aiops-api";

interface LogsPageProps {
  searchParams: Promise<{ incident?: string }>;
}

export default async function LogsPage({ searchParams }: LogsPageProps) {
  const params = await searchParams;
  const payload = await fetchLogs(params.incident);
  return <LogsClient initialPayload={payload} incidentFilter={params.incident} />;
}
