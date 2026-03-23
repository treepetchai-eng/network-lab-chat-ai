import { IncidentsClient } from "@/components/aiops/incidents-client";
import { fetchIncidents } from "@/lib/aiops-api";

export default async function IncidentsPage() {
  const incidents = await fetchIncidents();
  return <IncidentsClient initialIncidents={incidents} />;
}
