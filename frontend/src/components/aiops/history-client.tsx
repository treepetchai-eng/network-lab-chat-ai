"use client";

import Link from "next/link";
import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { SectionCard } from "@/components/aiops/section-card";
import { StatusBadge } from "@/components/aiops/status-badge";
import type { AIOpsIncident } from "@/lib/aiops-types";

const PAGE_SIZE = 15;

function fmt(v: string | null | undefined) {
  return v ? new Date(v).toLocaleString() : "—";
}

function duration(opened: string, resolved: string | null | undefined) {
  if (!resolved) return null;
  const ms = new Date(resolved).getTime() - new Date(opened).getTime();
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${hrs}h ${rem}m` : `${hrs}h`;
}

export function HistoryClient({ initialHistory }: { initialHistory: AIOpsIncident[] }) {
  const [page, setPage] = useState(1);
  const history = initialHistory;

  const totalPages = Math.max(1, Math.ceil(history.length / PAGE_SIZE));
  const safePage   = Math.min(page, totalPages);
  const paged      = history.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <SectionCard title="Incident History" eyebrow="Resolved" noPadding>
      {paged.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="border-b border-white/[0.07]">
                <tr>
                  {["Incident","Severity","Device","Resolution","Duration","Resolved"].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left text-[0.68rem] font-semibold uppercase tracking-widest text-slate-600">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {paged.map((inc) => (
                  <tr key={inc.incident_no} className="transition hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <Link href={`/aiops/incidents/${inc.incident_no}`} className="block">
                        <p className="text-[0.65rem] font-semibold uppercase tracking-widest text-slate-600">{inc.incident_no}</p>
                        <p className="mt-0.5 text-[0.82rem] font-semibold text-slate-200">{inc.title}</p>
                      </Link>
                    </td>
                    <td className="px-4 py-3"><StatusBadge value={inc.severity} showDot /></td>
                    <td className="px-4 py-3 font-mono text-[0.77rem] text-slate-400">{inc.primary_hostname ?? inc.primary_source_ip}</td>
                    <td className="px-4 py-3">
                      <span className="text-[0.77rem] text-slate-400">{inc.resolution_type?.replaceAll("_", " ") ?? "—"}</span>
                    </td>
                    <td className="px-4 py-3 text-[0.77rem] text-slate-500">
                      {duration(inc.opened_at, inc.resolved_at) ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-[0.75rem] text-slate-600">{fmt(inc.resolved_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-white/[0.07] px-4 py-3">
              <p className="text-[0.72rem] text-slate-600">{history.length} resolved</p>
              <div className="flex items-center gap-2">
                <button
                  disabled={safePage <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
                <span className="text-[0.72rem] text-slate-500">{safePage} / {totalPages}</span>
                <button
                  disabled={safePage >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded border border-white/8 bg-white/[0.03] p-1.5 text-slate-400 transition hover:bg-white/[0.07] disabled:opacity-30"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="px-4 py-12 text-center">
          <p className="text-[0.84rem] font-medium text-slate-400">No resolved incidents yet</p>
          <p className="mt-1 text-[0.76rem] text-slate-600">History will appear here once incidents complete the lifecycle.</p>
        </div>
      )}
    </SectionCard>
  );
}
