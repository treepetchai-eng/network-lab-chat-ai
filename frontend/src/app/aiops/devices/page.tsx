import Link from "next/link";
import { Router } from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import { fetchDevices } from "@/lib/aiops-api";

export default async function DevicesPage() {
  const devices = await fetchDevices();

  return (
    <SectionCard title="Managed Devices" eyebrow="Inventory">
      {devices.length ? (
        <div className="overflow-hidden rounded-[1.5rem] border border-white/8">
          <table className="min-w-full divide-y divide-white/8 text-left text-sm">
            <thead className="bg-white/[0.04] text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Hostname</th>
                <th className="px-4 py-3 font-medium">IP</th>
                <th className="px-4 py-3 font-medium">Platform</th>
                <th className="px-4 py-3 font-medium">Role</th>
                <th className="px-4 py-3 font-medium">Site</th>
                <th className="px-4 py-3 font-medium">Open Incidents</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/6">
              {devices.map((device) => (
                <tr key={device.hostname} className="bg-white/[0.02] transition hover:bg-cyan-300/[0.04]">
                  <td className="px-4 py-4">
                    <Link href={`/aiops/devices/${encodeURIComponent(device.hostname)}`} className="font-semibold text-cyan-200 transition hover:text-cyan-100">
                      {device.hostname}
                    </Link>
                  </td>
                  <td className="px-4 py-4 text-slate-300">{device.ip_address}</td>
                  <td className="px-4 py-4 text-slate-300">{device.os_platform}</td>
                  <td className="px-4 py-4"><StatusBadge value={device.device_role} /></td>
                  <td className="px-4 py-4 text-slate-300">{device.site}</td>
                  <td className="px-4 py-4">
                    {device.open_incident_count > 0 ? (
                      <span className="inline-flex items-center rounded-full border border-rose-400/30 bg-rose-400/12 px-2.5 py-0.5 text-xs font-semibold text-rose-200">
                        {device.open_incident_count}
                      </span>
                    ) : (
                      <span className="text-slate-500">0</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-[1.2rem] border border-dashed border-white/12 bg-white/[0.02] p-8 text-center">
          <Router className="mx-auto h-8 w-8 text-slate-500" />
          <p className="mt-3 text-[0.92rem] font-medium text-slate-200">No devices found</p>
          <p className="mt-2 text-[0.85rem] text-slate-400">
            Devices are synced from inventory on backend startup. Check that the inventory CSV is present and the backend has bootstrapped.
          </p>
        </div>
      )}
    </SectionCard>
  );
}
