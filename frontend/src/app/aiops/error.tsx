"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

export default function AIOpsError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex min-h-[50vh] items-center justify-center">
      <div className="max-w-lg rounded-[1.2rem] border border-rose-400/20 bg-rose-400/[0.06] p-8 text-center">
        <AlertTriangle className="mx-auto h-10 w-10 text-rose-300" />
        <h2 className="mt-4 text-lg font-semibold text-rose-100">Failed to load page</h2>
        <p className="mt-2 text-[0.88rem] leading-7 text-rose-200/70">
          {error.message || "An unexpected error occurred while loading AIOps data."}
        </p>
        <p className="mt-2 text-[0.82rem] text-rose-200/50">
          Check that the backend API is running and accessible, then try again.
        </p>
        <button
          onClick={reset}
          className="mt-5 inline-flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-400/[0.1] px-4 py-2.5 text-[0.88rem] font-medium text-rose-100 transition hover:bg-rose-400/[0.15]"
        >
          <RefreshCw className="h-4 w-4" />
          Try Again
        </button>
      </div>
    </div>
  );
}
