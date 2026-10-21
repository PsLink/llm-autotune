import type { MetricSpec } from '../api/client'

/** What `/objectives/metrics` offers, as one list: the platform's measures
 *  first, then the raw LLMBench keys they translate, still accepted. */
export function metricOptions(data: { metrics?: MetricSpec[]; measure_options?: MetricSpec[] }): MetricSpec[] {
  return [
    ...(data.measure_options ?? []),
    ...(data.metrics ?? []).map((m) => ({ ...m, group: `LLMBench key · ${m.group}` })),
  ]
}

const REF = /^(?:([^/]+)\/)?([a-z0-9_]+(?:@[a-z0-9]+)?)(?:\[([^\]]+)\])?(?::([a-z0-9]+))?$/

/** The spec of any name an objective can carry: a listed key, or a measure
 *  reference with a scenario, prompt bucket or sweep point around a listed
 *  one (`replay/ttft_ms@p90[32k_64k]`), labelled with those parts. */
export function specFor(key: string, specs: MetricSpec[]): MetricSpec | undefined {
  const exact = specs.find((m) => m.key === key)
  if (exact || key.includes('.')) return exact
  const match = REF.exec(key)
  if (!match) return undefined
  const [, scenario, base, qualifier, point] = match
  const spec = specs.find((m) => m.measure && m.key === base)
    ?? specs.find((m) => m.measure && m.key === base.replace(/@mean$/, ''))
  if (!spec) return undefined
  const extras = [qualifier && `prompts ${qualifier.replace('_', '–')}`, point, scenario && `in ${scenario}`]
    .filter(Boolean)
  return { ...spec, key, label: extras.length ? `${spec.label} (${extras.join(', ')})` : spec.label }
}
