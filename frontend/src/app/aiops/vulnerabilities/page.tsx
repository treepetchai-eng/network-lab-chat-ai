import { Suspense } from "react";
import { fetchVulnerabilitySummary } from "@/lib/aiops-api";
import { VulnerabilitiesClient } from "./vulnerabilities-client";

export default async function VulnerabilitiesPage() {
  let data = null;
  let error: string | null = null;
  try {
    data = await fetchVulnerabilitySummary();
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load";
  }

  return (
    <Suspense>
      <VulnerabilitiesClient initialData={data} initialError={error} />
    </Suspense>
  );
}
