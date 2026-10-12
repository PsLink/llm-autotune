/** Values typed into the New campaign form before, newest first, so the image
 *  or the model path of the last campaign is one click away rather than typed
 *  again. Kept in this browser only; the campaigns already on the platform add
 *  theirs (see `withPlatformValues`). Storage can be unavailable (a private
 *  window), and then there is simply no history. */

const KEY = 'autotune_recent_values'
const KEEP = 8

function read(): Record<string, string[]> {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? '{}')
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

export function recentValues(field: string): string[] {
  const values = read()[field]
  return Array.isArray(values) ? values.filter((v) => typeof v === 'string') : []
}

/** Put these values at the front of each field's history. */
export function rememberValues(values: Record<string, string>): void {
  const all = read()
  for (const [field, raw] of Object.entries(values)) {
    const value = raw.trim()
    if (!value) continue
    all[field] = [value, ...(all[field] ?? []).filter((v) => v !== value)].slice(0, KEEP)
  }
  try {
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    /* no storage: no history */
  }
}

/** This browser's history first, then what the platform's campaigns used,
 *  newest first, without repeats. */
export function withPlatformValues(field: string, platform: (string | undefined)[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const value of [...recentValues(field), ...platform]) {
    const v = (value ?? '').trim()
    if (!v || seen.has(v)) continue
    seen.add(v)
    out.push(v)
  }
  return out.slice(0, 12)
}
