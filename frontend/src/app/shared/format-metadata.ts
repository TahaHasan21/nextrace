export interface MetadataEntry {
  key: string;
  value: string;
}

/** Turns an event's metadata object into readable key/value rows for
 * display. Falls back to JSON.stringify only for a nested object/array
 * value - never for the metadata blob as a whole. */
export function formatMetadataEntries(
  metadata: Record<string, unknown> | null | undefined,
): MetadataEntry[] {
  if (!metadata) {
    return [];
  }
  return Object.entries(metadata).map(([key, value]) => ({
    key: humanizeKey(key),
    value: formatValue(value),
  }));
}

function humanizeKey(key: string): string {
  return key
    .split('_')
    .map((word) => (word.length > 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ');
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

/** Extracts the "HH:mm" portion of a backend ISO 8601 UTC timestamp
 * (e.g. "2026-09-06T10:01:00Z" -> "10:01") without timezone-conversion
 * ambiguity - the backend always sends UTC "Z" timestamps. */
export function formatTimeOnly(timestamp: string): string {
  return timestamp.slice(11, 16);
}

export function humanizeEventType(eventType: string): string {
  return eventType
    .split('_')
    .map((word) => (word.length > 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ');
}
