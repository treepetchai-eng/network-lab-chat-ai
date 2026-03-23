import { IncidentDetailClient } from "@/components/aiops/incident-detail-client";
import { fetchIncidentDetail } from "@/lib/aiops-api";

interface IncidentDetailPageProps {
  params: Promise<{ incidentNo: string }>;
}

export default async function IncidentDetailPage({ params }: IncidentDetailPageProps) {
  const { incidentNo } = await params;
  const data = await fetchIncidentDetail(incidentNo);
  return <IncidentDetailClient initialData={data} />;
}

