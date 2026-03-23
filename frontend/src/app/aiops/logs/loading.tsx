import { AIOpsTableLoading } from "@/components/aiops/aiops-loading";

export default function Loading() {
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <AIOpsTableLoading rows={6} />
      <AIOpsTableLoading rows={6} />
    </div>
  );
}
