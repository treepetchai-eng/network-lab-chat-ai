"use client";

import { motion } from "framer-motion";
import { MessageSquareText } from "lucide-react";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";

interface UserMessageBubbleProps {
  content: string;
}

export function UserMessageBubble({ content }: UserMessageBubbleProps) {
  return (
    <motion.section
      initial={{ opacity: 0, x: 18 }}
      animate={{ opacity: 1, x: 0 }}
      className="ml-auto w-full max-w-sm sm:max-w-md rounded-2xl sm:rounded-[30px] border border-cyan-300/14 bg-[linear-gradient(180deg,rgba(12,20,35,0.92),rgba(10,16,28,0.78))] p-3.5 sm:p-5 text-right shadow-[0_30px_80px_rgba(2,6,16,0.45)] backdrop-blur-2xl"
    >
      <div className="mb-3 sm:mb-4 inline-flex items-center gap-2 rounded-full border border-cyan-300/12 bg-cyan-400/10 px-2.5 py-0.5 sm:px-3 sm:py-1 text-[0.6rem] sm:text-[0.66rem] font-medium uppercase tracking-[0.22em] text-cyan-100/80">
        <MessageSquareText className="h-3.5 w-3.5" />
        Latest Request
      </div>
      <div className="ui-copy rounded-2xl sm:rounded-[24px] border border-cyan-300/14 bg-[linear-gradient(135deg,rgba(9,21,35,0.84),rgba(7,50,92,0.56))] px-3.5 py-3 sm:px-5 sm:py-4 text-left text-cyan-50 shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_0_40px_rgba(34,211,238,0.08)]">
        <MarkdownRenderer content={content} />
      </div>
    </motion.section>
  );
}
