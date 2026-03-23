export default function Loading() {
  return (
    <div className="space-y-5">
      <div className="animate-pulse rounded-[1.2rem] border border-white/8 bg-white/[0.03] p-6">
        <div className="h-3 w-20 rounded bg-white/10" />
        <div className="mt-3 h-6 w-48 rounded bg-white/10" />
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 rounded-[0.95rem] bg-white/[0.04]" />
          ))}
        </div>
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="animate-pulse rounded-[1.2rem] border border-white/8 bg-white/[0.03] p-6">
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
