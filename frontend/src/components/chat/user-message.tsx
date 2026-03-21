import React from "react";
import { User } from "lucide-react";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";

interface Props {
  content: string;
}

export const UserMessage = React.memo(function UserMessage({ content }: Props) {
  return (
    <div className="flex items-start justify-end gap-2 sm:gap-3">
      <div className="max-w-[min(85%,36rem)] sm:max-w-[min(72%,36rem)] rounded-2xl sm:rounded-[26px] border border-cyan-300/14 bg-[linear-gradient(135deg,rgba(8,30,48,0.94),rgba(6,108,161,0.76))] px-3.5 py-3 sm:px-5 sm:py-4 ui-copy text-cyan-50 shadow-[0_18px_44px_rgba(2,7,18,0.14)]">
        <MarkdownRenderer content={content} />
      </div>
      <div className="mt-1 flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-full border border-cyan-300/12 bg-[linear-gradient(135deg,rgba(15,30,52,0.9),rgba(20,60,90,0.7))] shadow-[0_0_14px_rgba(34,211,238,0.08)]">
        <User className="h-3 w-3 sm:h-3.5 sm:w-3.5 text-cyan-200/70" />
      </div>
    </div>
  );
});
