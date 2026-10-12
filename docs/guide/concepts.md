[User guide](README.md) › **Concepts and words**

# Concepts and words

LLM AutoTune uses a few everyday words in a specific sense. This page explains
each one once; the other pages link here when a word first comes up.

## The idea in one picture

```text
 search space ──► candidates ──► runs on leased GPUs ──► LLMBench measures ──► leaderboard
 (settings and     (one config    (the engine started      (the same load         (ranked by the
  values to try)    each)          with that config)        for every run)         objective)
```

You say which model to serve and which engine settings may change. The platform
starts the engine with one combination of settings at a time, on GPUs you lent
it, has LLMBench send each one the same load, and ranks them by the number you
care about. The best one, with its exact launch command, is the result.

## Tuning

**Engine.** The server that runs the model: [sglang](https://github.com/sgl-project/sglang)
or [vLLM](https://github.com/vllm-project/vllm). Its command-line flags (`--tp`,
`--mem-fraction-static`…) are what AutoTune tunes. The platform writes them
without dashes and with underscores: `tp`, `mem_fraction_static`.

**Parameter.** One engine flag that a search space mentions. See
[engine settings you will meet](#engine-settings-you-will-meet) below.

**Config** (configuration). One full set of parameter values, such as
`tp=2, mem_fraction_static=0.85`.

**Search space.** The parameters a campaign may change and the values each may
take. A parameter is *fixed* (one value), *swept* (a list), a *range* (min, max,
step) or *tied* to others (they change together). Details:
[Search spaces](search-spaces.md).

**Candidate.** One config from the search space, waiting to be tried. A space
of `tp: [1, 2]` and `mem_fraction_static: [0.8, 0.85, 0.88]` has 2 × 3 = 6
candidates.

**Run.** One attempt at one candidate: the engine started with that config on
a machine, checked for health, benchmarked, then stopped. A candidate can have
several runs (a retry after a broken machine, or a repeat to confirm a result).
See [Runs and failures](runs.md).

**Objective.** What "better" means: one **target metric**, a **direction**
(maximize throughput, minimize latency), and any number of **redlines**. See
[Objectives](objectives.md).

**Redline.** A limit a config must stay within to count at all, such as
"p99 time to first token ≤ 2000 ms". A config that crosses a redline is out,
however fast it was. A metric the benchmark did not report counts as crossed.

**Campaign.** One search, start to finish: the model, the image, the machines,
the search space, the benchmark, the objective, and when it may run. It copies
the search space and objective when it is created, so editing those later
never changes a campaign that already exists. See
[Creating a campaign](campaigns.md).

**Leaderboard.** A campaign's results, best first, by the objective's target
metric.

**Baseline.** The config production runs today, recorded on the **Baselines**
page. When a campaign has a baseline for its model, the leaderboard shows each
result against it (**vs baseline**, such as `+12%`). See [Baselines](baselines.md).

**Policy.** A search algorithm in a container. Without one, a campaign tries
every candidate in order (**grid**, or exhaustive search). With one, the
policy decides what to try next; the platform still starts, measures and
judges every config. The install comes with `random-search`. See
[Search policies](policies.md).

**Policy session.** One policy container's working life on a campaign:
*starting*, *searching*, *finalizing* (it names its best configs, the
**finalists**), *validating* (the platform measures the finalists itself),
*done*.

**Winner.** The best config that held every redline. Its run page has the
exact launch command and image, which is what you take to production.

## Measuring

**LLMBench.** The benchmark platform installed next to AutoTune. AutoTune
asks it to send load to each run's engine and reads the numbers back.

**Workload** and **benchmark.** The load each run gets. You describe it on a
campaign's **Benchmark** step, and AutoTune creates the matching *benchmark*
on LLMBench (named `autotune-sweep-…` or similar) and locks it, so every
candidate gets exactly the same test.

**Synthetic sweep.** A workload of random prompts of a fixed size, sent at a
few **concurrency** levels one after another. Fast and repeatable, but it
cannot show the effect of prompt caching.

**Replay.** A workload of recorded production requests, sent again. The only
measurement that sees real prompt lengths and real prefix-cache reuse. It
needs a replay dataset on LLMBench.

**Concurrency.** How many requests are in flight at the same time. A sweep at
`1, 4, 16` measures the engine under three loads.

**Requests per slot.** How many requests each concurrent slot sends at one
level: level *c* sends *c* × this many. More requests give steadier numbers and
longer runs.

**Metric names.** Metrics are written `<benchmark module>.<metric>`, such as
`perf_guidellm_sweep.output_tpm_card_norm`. The part before the dot says which
kind of benchmark produced it (`perf_guidellm_sweep` for the synthetic sweep,
`replay` for a replay).

The metrics you will meet first:

| short name | means | unit |
|---|---|---|
| TTFT | *time to first token*: how long until the answer starts | ms |
| ITL, TPOT | *inter-token latency*, *time per output token*: the gap between streamed tokens | ms |
| TPS, TPM | tokens per second, tokens per minute | tok/s, tok/min |
| output / input | generated tokens / prompt tokens | |
| per card (`card_norm`) | divided by the GPUs the config uses, so `tp=1` and `tp=2` compare fairly | tok/min/GPU |
| p50, p90, p99 | the median, and the value 90% or 99% of requests stay under | |

**Per-card throughput.** The default objective ranks by output tokens per
minute *per GPU*. Without dividing by the GPUs used, a config that takes more
cards nearly always wins, and the search turns into "use more hardware".

**Screen and verify.** An optional second stage: every candidate is first
measured with a quick workload (the *screen*), then the best few are measured
again with a slower, more realistic one (the *verify*, usually a replay), and
that second measurement decides. Set under **Benchmark ▸ Advanced**.

**Pre-flight checks.** Checks the platform runs before a campaign starts: on a
Kubernetes cluster, a probe pod on each machine pulls the engine and policy
images and mounts the model path, so a wrong image tag or path shows up
before the first run, not halfway through the night.

## Machines

**Platform cluster.** The Kubernetes cluster that runs AutoTune and LLMBench
themselves. It needs no GPUs.

**GPU cluster.** A Kubernetes cluster whose GPU nodes run the engines. You add
it on **Resources** with a kubeconfig that `deploy/gpu-cluster.sh` writes. It
can be the platform cluster itself.

**Machine.** One place a run can land. On a GPU cluster, each GPU node you
register becomes a machine. A bare-metal box reached over ssh is a machine
too. See [Machines and GPU clusters](machines.md).

**Lease.** Permission for the platform to use a machine. Campaigns only run on
leased machines. **End lease** lets current runs finish and starts nothing new
(the machine is *draining*), then hands it back; **End lease now** stops the
platform's runs at once. The platform only ever stops what it started itself.

**Smoke test.** A quick check that the platform can reach a machine and is
allowed to start pods there.

**Parallel candidates.** Several candidates sharing one machine, each on its
own GPUs. An 8-GPU node running `tp=2` configs can test four at once.

**Node group.** Several machines joined to serve one model across nodes
(multi-node). Most setups never need one.

## Time

**Window.** The hours a campaign may use the machines, such as 23:00 to 08:00
every night, in a timezone you choose. A run that could not finish before the
window closes is not started. A long search continues the next night where it
stopped.

**No window.** A campaign without a window runs only when you start it and
stops when its search is done or you stop it.

**Run now.** Starts a scheduled campaign at once, outside its window. It keeps
going until its search is done or you stop it.

## Engine settings you will meet

These are sglang names; vLLM has close equivalents. Hover the **?** next to a
parameter in the search-space editor for its description.

| parameter | what it changes |
|---|---|
| `tp` | *tensor parallel* size: how many GPUs one copy of the model is split across |
| `dp` | *data parallel* size: independent copies of the model |
| `ep` | *expert parallel* size, for mixture-of-experts (MoE) models |
| `mem_fraction_static` | share of GPU memory reserved for weights and the KV cache; higher means more cache and less headroom |
| `chunked_prefill_size` | how many prompt tokens are processed per step; trades time to first token against decode smoothness |
| `max_running_requests` | cap on requests being decoded at once |
| `schedule_policy` | which waiting request goes next (`fcfs`, `lpm`…) |
| `kv_cache_dtype` | precision of the KV cache; `fp8` roughly doubles how much fits |
| `attention_backend` | which attention kernels run |
| `cuda_graph_max_bs` | largest batch captured as a CUDA graph |

The **KV cache** is the memory that holds every in-flight request's context.
More of it lets more requests run at once; too little and the engine queues or
runs out of memory.

---

[User guide](README.md) · Next: [Your first campaign](first-campaign.md) →
