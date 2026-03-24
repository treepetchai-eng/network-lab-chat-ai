import Link from "next/link";
import { AlertCircle, ArrowRight } from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { VulnerabilityTab } from "@/components/aiops/vulnerability-tab";
import { fetchDeviceDetail, fetchDeviceVulnerabilities } from "@/lib/aiops-api";
import type { AIOpsDeviceVulnPayload } from "@/lib/aiops-types";

interface DeviceDetailPageProps {
  params: Promise<{ hostname: string }>;
  searchParams: Promise<{ tab?: string }>;
}

export default async function DeviceDetailPage({ params, searchParams }: DeviceDetailPageProps) {
  const { hostname } = await params;
  const { tab = "overview" } = await searchParams;

  let data;
  try {
    data = await fetchDeviceDetail(decodeURIComponent(hostname));
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Unknown error";
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center">
        <AlertCircle className="h-10 w-10 text-rose-400/60" />
        <p className="mt-4 text-[0.95rem] font-semibold text-slate-200">
          Could not load device {decodeURIComponent(hostname)}
        </p>
        <p className="mt-1 text-[0.8rem] text-slate-500">{msg}</p>
        <Link
          href="/aiops/devices"
          className="mt-5 rounded border border-white/10 bg-white/[0.04] px-4 py-2 text-[0.82rem] text-slate-400 transition hover:text-slate-200"
        >
          ← Back to Devices
        </Link>
      </div>
    );
  }

  const device = data.device;

  // Pre-fetch vuln data for the vulnerabilities tab (fail silently)
  let vulnData: AIOpsDeviceVulnPayload | null = null;
  if (tab === "vulnerabilities") {
    try {
      vulnData = await fetchDeviceVulnerabilities(decodeURIComponent(hostname));
    } catch {
      // will be loaded client-side on scan trigger
    }
  }

  const tabs = [
    { key: "overview",        label: "Overview" },
    { key: "vulnerabilities", label: "Vulnerabilities" },
  ];

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

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-white/[0.07]">
        {tabs.map(({ key, label }) => (
          <Link
            key={key}
            href={`/aiops/devices/${encodeURIComponent(device.hostname)}?tab=${key}`}
            className={`px-4 py-2.5 text-[0.82rem] font-medium transition border-b-2 -mb-px ${
              tab === key
                ? "border-cyan-400 text-cyan-300"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>

      {/* Tab content */}
      {tab === "vulnerabilities" ? (
        <VulnerabilityTab hostname={device.hostname} initialData={vulnData} />
      ) : (
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
      )}
    </div>
  );
}
