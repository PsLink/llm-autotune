[User guide](README.md) › **Search spaces**

# Search spaces

A **search space** lists the engine settings a campaign may change and the
values each may take. Every combination is a **candidate**: one config the
campaign may launch and measure. Find them under
**Automatic Tuning ▸ Search spaces**.

A campaign *copies* the search space when it is created. Editing a space later
never changes a campaign that already exists, so one space can be the starting
point for many campaigns.

## The list

![The search spaces list](images/spaces-list.png)

**Sweep** sums up what varies (`tp×2, mem_fraction_static×3`) and
**Candidates** how many configs that makes. **Edit** opens the editor, and
**Delete** removes the space (campaigns made from it keep their copy).

## Building one

Press **New search space**.

1. **Name** it, pick the **Engine** (`sglang` or `vllm`), and add **Notes** if
   you like.
2. **Add a parameter**: search for an engine setting. The list has two groups,
   **Commonly tuned** and **Usually fixed**, each with a one-line description.
   A flag that isn't listed can be typed in. Write it the way the platform does,
   without the leading dashes and with underscores:
   `--max-total-tokens` is `max_total_tokens`.
3. For each parameter, choose its **Mode** and fill in the values (below).
4. Check the **Preview**, then **Save**. A parameter left without a value is
   named, so you can fill it in or remove it.

![Picking a parameter](images/space-add-parameter.png)

The **?** next to each parameter in the table repeats its description.

## The four modes

![A space with every mode](images/space-editor-advanced.png)

**Fixed.** One value, the same on every candidate. Use it for a setting you
want set but not searched, like `schedule_policy: lpm`.

**Sweep.** A list of values to try. Type one and press Enter, then the next,
or paste several separated by commas. A parameter with a fixed set of choices
(`schedule_policy`, `kv_cache_dtype`…) offers them in a list instead. Swept
parameters multiply: 2 values of one and 3 of another make 6 candidates.

**Range.** Every value from **min** to **max** in steps of **step**: min 4096,
max 16384, step 4096 tries 4096, 8192, 12288 and 16384. Handy when the values
worth trying form an interval. A policy can treat a range as an interval
rather than a list.

**Tied.** Parameters that only make sense together change *together* instead
of being multiplied. Give each tied parameter the same **group** and list its
values in the same order: the first values go together, then the second, and
so on. Above, `tp` and `mem_fraction_static` in group 1 make three candidates,
`1 / 0.88`, `2 / 0.85` and `4 / 0.8`, not nine. A memory fraction that is safe
at `tp=4` may run out of memory at `tp=1`, so crossing them would waste runs.
If the lists have different lengths, the extra values at the end are dropped,
and the editor warns about it.

## Conditions: "when"

Some settings mean something only when another one is on. `hicache_size` does
nothing unless `enable_hierarchical_cache` is `true`. Set **when** on such a
parameter to the one it depends on, and **is…** to the values that turn it on.
Candidates where it would do nothing leave it out, so the same config isn't
launched twice under two names. The preview marks it *only if …* and counts
only real candidates: above, 2 × 2 × 4 × 3 would be 48, but only 36 differ.

## The preview

The **Preview** under the table shows each parameter's values and how many
candidates the space makes. Each candidate is one run of an engine, with model
loading and a benchmark each, so the count is your cost. A few dozen is
plenty for a grid search; for more, use a [policy](policies.md).

The preview also checks every candidate against the largest machine you have:
a `tp` × `dp` that needs more GPUs than the machine has, a memory fraction
outside 0.1–0.99, or a parallel layout the engine refuses to start with (such
as `ep` larger than the GPUs the config runs on). Such candidates are
listed under *rejected by static validation* and never launched.

## YAML

**Import / export YAML** shows the space as text, and takes one back. Use it to
keep spaces in a repository, or to copy one between installs. The format:

```yaml
name: tp x mem-fraction
engine: sglang
description: first sweep
base:                                   # Fixed
  schedule_policy: lpm
grid:                                   # Sweep
  mem_fraction_static: [0.8, 0.85, 0.88]
range:                                  # Range
  chunked_prefill_size: {min: 4096, max: 16384, step: 4096}
tied:                                   # Tied, one entry per group
  - tp: [1, 2, 4]
    max_running_requests: [64, 128, 256]
conditions:                             # when
  hicache_size: {enable_hierarchical_cache: [true]}
```

## Tips

- **Start from production.** Fix what production sets, and sweep one or two
  settings around it. A [baseline](baselines.md) can draft the whole campaign
  for you (**Tune from this**).
- **Two or three values per parameter** is usually enough to find which
  direction helps. Narrow in with a second campaign.
- **Tie, don't multiply,** settings that depend on each other.
- **Mind the GPUs.** A candidate with `tp=4` needs a machine with 4 free GPUs.
  With **Parallel candidates** on, a machine runs several narrow candidates at
  once.

---

← [Machines and GPU clusters](machines.md) · [User guide](README.md) · Next: [Objectives](objectives.md) →
