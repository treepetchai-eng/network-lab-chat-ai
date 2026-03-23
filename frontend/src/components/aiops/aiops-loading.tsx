export function AIOpsLoading() {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="animate-pulse rounded-[1rem] border border-white/8 bg-white/[0.035] px-4 py-4">
            <div className="h-3 w-20 rounded bg-white/10" />
            <div className="mt-3 h-6 w-12 rounded bg-white/10" />
            <div className="mt-2 h-3 w-full rounded bg-white/6" />
          </div>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="animate-pulse rounded-[1.2rem] border border-white/8 bg-white/[0.03] p-5">
            <div className="h-4 w-32 rounded bg-white/10" />
            <div className="mt-4 space-y-3">
              {Array.from({ length: 3 }).map((_, j) => (
                <div key={j} className="h-16 rounded-[1rem] bg-white/[0.04]" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AIOpsTableLoading({ rows = 5 }: { rows?: number }) {
  return (
    <div className="animate-pulse rounded-[1.2rem] border border-white/8 bg-white/[0.03] p-5">
      <div className="h-4 w-40 rounded bg-white/10" />
      <div className="mt-4 space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-12 rounded-lg bg-white/[0.04]" />
        ))}
      </div>
    </div>
  );
}
