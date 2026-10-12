/** A list's filters, kept in the page's URL (`?status=done&status=paused&q=qwen`)
 *  so a filtered list survives a reload and can be shared as a link. */
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string')
  return typeof value === 'string' && value ? [value] : []
}

export function useQueryFilters(keys: string[]) {
  const route = useRoute()
  const router = useRouter()
  const selected = ref<Record<string, string[]>>(
    Object.fromEntries(keys.map((k) => [k, asList(route.query[k])])))
  const search = ref(typeof route.query.q === 'string' ? route.query.q : '')

  watch([selected, search], () => {
    const query: Record<string, string | string[]> = {}
    for (const key of keys) if (selected.value[key]?.length) query[key] = selected.value[key]
    if (search.value.trim()) query.q = search.value.trim()
    void router.replace({ query })
  }, { deep: true })

  /** Does `value` pass the field's filter (an empty filter passes all)? */
  function passes(key: string, value: string | string[]): boolean {
    const wanted = selected.value[key]
    if (!wanted?.length) return true
    return (Array.isArray(value) ? value : [value]).some((v) => wanted.includes(v))
  }

  return { selected, search, passes }
}

/** Options for a filter, from the rows themselves, with how many rows have each. */
export function countOptions<T>(
  rows: T[], valueOf: (row: T) => string, labelOf: (value: string) => string = (v) => v,
  order?: string[],
): { value: string; label: string; count: number }[] {
  const counts = new Map<string, number>()
  for (const row of rows) {
    const value = valueOf(row)
    if (value) counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  const values = order
    ? [...order.filter((v) => counts.has(v)), ...[...counts.keys()].filter((v) => !order.includes(v))]
    : [...counts.keys()].sort((a, b) => a.localeCompare(b))
  return values.map((value) => ({ value, label: labelOf(value), count: counts.get(value)! }))
}
