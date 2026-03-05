type SnapshotEnvelope<T> = {
  updatedAt: string;
  data: T;
};

function parseSnapshot<T>(raw: string | null): SnapshotEnvelope<T> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as SnapshotEnvelope<T>;
    if (!parsed || typeof parsed !== "object" || !("data" in parsed)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function readRouteSnapshot<T>(key: string): SnapshotEnvelope<T> | null {
  try {
    return parseSnapshot<T>(localStorage.getItem(key));
  } catch {
    return null;
  }
}

export function writeRouteSnapshot<T>(key: string, data: T): void {
  try {
    const envelope: SnapshotEnvelope<T> = {
      updatedAt: new Date().toISOString(),
      data,
    };
    localStorage.setItem(key, JSON.stringify(envelope));
  } catch {
    // ignore quota / availability errors
  }
}

