import { LogsClient } from "@/components/aiops/logs-client";
import { fetchDevices, fetchLogs } from "@/lib/aiops-api";

interface LogsPageProps {
  searchParams: Promise<{ incident?: string }>;
}

export default async function LogsPage({ searchParams }: LogsPageProps) {
  const params = await searchParams;
  const [payload, devices] = await Promise.all([
    fetchLogs(params.incident),
    fetchDevices().catch(() => []),
  ]);
  return (
    <LogsClient
      initialPayload={payload}
      incidentFilter={params.incident}
      devices={devices.map(d => ({ hostname: d.hostname, ip_address: d.ip_address }))}
    />
  );
}
