export function isDebugUI(search?: string): boolean {
  if (typeof window === "undefined") return false;
  const query = new URLSearchParams(typeof search === "string" ? search : window.location.search);
  const debugParam = String(query.get("debug") || "").trim().toLowerCase();
  if (debugParam === "1" || debugParam === "true" || debugParam === "yes") return true;
  try {
    const stored = String(window.localStorage.getItem("dl_debug") || "").trim().toLowerCase();
    return stored === "1" || stored === "true" || stored === "yes";
  } catch {
    return false;
  }
}
