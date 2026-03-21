export function getStringParam(searchParams: URLSearchParams, key: string, fallback = ""): string {
  return searchParams.get(key) ?? fallback;
}

export function getNumberParam(searchParams: URLSearchParams, key: string, fallback: number): number {
  const raw = searchParams.get(key);
  if (!raw) {
    return fallback;
  }
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function getBooleanParam(searchParams: URLSearchParams, key: string, fallback = false): boolean {
  const raw = searchParams.get(key);
  if (raw === null) {
    return fallback;
  }
  return raw === "1" || raw.toLowerCase() === "true";
}

export function mergeSearchParams(
  searchParams: URLSearchParams,
  updates: Record<string, string | number | boolean | null | undefined>,
): string {
  const next = new URLSearchParams(searchParams.toString());
  for (const [key, value] of Object.entries(updates)) {
    if (value === null || value === undefined || value === "" || value === false) {
      next.delete(key);
      continue;
    }
    next.set(key, typeof value === "boolean" ? "1" : String(value));
  }
  return next.toString();
}
