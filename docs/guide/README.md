# User guide

How to use LLM AutoTune once it is installed: adding GPUs, saying what to
tune, running campaigns and reading what they find. For installing it, see
[Installing](../install.md).

![A finished campaign's leaderboard](images/campaign-leaderboard.png)

## New here? Read these first

1. **[Concepts and words](concepts.md)**: campaign, search space, objective,
   redline, policy, lease, and the rest of the vocabulary, each in a few lines.
2. **[Your first campaign](first-campaign.md)**: from a fresh install to a
   tuned config, step by step, with a screenshot of every step. You can follow
   it on a laptop with no GPUs.

## The pages, one by one

| page | what it covers | where in the UI |
|---|---|---|
| [Machines and GPU clusters](machines.md) | adding GPU clusters and ssh machines, leasing them, smoke tests, node groups | **Resources** |
| [Search spaces](search-spaces.md) | the settings to try: fixed, swept, ranged and tied parameters, conditions, YAML | **Automatic Tuning ▸ Search spaces** |
| [Objectives](objectives.md) | what "better" means: the target metric, the direction, redlines; which metric to pick | **Automatic Tuning ▸ Objectives** |
| [Search policies](policies.md) | grid search versus a policy, `random-search`, registering your own | **Automatic Tuning ▸ Policies** |
| [Creating a campaign](campaigns.md) | every step and field of New campaign; drafting from a baseline; YAML | **Automatic Tuning ▸ Campaigns ▸ New campaign** |
| [Running a campaign](running-campaigns.md) | statuses, Start, Run now, Pause, Stop, schedules, pre-flight checks, the policy panel | a campaign's page |
| [Reading results](results.md) | the leaderboard, a run's details, configuration, candidates, the report, rolling out a winner | a campaign's tabs |
| [Baselines](baselines.md) | recording what production runs, comparing against it, Tune from this | **Automatic Tuning ▸ Baselines** |
| [Runs and failures](runs.md) | a run's life, every kind of failure and what it usually means | **Runs** |
| [Account, users and API keys](account.md) | roles, passwords, the published name, API keys | the user menu, top right |
| [Troubleshooting](troubleshooting.md) | symptoms and their usual causes | |

## How the pieces fit

```text
                 Resources                        Automatic Tuning
         ┌─────────────────────┐   ┌──────────────────────────────────────────┐
         │ GPU clusters        │   │ Search space   Objective   Policy (opt.) │
         │   └─ machines ──────┼─┐ │       └────────────┼───────────┘         │
         │       (leased)      │ │ │                    ▼                     │
         └─────────────────────┘ └─┼──────────────►  Campaign  ◄── Baseline   │
                                   │                    │          (optional) │
                                   └────────────────────┼─────────────────────┘
                                                        ▼
                                   runs on the machines, measured by LLMBench
                                                        ▼
                                         leaderboard, report, winner
```

- A **campaign** brings everything together: it copies a search space and an
  objective, uses leased machines, and optionally a policy and a baseline.
- Each candidate config becomes a **run** on a machine, and LLMBench measures
  it with the campaign's workload.
- Results land on the campaign's **leaderboard**, ranked by its objective and,
  when a baseline exists, compared with production.

## Beyond the UI

- [After installing](../after-installing.md): settings, more GPUs, ingress,
  datasets, building policies.
- [How it works](../workflow.md): the product view, with diagrams of a
  campaign and a night of tuning.
- [Policy contract](../api/policy-contract.md) and
  [Machine lease API](../api/machine-lease.md): building on the platform.

The screenshots in this guide come from a laptop install running the mock
engine, so their numbers mean nothing about real performance.
