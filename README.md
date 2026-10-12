# LLM AutoTune

LLM AutoTune finds the best serving configuration for a model on your GPUs. A
**campaign** sets a model, a **search space** of engine settings (sglang,
vLLM) and an **objective**: one metric to improve, plus redlines a config must
hold. A **policy**, a search algorithm packaged as a container, picks the
configs to try. The platform launches each config, benchmarks it with
[LLMBench](https://github.com/modelsphere/llm-bench), and ranks it against the
**baseline**, the configuration production runs today.

![LLM AutoTune overview](docs/api/diagrams/platform-overview.png)

## Features

- **Pluggable search.** A policy is any container that implements a small HTTP
  API ([policy contract](docs/api/policy-contract.md)). Random search is
  included; without a policy, the platform tries every config in the space.
- **Platform-owned measurement.** The platform benchmarks every config the
  same way and re-measures the best before reporting. A policy never grades
  its own results.
- **Real hardware and traffic.** Benchmarks run on the GPUs you serve on and
  can replay captured production traffic. A config that crosses a latency or
  quality redline is rejected.
- **Existing GPUs.** Kubernetes GPU nodes or ssh machines, leased to the
  platform for a nightly window.
- **Reproducible results.** Every run records its engine command, image,
  engine version, card type and dataset.
- **Restart-safe.** All state is in Postgres; restarting the worker mid-run
  loses nothing.
- **Extensible.** Plugins add pages, API routes, scheduled steps and search
  strategies ([plugins](docs/plugins.md)).

## Architecture

![Platform architecture: the policy on a GPU machine asks the API for runs; the supervisor launches each config, LLMBench benchmarks it, and the platform validates the best](docs/api/diagrams/platform-architecture.svg)

The policy decides what to try. The supervisor starts the policy, launches the
configs it requests, and submits each to LLMBench. At the deadline the
platform re-measures the policy's best configs itself, and that measurement is
the result. Details: [platform architecture](docs/api/platform-architecture.md).

## Install

The platform runs on a Kubernetes cluster without GPUs; runs go to GPU
clusters (or ssh machines) added from the UI. Requirements: `kubectl`,
`helm`, `git`, `openssl`, and `docker` for building search policies.

```bash
git clone https://github.com/modelsphere/llm-autotune
cd llm-autotune
deploy/quickstart.sh                          # 1. LLM AutoTune + LLMBench on the current context
deploy/gpu-cluster.sh --context <gpu-cluster> # 2. a scoped account on a GPU cluster; writes llm-autotune-runs.kubeconfig
```

3. Sign in with the address and password the install prints. On
   **Resources ▸ Add GPU cluster**, upload the kubeconfig and register the GPU
   nodes.
4. Create a search space and a campaign. The campaign's **Check** step pulls
   the images and mounts the model path on the nodes before anything starts.
   [First campaign](docs/guide/first-campaign.md) walks through it with
   screenshots.

[Installing](docs/install.md) covers air-gapped clusters, a local setup with
two kind clusters, and troubleshooting. [After installing](docs/after-installing.md)
covers settings, more GPUs and ingress; [Deploying](docs/deploying.md) covers
the Helm chart and every setting.

## Demo without GPUs

```bash
deploy/demo.sh --kind
```

Creates a local [kind](https://kind.sigs.k8s.io/) cluster (allow Docker about
8 GB), installs both platforms, and starts a demo campaign on a mock engine
that needs no GPU. The full loop runs; the numbers are not performance data.
`deploy/demo.sh down --kind` removes it.

## Concepts

| | |
|---|---|
| **Campaign** | One search: a model, a search space, an objective, machines and a schedule. |
| **Search space** | The engine settings a campaign may change, and their values. |
| **Objective** | The target metric to rank by, plus redlines a config must hold. |
| **Run** | One config, launched on a machine and benchmarked. |
| **Baseline** | The configuration production runs today, measured the same way. |
| **Machine** | Where runs land: a node of a GPU cluster, or an ssh machine. |
| **Policy** | A container that decides which config to try next. |

The [user guide](docs/guide/concepts.md) defines the rest.

## Repository layout

```text
backend/     FastAPI API and the orchestrator worker (Python 3.12, uv)
  app/
    control/    search space, orchestrator, launch drivers
    evaluation/ health checks and the benchmark-platform adapter
    agent/      read models for LLM-written performance reports
frontend/    Vue 3 + TypeScript UI
deploy/
  quickstart.sh  installs both platforms; demo.sh installs the no-GPU demo
  gpu-cluster.sh prepares a GPU cluster and writes its kubeconfig
  helm/      the chart
  docker/    images, and a compose file for local development
policies/    submodule: llm-autotune-policies (the SDK and two policies)
operator/    the optional Kubernetes operator (Go)
mock-engine/ a fake engine for running the loop without GPUs
docs/        guides, architecture and API contracts
```

## Documentation

- [User guide](docs/guide/README.md): every page of the platform, starting
  with [concepts](docs/guide/concepts.md) and
  [a first campaign](docs/guide/first-campaign.md)
- [How it works](docs/workflow.md): the product view, without code
- [Architecture](docs/architecture.md): how the system is built
- [Installing](docs/install.md), [After installing](docs/after-installing.md),
  [Deploying](docs/deploying.md), [Upgrading](docs/upgrading.md)
- [Plugins](docs/plugins.md): extending the platform without forking it
- APIs: [policy contract](docs/api/policy-contract.md),
  [machine lease](docs/api/machine-lease.md),
  [agent API](docs/api/agent-api.md) (off by default)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md): `uv run pytest` in `backend/`,
`npm run typecheck` in `frontend/`, and `helm lint` on the chart.

## License

[Apache License 2.0](LICENSE).
