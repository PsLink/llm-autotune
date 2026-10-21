# Measures

A **measure** is the platform's name for one thing a benchmark found, such as
output tokens per minute per GPU, time to first token at p99, or success rate.
Each measure has the same unit, direction and meaning whichever LLMBench
module reported it. Objectives, redlines, reports and policies all use these
names, so nobody needs to know what each module calls a number internally.

LLMBench names a number after the module that produced it, and the names
collide:

| LLMBench key | in a guidellm sweep | in a replay | in opencompass |
|---|---|---|---|
| `output_tps_mean` | aggregate output rate of the level | output speed of **one request** | — |
| `output_tps` | aggregate output rate | — | output speed of **one request** |
| `input_tps_*` | aggregate prompt rate (counts overlapping requests twice) | prefill speed of **one request** | — |
| `uptime` | 1 − errored ÷ measured | succeeded ÷ dispatched | — |
| `*_card_norm` | rate × 8 ÷ cards (**per 8 cards**) | same | — |

Raw keys are still stored with every result and can still be used. An
objective written with a raw key (`replay.output_tpm_card_norm`) gives exactly
the same value it always did.

## Naming a measure

```text
[<scenario>/]<measure>[@<stat>][[<qualifier>]][:<point>]

output_tpm_per_gpu              the main scenario, at its operating point
ttft_ms@p99                     a statistic
ttft_ms@p90[32k_64k]            a replay's 32k–64k prompt-length bucket
accuracy[gpqa_diamond]          one task of an accuracy suite
request_output_tps:c1           a sweep's concurrency-1 level instead of the operating point
replay/success_rate             a named scenario (a module, `name#2` for its second run)
```

- **Scenario.** Each module in a benchmark is a scenario. Without one named,
  the main scenario is used: the first throughput module (sweep, guidellm or
  replay) in the benchmark's order, otherwise the first module.
- **Statistics.** A measure that takes a statistic says which ones exist.
  Latencies have no default, so `ttft_ms` alone is refused. Per-request speeds
  default to the mean. **Modules do not report the same percentiles.** A
  statistic is used only if the module itself reports it; it is never
  interpolated from neighbouring values or filled with 0. The platform refuses
  a reference to a statistic the benchmark never reports when the objective is
  written, not after a night of runs scores `None`. No module reports p95.
- **Missing values.** A value missing from a run (an empty prompt bucket, a
  judge that was turned off) is absent. An absent value fails any redline on
  it, because a limit that was not measured cannot be certified.
- `uptime` is accepted as another name for `success_rate`.

## In an objective

An objective's target and its redlines can name measures and raw keys,
mixed freely:

```json
{
  "target_metric": "output_tpm_per_gpu",
  "redlines": [
    {"metric": "success_rate", "op": ">=", "value": 0.99},
    {"metric": "ttft_ms@p90[32k_64k]", "op": "<=", "value": 8000},
    {"metric": "functional_acceptance/pass_rate", "op": ">=", "value": 0.99}
  ]
}
```

- A latency target is minimised by default, a throughput target maximised.
  The measure's catalogue entry decides.
- Saving an objective refuses a malformed reference, such as an unknown
  measure or a statistic the measure doesn't have.
- The campaign preflight refuses a target or redline that the chosen
  benchmark's modules never report (`itl_ms@p99` on a replay, `:c8` on
  anything but a sweep).
- The Baseline Hub gate reads `ttft_ms@p50/p90/p99` and `request_output_tps`
  redlines the same way as their raw-key forms.
- `GET /api/objectives/metrics` returns the catalogue as `measures`, and as
  pickable references (one per measure and statistic) as `measure_options`.
- The objective editor lists measures first, grouped by what they are, then
  the raw keys. A reference with a module, a prompt bucket or a sweep point
  (`replay/ttft_ms@p90[32k_64k]`) can be typed into the same field.
- The report API's results (`GET /api/agent/v1/runs/{id}/results`) carry
  `measures` next to the raw `metrics`.

## For a policy

The policy API (contract 1.3) speaks measures too:

- `GET /api/policy/v1/benchmarks/{id}` returns `measures`, one entry per
  module, with its operating point, its other points and every key it can
  report.
- The session manifest carries the catalogue (`measures`) and, for each
  benchmark suite, its `workload`: what each module sends and what its
  measures describe.

See [the policy contract](policy-contract.md#benchmarks).

## The operating point

A scenario's measures describe one operating point unless a reference names
another point:

- **Sweep, grid mode:** the level with the **highest total throughput** among
  the levels that met the sweep's own SLO (per-request output-speed floor plus
  TTFT cap). That is not always the highest passing concurrency. Past the
  knee, a higher level can pass the SLO while delivering less. If no level met
  the SLO, the lowest level is used, with `meets_slo` = 0, which is the same
  fallback LLMBench reports. A result from an LLMBench too old to flag each
  level uses the level LLMBench reported.
- **Sweep, auto mode:** the level the search confirmed at full length. The
  search's other levels are short probes, too noisy to choose between.
- **One-level modules and replays:** the run itself.

`:peak` is the level with the highest total throughput, ignoring the SLO.
`:c8` is level 8 of a sweep.

## Per GPU

`*_per_gpu` divides by the GPUs the config occupies (tp × dp × pp), so tp=2
and tp=4 are compared on what one card delivers. LLMBench's `*_card_norm` is
the same rate **per eight cards**. A `*_per_gpu` value is one eighth of the
matching `*_card_norm`.

## The catalogue
### Throughput

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `output_tpm_per_gpu` | tok/min/GPU | higher | Generated tokens per minute of wall clock, divided by the GPUs the config occupies. |
| `input_tpm_per_gpu` | tok/min/GPU | higher | Prompt tokens processed per minute, cached ones included, per GPU. |
| `uncached_input_tpm_per_gpu` | tok/min/GPU | higher | Prompt tokens actually computed (not served from the prefix cache) per minute, per GPU. |
| `cached_input_tpm_per_gpu` | tok/min/GPU | higher | Prompt tokens served from the prefix cache per minute, per GPU. |
| `total_tpm_per_gpu` | tok/min/GPU | higher | Input plus output tokens per minute, per GPU. |
| `output_tpm` | tok/min | higher | Generated tokens per minute for the whole deployment. Rewards using more GPUs; prefer the per-GPU form unless sizing a fixed deployment. |
| `input_tpm` | tok/min | higher | Prompt tokens per minute for the whole deployment, cached ones included. |
| `uncached_input_tpm` | tok/min | higher | Prompt tokens actually computed per minute, whole deployment. |
| `cached_input_tpm` | tok/min | higher | Prompt tokens served from the prefix cache per minute, whole deployment. |
| `total_tpm` | tok/min | higher | Input plus output tokens per minute, whole deployment. |
| `request_rate` | req/s | higher | Requests completed per second of the measurement window, whole deployment. |

### Per request

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `request_output_tps` | tok/s | higher | Output tokens per second one request sees, over its whole life (queueing and prefill included). A sweep reports it as aggregate output ÷ concurrency, a mean only; a replay measures it per request and also reports its spread. |
| `request_prefill_tps` | tok/s | higher | Prompt tokens per second of time to first token, per request. |

### Latency

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `ttft_ms`[prompt bucket] | ms | lower | From sending a request to its first generated token. |
| `itl_ms` | ms | lower | Gap between consecutive streamed tokens. |
| `tpot_ms` | ms | lower | Decode time per generated token after the first. |
| `e2e_latency_ms` | ms | lower | From sending a request to its last token. |

### Health

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `success_rate` | — | higher | Share of requests the service actually received that succeeded (LLMBench calls it uptime). Requests a benchmark never dispatched are not counted against it. |
| `error_rate` | — | lower | Share of requests the service received that failed. |
| `unfinished_rate` | — | lower | Share of requests that returned without a finish marker (the stream just stopped). |
| `truncated_rate` | — | lower | Share of completed requests cut off by the length limit. |
| `not_started_rate` | — | lower | Share of the workload the benchmark never dispatched because its time ran out. Above zero, the throughput describes only part of the workload. |
| `meets_slo` | — | higher | 1 when the operating point held the benchmark's own service target (a sweep's per-request speed floor and TTFT cap), else 0. |
| `requests_total` | req | — | Requests the benchmark meant to send (a sweep: those measured in the window). |
| `requests_completed` | req | — | Requests that succeeded. |
| `requests_failed` | req | — | Requests that errored. |
| `requests_not_started` | req | — | Requests never dispatched. |
| `requests_unfinished` | req | — | Requests that returned without a finish marker. |
| `requests_truncated` | req | — | Requests cut off by the length limit. |
| `requests_runaway` | req | — | Requests the client had to stop because the server generated past the length it was given, or that ended repeating one token. |

### Workload

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `concurrency` | — | — | Requests in flight at the operating point. |
| `prompt_tokens_mean` | tok | — | Mean prompt tokens per request (a sweep: the configured length). |
| `completion_tokens_mean` | tok | — | Mean generated tokens per request (a sweep: the configured length). |
| `cache_hit_rate` | — | higher | Share of prompt tokens served from the prefix cache, token-weighted. |
| `window_s` | s | — | Length of the window the numbers describe. |
| `prompt_bucket_requests`[prompt bucket] | req | — | Completed requests whose prompt fell in the bucket — read it before trusting the bucket's TTFT. |

### Quality

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `pass_rate` | — | higher | Share of functional checks passed. |
| `tool_call_success_rate` | — | higher | Share of tool-call requests answered with a valid tool call. |
| `hallucination_rate` | — | lower | Share of judged answers with a clear hallucination. |
| `answer_quality_rate` | — | higher | Share of judged answers rated good or acceptable. |
| `poor_answer_rate` | — | lower | Share of judged answers rated poor (off-topic, empty, broken format). |
| `long_output_completion_rate` | — | higher | How much of a requested long output the model produced before stopping. |
| `accuracy`[task] | — | higher | Share of a task's questions answered correctly. |

### Score

| Measure | Unit | Better | Meaning |
|---|---|---|---|
| `score` | — | higher | The module's own score under the benchmark's weights. For a replay it is a value-weighted token rate; for a quality suite a weighted accuracy. Changes when the benchmark's weights are edited. |
| `score_per_gpu` | — | higher | The module score divided by the GPUs the config occupies — only for a module whose score is a throughput (a replay). |

## Which module reports what

A blank cell means the module never reports the measure. The statistics listed are the only ones the module reports.

| Measure | sweep | guidellm | replay | agentic | opencompass | functional | tool call | halluc. | truncation |
|---|---|---|---|---|---|---|---|---|---|
| `output_tpm_per_gpu` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `input_tpm_per_gpu` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `uncached_input_tpm_per_gpu` |  |  | ✓ |  |  |  |  |  |  |
| `cached_input_tpm_per_gpu` |  |  | ✓ |  |  |  |  |  |  |
| `total_tpm_per_gpu` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `output_tpm` | ✓ | ✓ | ✓ |  | ✓ |  |  |  |  |
| `input_tpm` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `uncached_input_tpm` |  |  | ✓ |  |  |  |  |  |  |
| `cached_input_tpm` |  |  | ✓ |  |  |  |  |  |  |
| `total_tpm` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `request_rate` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `request_output_tps` | mean | mean | mean, p10, p50, p90 |  | mean |  |  |  |  |
| `request_prefill_tps` |  |  | mean, p10, p50, p90 |  |  |  |  |  |  |
| `ttft_ms` | mean, p50, p90, p99 | mean, p50, p90, p99 | mean, p50, p90, p99 (+ buckets) | mean, p50, p99 | mean |  |  |  |  |
| `itl_ms` | mean, p50, p90, p99 | mean, p50, p90, p99 |  | mean, p50, p99 |  |  |  |  |  |
| `tpot_ms` | mean, p50, p90, p99 | mean, p50, p90, p99 |  |  | mean |  |  |  |  |
| `e2e_latency_ms` |  |  | p50, p99 |  |  |  |  |  |  |
| `success_rate` | ✓ | ✓ | ✓ | ✓ |  |  |  |  |  |
| `error_rate` | ✓ | ✓ | ✓ | ✓ |  |  | ✓ |  | ✓ |
| `unfinished_rate` |  |  | ✓ |  |  |  |  |  |  |
| `truncated_rate` |  |  | ✓ |  |  |  |  |  | ✓ |
| `not_started_rate` |  |  | ✓ |  |  |  |  |  |  |
| `meets_slo` | ✓ |  |  |  |  |  |  |  |  |
| `requests_total` | ✓ | ✓ | ✓ | ✓ |  |  | ✓ | ✓ | ✓ |
| `requests_completed` | ✓ | ✓ | ✓ |  |  |  | ✓ | ✓ |  |
| `requests_failed` | ✓ | ✓ | ✓ | ✓ |  |  | ✓ | ✓ |  |
| `requests_not_started` |  |  | ✓ |  |  |  |  |  |  |
| `requests_unfinished` |  |  | ✓ | ✓ |  |  |  |  |  |
| `requests_truncated` |  |  | ✓ |  |  |  | ✓ |  |  |
| `requests_runaway` |  |  | ✓ |  |  |  |  |  |  |
| `concurrency` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `prompt_tokens_mean` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `completion_tokens_mean` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `cache_hit_rate` |  |  | ✓ |  |  |  |  |  |  |
| `window_s` | ✓ | ✓ | ✓ |  |  |  |  |  |  |
| `prompt_bucket_requests` |  |  | ✓ |  |  |  |  |  |  |
| `pass_rate` |  |  |  |  |  | ✓ |  |  |  |
| `tool_call_success_rate` |  |  |  |  |  |  | ✓ |  |  |
| `hallucination_rate` |  |  | ✓ |  |  |  |  | ✓ |  |
| `answer_quality_rate` |  |  | ✓ |  |  |  |  |  |  |
| `poor_answer_rate` |  |  | ✓ |  |  |  |  |  |  |
| `long_output_completion_rate` |  |  |  |  |  |  |  |  | ✓ |
| `accuracy` |  |  |  |  | ✓ |  |  |  |  |
| `score` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `score_per_gpu` |  |  | ✓ |  |  |  |  |  |  |

Modules this table doesn't cover still provide their `score`, and their raw
keys can still be used. A build of LLMBench that names a module differently
maps the name onto one of the families above with
`AUTOTUNE_LLMBENCH_MODULE_FAMILIES` (JSON, e.g. `{"my_sweep": "perf_guidellm_sweep"}`).
