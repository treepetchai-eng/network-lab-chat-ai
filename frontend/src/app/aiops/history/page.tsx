import { HistoryClient } from "@/components/aiops/history-client";
import { fetchHistory } from "@/lib/aiops-api";

export default async function HistoryPage() {
  const history = await fetchHistory();
  return <HistoryClient initialHistory={history} />;
}
