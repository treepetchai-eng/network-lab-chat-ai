import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { CodeBlock } from "./code-block";

interface Props {
  content: string;
}

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), "className"],
  },
};

const markdownComponents = {
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="my-3 sm:my-4 overflow-x-auto rounded-2xl sm:rounded-[22px] border border-white/10 bg-white/[0.03] shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] -mx-1 sm:mx-0">
      <table className="w-full border-collapse text-[0.82rem] sm:text-[0.92rem]">{children}</table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead className="border-b border-white/10 bg-white/[0.04]">{children}</thead>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th className="whitespace-nowrap px-4 py-3 text-left text-[0.68rem] font-semibold uppercase tracking-[0.22em] text-slate-400">{children}</th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td className="whitespace-nowrap px-4 py-3 font-mono text-[0.84rem] leading-6 text-slate-200">{children}</td>
  ),
  tr: ({ children }: { children?: React.ReactNode }) => (
    <tr className="border-b border-white/6 last:border-none even:bg-white/[0.02]">{children}</tr>
  ),
  pre: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  code: ({ className, children, ...props }: { className?: string; children?: React.ReactNode }) => {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !match && !className;
    const codeStr = String(children).replace(/\n$/, "");

    if (isInline) {
      return (
        <code className="rounded-full border border-cyan-300/12 bg-cyan-400/10 px-2 py-0.5 text-[0.8rem] text-cyan-50" {...props}>
          {children}
        </code>
      );
    }
    return <CodeBlock>{codeStr}</CodeBlock>;
  },
  p: ({ children }: { children?: React.ReactNode }) => <p className="mb-3 leading-7 tracking-[0.002em] last:mb-0">{children}</p>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className="mb-3 list-disc space-y-2 pl-6">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className="mb-3 list-decimal space-y-2 pl-6">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li className="leading-7 tracking-[0.002em]">{children}</li>,
  strong: ({ children }: { children?: React.ReactNode }) => <strong className="font-semibold text-white">{children}</strong>,
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} className="text-cyan-100 underline decoration-cyan-300/30 underline-offset-4 transition-colors hover:decoration-cyan-200/70" target="_blank" rel="noopener noreferrer">{children}</a>
  ),
  h1: ({ children }: { children?: React.ReactNode }) => <h1 className="mb-4 mt-5 text-[1.32rem] font-semibold tracking-[-0.018em] text-white">{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 className="mb-3 mt-4 text-[1.14rem] font-semibold tracking-[-0.014em] text-white">{children}</h2>,
  h3: ({ children }: { children?: React.ReactNode }) => <h3 className="mb-2 mt-4 text-[1rem] font-semibold tracking-[-0.01em] text-white">{children}</h3>,
  blockquote: ({ children }: { children?: React.ReactNode }) => (
    <blockquote className="my-4 rounded-r-2xl border-l-2 border-cyan-300/30 bg-cyan-400/6 px-4 py-3 text-slate-300">{children}</blockquote>
  ),
  hr: () => <hr className="my-5 border-white/10" />,
};

export const MarkdownRenderer = React.memo(function MarkdownRenderer({ content }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
      components={markdownComponents}
    >
      {content}
    </ReactMarkdown>
  );
});
