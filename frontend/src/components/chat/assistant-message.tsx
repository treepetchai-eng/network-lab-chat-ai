import React, { useState } from "react";
import { CheckCircle2, Copy, Check, Network, Radar, TriangleAlert } from "lucide-react";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";
import { CollapsibleStep } from "@/components/stream/collapsible-step";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/types";

interface Props {
  message: ChatMessage;
}

function extractCommandContext(message: ChatMessage): { command?: string; host?: string } {
  const cliStep = [...message.steps].reverse().find((step) => step.toolName === "run_cli");
  if (!cliStep) return {};
  const cleaned = cliStep.name.replace(/^FAILED\s+—\s+/, "");
  const [command, host] = cleaned.split(" @ ").map((part) => part?.trim());
  return {
    command: command || undefined,
    host: host || undefined,
  };
}

function summarizeOutcome(message: ChatMessage): {
  title: string;
  tone: "good" | "warn";
  lines: string[];
} | null {
  const content = message.content;
  if (!content) return null;

  // Only show assessment summaries when actual tool execution happened
  if (message.steps.length === 0) return null;

  const lower = content.toLowerCase();
  const { command, host } = extractCommandContext(message);
  const hasManySteps = message.steps.length > 1;
  const relationshipLike =
    /relationship|topology|dependency|adjacency|neighbor|ความสัมพันธ์|โทโพโลยี|เชื่อมต่อกัน|พึ่งพา/i.test(content) ||
    message.steps.some((step) => /cdp|lldp|interface brief|show ip route|show running-config \| section/i.test(step.name));

  const actualBGPCheck =
    (command?.toLowerCase().includes("show ip bgp summary") ?? false) ||
    (!relationshipLike && /bgp/.test(lower) && /(established|prefix|neighbor|peer|session|as )/i.test(content));

  if (relationshipLike) {
    const needsReview = /partial|inference|gap|limit|ยังไม่ครบ|ยังไม่สมบูรณ์|ต้องดูเพิ่ม|review/i.test(content);
    return {
      title: "Relationship Analysis",
      tone: needsReview ? "warn" : "good",
      lines: [
        "Check: Topology / dependency",
        needsReview ? "Outcome: Partial map from evidence" : "Outcome: Relationship map summarized",
      ],
    };
  }

  if (actualBGPCheck) {
    const healthy = /(established|ทำงานปกติ|healthy|normal)/i.test(content);
    return {
      title: host ? `BGP Assessment · ${host}` : "BGP Assessment",
      tone: healthy ? "good" : "warn",
      lines: [
        command ? `Check: ${command}` : "Check: BGP",
        healthy ? "Outcome: Healthy" : "Outcome: Review evidence",
      ],
    };
  }

  const reachabilityMatch =
    /reachable|reachability|ssh|เข้าได้|เข้าไม่ได้|ครบทุกตัว|all devices/i.test(content)
      ? content.match(/(\d+)\s*\/\s*(\d+)/)
      : null;
  if (reachabilityMatch) {
    const current = Number(reachabilityMatch[1]);
    const total = Number(reachabilityMatch[2]);
    return {
      title: "Execution Summary",
      tone: current === total ? "good" : "warn",
      lines: [
        `Outcome: ${current}/${total} checks succeeded`,
        current === total ? "Risk: None observed" : `Risk: ${total - current} checks need review`,
      ],
    };
  }

  if (hasManySteps && (command || host)) {
    return {
      title: "Operational Summary",
      tone: /error|timeout|failed|เข้าไม่ได้|issue/i.test(content) ? "warn" : "good",
      lines: [
        command ? `Check: ${command}` : "Check: Executed command",
      ],
    };
  }

  return null;
}

export const AssistantMessage = React.memo(function AssistantMessage({ message }: Props) {
  const summary = summarizeOutcome(message);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-start gap-2 sm:gap-3">
      <div className="mt-1 flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-full border border-cyan-300/18 bg-[linear-gradient(135deg,rgba(8,42,64,0.95),rgba(12,132,176,0.82))] shadow-[0_0_18px_rgba(34,211,238,0.16)]">
        <Network className="h-3 w-3 sm:h-3.5 sm:w-3.5 text-white" />
      </div>

      <div className="min-w-0 flex-1 space-y-2 sm:space-y-3">
        {/* Label */}
        <div className="flex items-center justify-between">
          <span className="text-[0.7rem] sm:text-[0.74rem] font-medium text-cyan-200/60">Network Copilot</span>
          <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1 rounded-lg border border-white/8 bg-white/[0.03] px-2 py-0.5 text-[0.62rem] text-slate-500 transition-all hover:border-cyan-300/16 hover:bg-white/[0.06] hover:text-slate-300"
          >
            {copied ? (
              <><Check className="h-2.5 w-2.5 text-emerald-400" /><span className="text-emerald-400">Copied</span></>
            ) : (
              <><Copy className="h-2.5 w-2.5" /><span>Copy</span></>
            )}
          </button>
        </div>
        {summary ? (
          <div
            className={cn(
              "overflow-hidden rounded-2xl sm:rounded-[20px] border px-3 py-2.5 sm:px-3.5 sm:py-3 shadow-[0_12px_30px_rgba(2,7,18,0.16)]",
              summary.tone === "good"
                ? "border-emerald-300/16 bg-[linear-gradient(180deg,rgba(8,35,32,0.55),rgba(7,20,22,0.68))]"
                : "border-amber-300/16 bg-[linear-gradient(180deg,rgba(43,30,10,0.48),rgba(18,14,10,0.72))]",
            )}
          >
            <div className="flex items-start gap-2.5">
              <span
                className={cn(
                  "mt-0.5 flex h-8 w-8 items-center justify-center rounded-full border",
                  summary.tone === "good"
                    ? "border-emerald-200/18 bg-emerald-300/12 text-emerald-100"
                    : "border-amber-200/18 bg-amber-300/12 text-amber-100",
                )}
              >
                {summary.tone === "good" ? <CheckCircle2 className="h-3.5 w-3.5" /> : <TriangleAlert className="h-3.5 w-3.5" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Radar className="h-3 w-3 text-cyan-100/72" />
                  <span className="text-[0.66rem] uppercase tracking-[0.16em] text-cyan-100/54">Assessment</span>
                </div>
                <h3 className="mt-1 text-[0.84rem] sm:text-[0.92rem] font-semibold tracking-[0.01em] text-white">{summary.title}</h3>
                <div className={cn("mt-2 grid gap-1.5 sm:gap-2", summary.lines.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
                  {summary.lines.map((line) => (
                    <div key={line} className="rounded-xl sm:rounded-[14px] border border-white/8 bg-white/[0.04] px-2 py-1 sm:px-2.5 sm:py-1.5 text-[0.72rem] sm:text-[0.76rem] text-slate-200">
                      {line}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : null}
        {message.steps.length > 0 && (
          <div className="rounded-2xl sm:rounded-[20px] border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,27,0.72),rgba(8,13,23,0.58))] p-1 sm:p-1.5 shadow-[0_12px_34px_rgba(2,7,18,0.16)]">
            {message.steps.map((step) => (
              <CollapsibleStep key={step.id} step={step} />
            ))}
          </div>
        )}
        {message.content && (
          <div className="ui-copy text-slate-200">
            <MarkdownRenderer content={message.content} />
          </div>
        )}
      </div>
    </div>
  );
});
