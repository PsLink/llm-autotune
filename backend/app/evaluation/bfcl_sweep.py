"""BFCL scan plans and selection shared by reports and plugins."""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Concurrency = Annotated[int, Field(strict=True, ge=1, le=128)]


class BFCLSweepPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["linear"] = "linear"
    min: Concurrency
    max: Concurrency
    points: int = Field(default=5, strict=True, ge=2, le=16)

    @model_validator(mode="after")
    def validate_range(self):
        if self.max - self.min + 1 < self.points:
            raise ValueError("the range must contain at least points distinct integers")
        return self

    def resolved(self) -> list[int]:
        denominator = self.points - 1
        return [self.min + (2 * i * (self.max - self.min) + denominator) // (2 * denominator)
                for i in range(self.points)]

    def snapshot(self) -> dict:
        return {**self.model_dump(), "resolved_concurrencies": self.resolved(),
                "sampling_version": 1}


def bfcl_levels(metrics: dict, key: str) -> list[dict]:
    """Recover all points from the platform's lossless flattened metrics."""
    levels: dict[int, dict] = {}
    prefix = f"{key}."
    for name, value in metrics.items():
        if not name.startswith(prefix):
            continue
        match = re.fullmatch(r"c(\d+)\.(.+)", name[len(prefix):])
        if match:
            levels.setdefault(int(match[1]), {})[match[2]] = value
    return [{"concurrency": c, "metrics": {k: v for k, v in values.items() if k != "error"},
             "error": str(values.get("error") or "")} for c, values in sorted(levels.items())]


def select_bfcl_level(metrics: dict, report: dict, floors: dict | None = None) -> dict | None:
    """Highest-TPM valid point clearing frozen redlines and confirmed floors.

    A diagnostic representative remains in raw scalars when no point passes;
    it must never become a best_level or a ranked row.
    """
    key = str(report.get("module") or report.get("module_name") or "bfcl_v4")
    canceled = metrics.get(f"{key}.sweep_canceled") in (1, True, "1", "true")
    status = str(report.get("status") or "").lower()
    error = str(report.get("error") or "").strip().lower()
    if (canceled or status in ("canceled", "cancelled", "aborted")
            or error in ("canceled", "cancelled", "bfcl canceled", "bfcl scan canceled")):
        return None
    bounds = [b for b in report.get("bounds") or [] if b.get("role") == "redline"]
    wanted = {k[len(key) + 1:]: v for k, v in (floors or {}).items()
              if k.startswith(f"{key}.")}
    choices = []
    for level in bfcl_levels(metrics, key):
        values = level["metrics"]
        if values.get("valid") != 1:
            continue
        checks = {b["metric"]: (b.get("min"), b.get("max")) for b in bounds}
        checks.update({name: (floor, None) for name, floor in wanted.items()})
        def within(name: str, low: Any, high: Any, values: dict = values) -> bool:
            value = values.get(name)
            return (isinstance(value, (int, float)) and math.isfinite(value)
                    and (low is None or value >= low) and (high is None or value <= high))
        total = values.get("total_tpm")
        if (isinstance(total, (int, float)) and math.isfinite(total)
                and all(within(name, *limits) for name, limits in checks.items())):
            choices.append(level)
    return max(choices, key=lambda lv: (lv["metrics"]["total_tpm"], -lv["concurrency"])) \
        if choices else None
