import { cn } from "@/lib/utils";

export interface OpsTab {
  id: string;
  label: string;
  badge?: number | string;
}

interface OpsTabsProps {
  tabs: OpsTab[];
  activeTab: string;
  onChange: (id: string) => void;
}

export function OpsTabs({ tabs, activeTab, onChange }: OpsTabsProps) {
  return (
    <div className="border-b border-white/8">
      <div className="flex gap-1 px-6 sm:px-8">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => onChange(tab.id)}
            className={cn(
              "flex items-center gap-2 border-b-2 px-3 py-3 text-sm font-medium transition",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/35",
              activeTab === tab.id
                ? "border-cyan-400 text-white"
                : "border-transparent text-slate-400 hover:text-slate-200",
            )}
          >
            {tab.label}
            {tab.badge !== undefined && tab.badge !== 0 && (
              <span className={cn(
                "min-w-[1.25rem] rounded-full px-1.5 py-0.5 text-[0.65rem] font-semibold leading-none",
                activeTab === tab.id
                  ? "bg-cyan-400/20 text-cyan-300"
                  : "bg-white/[0.06] text-slate-400",
              )}>
                {tab.badge}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
