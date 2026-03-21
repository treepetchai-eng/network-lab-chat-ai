import type { ReactNode } from "react";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  breadcrumb,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
}) {
  return (
    <div className="border-b border-white/8 px-6 py-4 sm:px-8">
      {breadcrumb && <div className="mb-1.5">{breadcrumb}</div>}
      <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          {eyebrow && (
            <p className="text-[0.66rem] uppercase tracking-[0.18em] text-cyan-100/50">{eyebrow}</p>
          )}
          <h1 className="text-lg font-semibold tracking-tight text-white">{title}</h1>
          {description ? <p className="mt-0.5 text-sm text-slate-400">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
