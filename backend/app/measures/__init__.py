"""Measures: the platform's one vocabulary for benchmark results.

    scenarios = measures_of(result.metrics, reports, cards=2)
    value("ttft_ms@p99", scenarios)            # the main scenario's operating point
    value("replay/success_rate", scenarios)
    value("replay.output_tpm_card_norm", metrics=result.metrics)   # a raw key, verbatim

See `catalog` for the measures, `refs` for how one is named and `adapters`
for how each LLMBench module's keys become them.
"""

from __future__ import annotations

from typing import Any

from app.measures.adapters import Scenario, adapt
from app.measures.catalog import BY_NAME, MEASURES, Measure, lookup
from app.measures.refs import MeasureRef, RefError, is_measure_ref, label, parse

__all__ = [
    "BY_NAME",
    "MEASURES",
    "Measure",
    "MeasureRef",
    "RefError",
    "Scenario",
    "adapt",
    "is_measure_ref",
    "label",
    "lookup",
    "main_scenario",
    "measures_of",
    "parse",
    "resolve",
    "value",
]

# Keys at the top of a run's metrics that belong to no module.
_SUBMISSION_KEYS = {"score_total"}


def _split(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A run's flat `<module>.<key>` metrics, grouped by module, in the order
    the modules first appear."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in (metrics or {}).items():
        if key in _SUBMISSION_KEYS or "." not in key:
            continue
        module, _, rest = key.partition(".")
        out.setdefault(module, {})[rest] = value
    return out


def measures_of(
    metrics: dict[str, Any] | None,
    reports: list[dict[str, Any]] | None = None,
    cards: float | None = None,
    replay_names: tuple[str, ...] | None = None,
    aliases: dict[str, str] | None = None,
) -> list[Scenario]:
    """Every module of a run as a Scenario.

    `metrics` is the flat dict a run's Result stores. `reports` (optional) are
    the module reports, for each module's params — a replay's concurrency, a
    sweep's configured token lengths. `cards` is what the config occupied;
    without it per-GPU measures are recovered from LLMBench's card-normalized
    pairs, or left out.
    """
    params_by_key: dict[str, dict[str, Any]] = {}
    for report in reports or []:
        if isinstance(report, dict):
            key = str(report.get("module") or report.get("module_name") or "")
            params_by_key[key] = dict(report.get("params") or {})
    if replay_names is None or aliases is None:
        from app.core.config import get_settings

        settings = get_settings()
        if replay_names is None:
            configured = settings.llmbench_replay_module
            replay_names = (configured,) if configured else ()
        if aliases is None:
            aliases = dict(settings.llmbench_module_families or {})
    return [
        adapt(key, raw, params_by_key.get(key), cards, replay_names, aliases)
        for key, raw in _split(metrics or {}).items()
    ]


def main_scenario(scenarios: list[Scenario], wanted: str | None = None) -> Scenario | None:
    """The scenario a reference without one means: the named one, else the
    first throughput scenario in benchmark order, else the first of any kind."""
    if wanted:
        return next((s for s in scenarios if s.key == wanted), None)
    return next((s for s in scenarios if s.is_throughput), scenarios[0] if scenarios else None)


def resolve(ref: MeasureRef, scenarios: list[Scenario]) -> float | None:
    scenario = main_scenario(scenarios, ref.scenario)
    if scenario is None:
        return None
    point = scenario.points.get(ref.point or "operating") or {}
    return point.get(ref.key)


def value(
    name: str,
    scenarios: list[Scenario] | None = None,
    *,
    metrics: dict[str, Any] | None = None,
) -> Any:
    """What `name` reads to in a run: a measure reference through the
    scenarios, a raw LLMBench key verbatim from the stored metrics."""
    if is_measure_ref(name):
        if scenarios is None:
            scenarios = measures_of(metrics or {})
        return resolve(parse(name), scenarios)
    return (metrics or {}).get(name)
