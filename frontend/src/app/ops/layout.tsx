import { OpsIdentityProvider } from "@/components/ops/ops-identity-context";
import { OpsShell } from "@/components/ops/ops-shell";

export default function OpsLayout({ children }: { children: React.ReactNode }) {
  return (
    <OpsIdentityProvider>
      <OpsShell>{children}</OpsShell>
    </OpsIdentityProvider>
  );
}
