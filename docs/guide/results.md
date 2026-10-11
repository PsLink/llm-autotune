[User guide](README.md) › **Reading results**

# Reading results

A campaign's page has four tabs: **Leaderboard**, **Runs**, **Configuration**
and **Candidates**. **Report** above them writes it all up as Markdown.

## Leaderboard

![A finished campaign's leaderboard](images/campaign-leaderboard.png)

The line at the top restates the objective: the direction, the target metric's
label and its full name, and the redlines, if any.

The bars show every measured config, best first, labeled with the values of
the swept parameters in order (`1 · 0.85` is `tp=1`, `mem_fraction_static=0.85`).
The percentages on the right compare each config with the baseline, or with
the best config when there is no baseline.

The table has one row per measured config:

| column | means |
|---|---|
| **Run** | the run that produced the score; its details are on the **Runs** tab |
| **Config** | the values that vary between candidates. The fixed ones are on the **Candidates** tab. |
| **GPU** | the card type it ran on, when known. Yellow when it differs from the baseline's card, which makes the comparison unfair. |
| score | the target metric, with its unit, such as *Output TPM per card (tok/min/GPU)* |
| **vs baseline** | how it compares with production's config measured in the same campaign, such as `+12.0%`. Empty when there is no baseline ([Baselines](baselines.md)). |
| **Redlines** | **held** if the config kept within every redline; **crossed** with which one if not. A crossed config never wins. |

A row tagged **baseline** is production's own config, measured the same way
in the same campaign. With one, the bars turn green (better than the
baseline), grey (the baseline) or orange (worse):

![A leaderboard with a baseline row](images/baseline-leaderboard.png)

Here `tp=1` is production's own config, with only a flag that changes nothing
on this mock engine, so its +0.9% is measurement noise; `tp=2` halves the
throughput per GPU.

### Two boards

A campaign with a [verify stage](campaigns.md#4-benchmark) shows two boards.
**Verified on real traffic** on top is the one that decides: the best few
configs measured again with the second workload. **All candidates** below is
the first, quicker measurement of every config. Their numbers come from
different benchmarks, so they are never mixed in one table. A replay row
tagged **other dataset** was measured on a different sample of the dataset and
does not compare with the rest.

## Runs

The **Runs** tab lists every run: its status, why it failed if it did, which
GPUs it had, the endpoint it served on, when it started and how long it took.
Click a row for the details:

![A run's details](images/campaign-run-drawer.png)

- **The verdict box:** which benchmark measured it, which metric ranked it,
  its score, and whether it held the redlines.
- **What the objective looked at:** the values of the target metric and every
  redline metric.
- **Config:** the full set of engine settings, fixed ones included.
- **Launch command:** exactly what was started. On Kubernetes it is the
  manifest that was applied; over ssh, the `docker run` line.
- **Results:** everything LLMBench reported, under **Metrics**.
- **Error** and **Captured log:** for a failed run, why, and the engine's log.
  **Download** saves the log.

[Runs and failures](runs.md) explains the statuses and failure kinds.

## Configuration

**Configuration** shows the campaign as it was created: every setting, the
launch extras, the search space and the objective, each with a copy button.
Editing the search space or objective it was made from afterwards changes
nothing here.

![The Configuration tab](images/campaign-configuration.png)

**Difference from production** lists flags that production's recorded
baseline sets and this campaign never does. Each one is a difference the
results don't account for.

## Candidates

**Candidates** lists every config in the search space and what became of it:

| status | means |
|---|---|
| **queued** | waiting for a free machine |
| a run state, such as **benching** | being measured now |
| **benchmarked** | measured; its score is on the leaderboard |
| **failed** | every attempt failed; the reason is next to it |
| **stopped** | its run was stopped |
| **rejected** | the static check refused it before launch (for example, more GPUs than any machine has) |

The map on top shows the space and how its candidates pack onto the machines
("3 rounds of 4 cards, widest first"). Click a row to open its run.
**Fixed in every candidate** lists the settings that never change.

![The Candidates tab](images/campaign-candidates.png)

## The report

**Report** writes the campaign up as Markdown: the objective and model, a
verdict, a table of results against production, the configs that crossed a
redline, the failures, and suggested next steps. **Copy markdown** puts it on
the clipboard for a ticket or a chat.

![The campaign report](images/campaign-report.png)

It does not call a win it cannot back. A lead smaller than the measurement
noise is reported as *no meaningful difference*. When repeated measurements of
the same config disagree by more than the lead, it is *unstable*. When no
baseline was measured, the report says so: the "canary" it mentions is the
baseline run, production's config measured within the same campaign.

## Comparing campaigns

Two results compare only when they were measured the same way:

- **the same benchmark**: the same workload always maps to the same LLMBench
  benchmark, named on the **Benchmark** step and in each run's details;
- **the same card type**;
- for a replay, **the same dataset sample**.

Numbers from different workloads are different measurements, even when the
metric has the same name.

## Taking the winner to production

The winner is a run like any other. Its details have:

- the full **Config** and the exact **Launch command**;
- the **image**, on the **Configuration** tab;
- the engine version and image digest it ran with, in its results.

Apply those to your serving deployment, by hand or from your own pipeline.
Then record the new config on [Baselines](baselines.md), so the next campaign
measures against it.

---

← [Running a campaign](running-campaigns.md) · [User guide](README.md) · Next: [Baselines](baselines.md) →
