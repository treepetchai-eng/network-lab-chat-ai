import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchDeviceDetail } from "@/lib/aiops-api";

interface DeviceDetailPageProps {
  params: Promise<{ hostname: string }>;
}

export default async function DeviceDetailPage({ params }: DeviceDetailPageProps) {
  const { hostname } = await params;
  const data = await fetchDeviceDetail(decodeURIComponent(hostname));

  const device = data.device;

  return (
    <div className="space-y-5">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-[0.78rem] text-slate-400">
        <Link href="/aiops/devices" className="transition hover:text-cyan-300">Devices</Link>
        <span>/</span>
        <span className="text-slate-200">{device.hostname}</span>
      </nav>

      {/* Device Info */}
      <SectionCard title={device.hostname} eyebrow="Device Details">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
            <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">IP Address</p>
            <p className="mt-1.5 text-[0.9rem] font-medium text-white">{device.ip_address}</p>
          </div>
          <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
            <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Platform</p>
            <p className="mt-1.5 text-[0.9rem] font-medium text-white">{device.os_platform}</p>
          </div>
          <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
            <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Role</p>
            <p className="mt-1.5"><StatusBadge value={device.device_role} /></p>
          </div>
          <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
            <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Site</p>
            <p className="mt-1.5 text-[0.9rem] font-medium text-white">{device.site}</p>
          </div>
          {device.version && (
            <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
              <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Version</p>
              <p className="mt-1.5 text-[0.9rem] font-medium text-white">{device.version}</p>
            </div>
          )}
          <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
            <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Open Incidents</p>
            <p className="mt-1.5 text-[0.9rem] font-medium text-white">{device.open_incident_count}</p>
          </div>
          {device.last_incident_seen && (
            <div className="rounded-[0.95rem] border border-white/8 bg-white/[0.03] px-3.5 py-3">
              <p className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] text-slate-400">Last Incident</p>
              <p className="mt-1.5 text-[0.9rem] font-medium text-white">{new Date(device.last_incident_seen).toLocaleString()}</p>
            </div>
          )}
        </div>
      </SectionCard>

      <div className="grid gap-5 xl:grid-cols-[1fr_1fr]">
        {/* Incidents for this device */}
        <SectionCard title="Device Incidents" eyebrow="Operational Context">
          {data.incidents.length ? (
            <div className="space-y-3">
              {data.incidents.slice(0, 15).map((incident) => (
                <Link
                  key={incident.incident_no}
                  href={`/aiops/incidents/${incident.incident_no}`}
                  className="block rounded-[1.05rem] border border-white/10 bg-white/[0.04] p-3.5 transition hover:border-cyan-300/16 hover:bg-cyan-300/[0.05]"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.14em] text-cyan-200/70">{incident.incident_no}</p>
                      <p className="mt-1 text-[0.94rem] font-semibold text-white">{incident.title}</p>
                      <p className="mt-1 text-[0.82rem] text-slate-400">{new Date(incident.last_seen_at).toLocaleString()}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge value={incident.severity} />
                      <StatusBadge value={incident.status} />
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <div className="rounded-[1rem] border border-dashed border-white/12 bg-white/[0.02] p-5 text-center">
              <p className="text-[0.88rem] text-slate-300/75">No incidents recorded for this device.</p>
            </div>
          )}
        </SectionCard>

        {/* Recent events for this device */}
        <SectionCard
          title="Recent Events"
          eyebrow="Parsed Syslog"
          actions={
            <Link
              href={`/aiops/logs?incident=`}
              className="inline-flex items-center gap-1 text-[0.78rem] text-slate-400 transition hover:text-cyan-300"
            >
              All Logs <ArrowRight className="h-3 w-3" />
            </Link>
          }
        >
          {data.events.length ? (
            <div className="space-y-3">
              {data.events.slice(0, 15).map((event) => (
                <div key={event.id} className="rounded-[1.05rem] border border-white/10 bg-white/[0.04] p-3.5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <StatusBadge value={event.event_state} />
                      <StatusBadge value={event.severity} />
                      {event.incident_no ? (
                        <Link href={`/aiops/incidents/${event.incident_no}`} className="text-[0.72rem] font-medium text-cyan-200 transition hover:text-cyan-100">
                          {event.incident_no}
                        </Link>
                      ) : null}
                    </div>
                    <p className="text-[0.68rem] text-slate-500">{new Date(event.created_at).toLocaleString()}</p>
                  </div>
                  <p className="mt-2 text-[0.92rem] font-semibold text-white">{event.title}</p>
                  <p className="mt-1 text-[0.85rem] leading-7 text-slate-300">{event.summary}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-[1rem] border border-dashed border-white/12 bg-white/[0.02] p-5 text-center">
              <p className="text-[0.88rem] text-slate-300/75">No syslog events recorded for this device.</p>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
