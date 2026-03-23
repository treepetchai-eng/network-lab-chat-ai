import { ApprovalsClient } from "@/components/aiops/approvals-client";
import { fetchApprovals } from "@/lib/aiops-api";

export default async function ApprovalsPage() {
  const approvals = await fetchApprovals();
  return <ApprovalsClient initialApprovals={approvals} />;
}
