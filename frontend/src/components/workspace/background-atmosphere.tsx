"use client";

import { motion } from "framer-motion";

const particles = [
  { top: "8%", left: "10%", delay: 0.2, size: 2 },
  { top: "14%", left: "82%", delay: 1.8, size: 3 },
  { top: "22%", left: "56%", delay: 0.8, size: 2 },
  { top: "28%", left: "18%", delay: 1.4, size: 2 },
  { top: "38%", left: "72%", delay: 2.2, size: 2 },
  { top: "45%", left: "8%", delay: 0.5, size: 3 },
  { top: "54%", left: "38%", delay: 2.7, size: 2 },
  { top: "63%", left: "88%", delay: 1.1, size: 2 },
  { top: "70%", left: "12%", delay: 2.5, size: 2 },
  { top: "82%", left: "66%", delay: 0.9, size: 3 },
  { top: "88%", left: "28%", delay: 1.9, size: 2 },
];

export function BackgroundAtmosphere() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.16),transparent_32%),radial-gradient(circle_at_80%_12%,rgba(56,189,248,0.10),transparent_28%),linear-gradient(180deg,#040812_0%,#07111e_42%,#04070d_100%)]" />
      <div className="absolute inset-0 opacity-50 [background-image:linear-gradient(rgba(95,126,170,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(95,126,170,0.06)_1px,transparent_1px)] [background-size:120px_120px] [mask-image:radial-gradient(circle_at_center,black,transparent_82%)]" />
      <div className="absolute -left-24 top-12 h-[30rem] w-[30rem] rounded-full bg-cyan-400/10 blur-[140px]" />
      <div className="absolute right-[-10rem] top-32 h-[32rem] w-[32rem] rounded-full bg-blue-500/10 blur-[160px]" />
      <div className="absolute left-1/2 top-[16%] h-72 w-[42rem] -translate-x-1/2 rounded-full border border-cyan-300/10 opacity-60 blur-[1px]" />
      <div className="absolute left-[8%] top-[18%] h-96 w-96 rounded-full border border-white/6 opacity-30" />
      <div className="absolute right-[6%] top-[40%] h-[26rem] w-[36rem] rounded-full border border-cyan-300/10 opacity-50 rotate-[8deg]" />
      {particles.map((particle, index) => (
        <motion.span
          key={`${particle.left}-${particle.top}-${index}`}
          className="absolute rounded-full bg-cyan-100/70 shadow-[0_0_18px_rgba(103,232,249,0.6)]"
          style={{ top: particle.top, left: particle.left, width: particle.size, height: particle.size }}
          animate={{ y: [0, -14, 0], opacity: [0.35, 1, 0.35] }}
          transition={{ duration: 5 + index * 0.45, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut", delay: particle.delay }}
        />
      ))}
      <motion.div
        className="absolute left-[20%] top-[58%] h-px w-[18rem] bg-[linear-gradient(90deg,transparent,rgba(103,232,249,0.6),transparent)]"
        animate={{ x: [0, 28, 0], opacity: [0.15, 0.5, 0.15] }}
        transition={{ duration: 7, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute right-[12%] top-[22%] h-px w-[10rem] bg-[linear-gradient(90deg,transparent,rgba(125,211,252,0.55),transparent)]"
        animate={{ x: [0, -22, 0], opacity: [0.1, 0.45, 0.1] }}
        transition={{ duration: 6.2, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut", delay: 1.4 }}
      />
    </div>
  );
}
