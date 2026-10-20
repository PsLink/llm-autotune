"""The service level a scenario's reported level is judged against.

A throughput number means nothing on its own: any engine goes faster if it is
allowed to answer slower. So a scenario is measured at the highest load that
still holds a latency bound, and the bound is what this describes.

It is built from the campaign's objective — the same redlines the objective
already ranks by — so a report and a leaderboard cannot disagree about what
"fast enough" meant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Redline metric names a gate is read from. They are suffixes: a redline names
# a module too (`perf_sweep.ttft_p50_ms`), and the gate is per-scenario.
# The TTFT percentiles LLMBench reports. There is no p95: a redline written
# against one would set a gate whose percentile no result ever carries.
TTFT_SUFFIXES = ("ttft_p50_ms", "ttft_p90_ms", "ttft_p99_ms")
REQUEST_TPS_SUFFIX = "request_output_tps"


def _measure_leaf(metric: str) -> str | None:
    """The raw-key leaf a gate measure stands for (`ttft_ms@p99` →
    `ttft_p99_ms`), or None when `metric` is not a gate measure. Only the
    overall TTFT counts — a prompt-bucket TTFT is not the scenario's gate."""
    from app.measures import RefError, is_measure_ref, parse

    if not is_measure_ref(metric):
        return None
    try:
        ref = parse(metric)
    except RefError:
        return None
    if ref.point or ref.qualifier:
        return None
    if ref.measure == "ttft_ms" and ref.stat in ("p50", "p90", "p99"):
        return f"ttft_{ref.stat}_ms"
    if ref.measure == "request_output_tps" and ref.stat == "mean":
        return REQUEST_TPS_SUFFIX
    return None


@dataclass
class Gate:
    """A latency bound plus a per-request floor. A new rule is a new field
    here plus the redline it is read from."""

    ttft_ms: float = 0.0
    ttft_percentile: str = "p50"
    min_request_output_tps: float = 0.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Gate:
        raw = raw or {}
        return cls(
            ttft_ms=float(raw.get("ttft_ms") or 0),
            ttft_percentile=str(raw.get("ttft_percentile") or "p50"),
            min_request_output_tps=float(raw.get("min_request_output_tps") or 0),
        )

    @classmethod
    def from_objective(cls, objective: dict[str, Any] | None) -> Gate | None:
        """The gate a campaign's objective implies, or None when it declares
        no latency redline — an unconstrained campaign has no gate, which is
        not the same as a gate nothing can pass.
        """
        redlines = (objective or {}).get("redlines") or []
        ttft_ms = 0.0
        percentile = "p50"
        min_tps = 0.0
        for rule in redlines:
            if not isinstance(rule, dict):
                continue
            metric = str(rule.get("metric") or "")
            value = rule.get("value")
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            leaf = metric.rsplit(".", 1)[-1]
            # A measure reference names the same two rules: ttft_ms@p99 and
            # request_output_tps (app.measures).
            measure = _measure_leaf(metric)
            if measure is not None:
                leaf = measure
            if leaf in TTFT_SUFFIXES and rule.get("op") in ("<=", "<"):
                # The tightest TTFT redline is the gate: a scenario has to hold
                # every one of them, so the strictest is the binding one.
                if ttft_ms == 0.0 or number < ttft_ms:
                    ttft_ms = number
                    percentile = leaf.split("_")[1]
            elif leaf == REQUEST_TPS_SUFFIX and rule.get("op") in (">=", ">"):
                min_tps = max(min_tps, number)
        if ttft_ms == 0.0 and min_tps == 0.0:
            return None
        return cls(ttft_ms=ttft_ms, ttft_percentile=percentile, min_request_output_tps=min_tps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ttft_ms": self.ttft_ms,
            "ttft_percentile": self.ttft_percentile,
            "min_request_output_tps": self.min_request_output_tps,
        }

    def describe(self) -> str:
        parts = []
        if self.ttft_ms:
            parts.append(f"TTFT {self.ttft_percentile} ≤ {self.ttft_ms:g} ms")
        if self.min_request_output_tps:
            parts.append(f"≥ {self.min_request_output_tps:g} tok/s per request")
        return ", ".join(parts)
