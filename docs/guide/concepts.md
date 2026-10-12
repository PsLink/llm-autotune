[User guide](README.md) › **Concepts**

# Concepts

The terms used throughout the UI and this guide. Other pages link here when a
term first appears.

## Overview

```text
 search space ──► candidates ──► runs on leased GPUs ──► LLMBench measures ──► leaderboard
 (settings and     (one config    (the engine started      (the same load         (ranked by the
  values to try)    each)          with that config)        for every run)         objective)
```

You specify the model and which engine settings may change. The platform
starts the engine with one combination of settings at a time on leased GPUs,
has LLMBench send each the same load, and ranks the results by the objective.
The result is the best config with its exact engine command.

## Tuning

**Engine.** The inference server that runs the model:
[sglang](https://github.com/sgl-project/sglang) or
[vLLM](https://github.com/vllm-project/vllm). Its command-line flags (`--tp`,
`--mem-fraction-static`…) are the parameters AutoTune tunes. The platform
writes them without leading dashes and with underscores: `tp`,
`mem_fraction_static`.

**Parameter.** One engine flag in a search space. See
[common engine parameters](#common-engine-parameters).

**Config** (configuration). One complete set of parameter values, such as
`tp=2, mem_fraction_static=0.85`.

**Search space.** The parameters a campaign may change and the values each
may take. A parameter is *fixed* (one value), *swept* (a list), a *range*
(min, max, step) or *tied* to others (they change together). See
[Search spaces](search-spaces.md).

**Candidate.** One config from the search space. A space with
`tp: [1, 2]` and `mem_fraction_static: [0.8, 0.85, 0.88]` has 2 × 3 = 6
candidates.

**Run.** One attempt at one candidate: the engine is started with that config
on a machine, health-checked, benchmarked, then stopped. A candidate can have
several runs (a retry after an infrastructure failure, or a repeat to confirm a
result). See [Runs and failures](runs.md).

**Objective.** The definition of "better": one **target metric**, a
**direction** (maximize or minimize), and any number of **redlines**. See
[Objectives](objectives.md).

**Redline.** A limit a config must stay within to be ranked, such as
"p99 time to first token ≤ 2000 ms". A config that crosses a redline is
rejected regardless of its score. A metric the benchmark did not report counts
as crossed.

**Campaign.** One search: model, image, machines, search space, benchmark,
objective and schedule. A campaign copies the search space and objective when
it is created, so later edits to those do not affect existing campaigns. See
[Creating a campaign](campaigns.md).

**Leaderboard.** A campaign's results, best first, by the objective's target
metric.

**Baseline.** The config production runs today, recorded on the **Baselines**
page. When a campaign's model has a baseline, the leaderboard compares each
result with it (**vs baseline**, such as `+12%`). See [Baselines](baselines.md).

**Policy.** A search algorithm packaged as a container. Without a policy, a
campaign tries every candidate in order (**grid**, or exhaustive search). With
one, the policy chooses what to try next; the platform still launches,
measures and ranks every config. The install includes `random-search`. See
[Search policies](policies.md).

**Policy session.** One policy container's lifetime on a campaign: *starting*,
*searching*, *finalizing* (it names its best configs, the **finalists**),
*validating* (the platform measures the finalists itself), *done*.

**Trial.** One config a policy tries: an engine the platform launches for the
policy, and the benchmark of it.

**Winner.** The best config that held every redline. Its run details show the
engine command, image and weights path to deploy.

## Measurement

**LLMBench.** The benchmark platform installed alongside AutoTune. AutoTune
asks it to send load to each run's engine and reads back the metrics.

**Workload** and **benchmark.** The load each run receives. A campaign's
**Benchmark** step describes the workload, and AutoTune creates and locks the
matching *benchmark* on LLMBench (named `autotune-sweep-…` or similar), so
every candidate receives the same test.

**Synthetic sweep.** A workload of random prompts of a fixed size, sent at
several **concurrency** levels in turn. Fast and repeatable, but it does not
reflect prompt caching.

**Replay.** A workload of recorded production requests, sent again. It
reflects real prompt lengths and prefix-cache reuse, and needs a dataset.

**Dataset.** A sample of one model's recorded requests, which LLMBench
collects from the gateway's request logs and replaces with a fresh sample
on an interval. A campaign keeps one sample for its whole life. See
[Datasets](datasets.md).

**Concurrency.** The number of requests in flight at the same time. A sweep at
`1, 4, 16` measures the engine under three loads.

**Requests per slot.** How many requests each concurrent slot sends at one
level: level *c* sends *c* × this many. More requests give more stable numbers
and longer runs.

**Metric names.** Metrics are named `<benchmark module>.<metric>`, such as
`perf_guidellm_sweep.output_tpm_card_norm`. The prefix identifies the
benchmark that produced it (`perf_guidellm_sweep` for the synthetic sweep,
`replay` for a replay).

Common metric terms:

| term | meaning | unit |
|---|---|---|
| TTFT | time to first token | ms |
| ITL, TPOT | inter-token latency, time per output token | ms |
| TPS, TPM | tokens per second, tokens per minute | tok/s, tok/min |
| output / input | generated tokens / prompt tokens | |
| per card (`card_norm`) | divided by the number of GPUs the config uses | tok/min/GPU |
| p50, p90, p99 | median, and the value 90% or 99% of requests stay under | |

**Per-GPU throughput.** The default objective ranks by output tokens per
minute *per GPU*. Without normalizing by GPU count, a config that uses more
GPUs nearly always ranks first.

**Screen and verify.** An optional second stage: every candidate is first
measured with a quick workload (the *screen*), then the best few are measured
again with a slower, more realistic one (the *verify*, usually a replay), and
the second measurement decides. Set under **Benchmark ▸ Advanced**.

**Pre-flight checks.** Checks run before a campaign starts. On a Kubernetes
cluster, probe pods on each machine pull the engine and policy images and
mount the model path, so a wrong image tag or path is reported before the
first run.

## Machines

**Platform cluster.** The Kubernetes cluster that runs AutoTune and LLMBench.
It needs no GPUs.

**GPU cluster.** A Kubernetes cluster whose GPU nodes run the engines. It is
added on **Resources** with a kubeconfig written by `deploy/gpu-cluster.sh`,
and can be the platform cluster itself.

**Machine.** One place a run can land: a registered GPU node of a GPU
cluster, or a bare-metal machine reached over ssh. See
[Machines and GPU clusters](machines.md).

**Lease.** Permission for the platform to use a machine; campaigns run only on
leased machines. **End lease** lets current runs finish and starts nothing new
(the machine is *handing back*), then releases it. **End lease now** stops the
platform's runs immediately. The platform only stops workloads it started.

**Smoke test.** A quick check that the platform can reach a machine and is
allowed to start pods there.

**Parallel candidates.** Several candidates sharing one machine, each on its
own GPUs. An 8-GPU node can run four `tp=2` configs at once.

**Node group.** Several machines joined to serve one model across nodes
(multi-node). Most setups do not need one.

## Scheduling

**Window.** The hours a campaign may use machines, such as 23:00 to 08:00
every night, in a chosen timezone. A run that cannot finish before the window
closes is not started. A search that does not finish in one window continues
in the next.

**No window.** A campaign without a window runs only when started, until its
search is done or it is stopped.

**Run now.** Starts a scheduled campaign immediately, outside its window. It
runs until its search is done or it is stopped.

## Common engine parameters

sglang names; vLLM has close equivalents. The **?** next to a parameter in the
search-space editor shows its description.

| parameter | effect |
|---|---|
| `tp` | *tensor parallel* size: how many GPUs one model replica is split across |
| `dp` | *data parallel* size: independent model replicas |
| `ep` | *expert parallel* size, for mixture-of-experts (MoE) models |
| `mem_fraction_static` | share of GPU memory reserved for weights and the KV cache; higher means more cache and less headroom |
| `chunked_prefill_size` | prompt tokens processed per step; trades time to first token against decode smoothness |
| `max_running_requests` | maximum requests decoded at once |
| `schedule_policy` | which waiting request is scheduled next (`fcfs`, `lpm`…) |
| `kv_cache_dtype` | precision of the KV cache; `fp8` roughly doubles its capacity |
| `attention_backend` | which attention kernels run |
| `cuda_graph_max_bs` | largest batch size captured as a CUDA graph |

The **KV cache** holds the context of every in-flight request. A larger cache
allows more concurrent requests; too small a cache causes queueing or
out-of-memory errors.

---

[User guide](README.md) · Next: [First campaign](first-campaign.md) →
