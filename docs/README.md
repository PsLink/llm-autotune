# Documentation

**Using the platform**

- [User guide](guide/README.md): every page of the UI, with screenshots. Start
  with [Concepts](guide/concepts.md) and [First campaign](guide/first-campaign.md).

**How it works**

- [How it works](workflow.md): the product view, without code.
- [Architecture](architecture.md): components, the run state machine, and the
  extension points.
- [Platform architecture](api/platform-architecture.md): a policy campaign,
  step by step.

**Operating it**

- [Installing](install.md): the platform cluster, GPU clusters and a first
  campaign.
- [After installing](after-installing.md): settings, search policies, more
  GPUs, ingress and datasets.
- [Deploying](deploying.md): the Helm chart and every setting.
- [Upgrading](upgrading.md): how the schema is applied.

**Integrating with it**

- [Policy API](api/policy-api.md): what a search container implements, in
  short; the full [policy contract](api/policy-contract.md).
- [Machine lease API](api/machine-lease.md): leasing GPU machines from another
  system, and ending the lease.
- [Agent API](api/agent-api.md): the read models LLM-written performance
  reports use (off by default), and the
  [report agent prompt](perf-report-prompt.md).
- [Plugins](plugins.md): routes, worker steps, tables, managed campaigns and
  pages from a separately installed package.
