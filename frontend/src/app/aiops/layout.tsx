import type { ReactNode } from "react";
import { AIOpsShell } from "@/components/aiops/aiops-shell";

export const dynamic = "force-dynamic";

export default function AIOpsLayout({ children }: { children: ReactNode }) {
  return <AIOpsShell>{children}</AIOpsShell>;
}
