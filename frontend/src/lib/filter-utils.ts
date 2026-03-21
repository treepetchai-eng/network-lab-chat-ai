export function buildSearchText(parts: Array<string | number | null | undefined>): string {
  return parts
    .filter((value): value is string | number => value !== null && value !== undefined && `${value}`.trim().length > 0)
    .join(" ")
    .toLowerCase();
}

export function uniqueSortedStrings(values: Array<string | null | undefined>): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => value?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  ).sort((left, right) => left.localeCompare(right));
}
