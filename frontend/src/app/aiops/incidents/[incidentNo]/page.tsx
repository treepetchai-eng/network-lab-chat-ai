import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { IncidentDetailClient } from "@/components/aiops/incident-detail-client";
import { fetchIncidentDetail } from "@/lib/aiops-api";

interface IncidentDetailPageProps {
  params: Promise<{ incidentNo: string }>;
}

export default async function IncidentDetailPage({ params }: IncidentDetailPageProps) {
  const { incidentNo } = await params;

  try {
    const data = await fetchIncidentDetail(incidentNo);
    return <IncidentDetailClient initialData={data} />;
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Unknown error";
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <AlertCircle className="h-10 w-10 text-rose-400/60" />
        <p className="mt-4 text-[0.95rem] font-semibold text-slate-200">
          Could not load incident {incidentNo}
        </p>
        <p className="mt-1 text-[0.8rem] text-slate-500">{msg}</p>
        <Link
          href="/aiops/incidents"
          className="mt-5 rounded border border-white/10 bg-white/[0.04] px-4 py-2 text-[0.82rem] text-slate-400 transition hover:text-slate-200"
        >
          ← Back to Incidents
        </Link>
      </div>
    );
  }
}
