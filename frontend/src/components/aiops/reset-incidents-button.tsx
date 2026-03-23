"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Trash2 } from "lucide-react";
import { resetIncidents } from "@/lib/aiops-api";

export function ResetIncidentsButton() {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  const handleReset = () => {
    if (!window.confirm("Delete all incidents, logs, events, proposals, and executions for a fresh lab test?")) {
      return;
    }
    setMessage(null);
    startTransition(async () => {
      try {
        const result = await resetIncidents();
        setMessage(
          `Removed ${result.incidents_removed} incidents, ${result.events_removed} events, and ${result.raw_logs_removed} raw logs.`,
        );
        router.refresh();
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Reset failed");
      }
    });
  };

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handleReset}
        disabled={pending}
        className="inline-flex items-center gap-1.5 rounded border border-rose-500/25 bg-rose-500/[0.08] px-2.5 py-1.5 text-[0.73rem] font-medium text-rose-300 transition hover:border-rose-500/40 hover:bg-rose-500/[0.14] disabled:opacity-50"
      >
        <Trash2 className="h-4 w-4" />
        Reset Incident Data
      </button>
      {message ? <p className="text-[0.72rem] text-slate-500">{message}</p> : null}
    </div>
  );
}
