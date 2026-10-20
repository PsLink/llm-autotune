"""Naming one number: which measure, which statistic, where.

    [<scenario>/]<measure>[@<stat>][[<qualifier>]][:<point>]

    output_tpm_per_gpu                     the benchmark's main scenario, at its operating point
    ttft_ms@p99                            a statistic
    ttft_ms@p90[32k_64k]                   a replay's 32k–64k prompt bucket
    accuracy[gpqa_diamond]                 one task of an accuracy suite
    request_output_tps:c1                  a sweep's concurrency-1 level, not the operating point
    replay/success_rate                    a named scenario: a module key, `name#2` for
                                           a second run of the same module

A raw LLMBench key (`replay.output_tpm_card_norm`) has a `.` and none of
`/@[:`, so the two can never be mistaken for each other: an objective written
before measures existed keeps resolving to exactly what it always did.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.measures.catalog import ALIASES, BY_NAME, lookup

_REF = re.compile(
    r"^(?:(?P<scenario>[A-Za-z0-9_\-]+(?:#\d+)?)/)?"
    r"(?P<measure>[a-z0-9_]+)"
    r"(?:@(?P<stat>[a-z0-9]+))?"
    r"(?:\[(?P<qualifier>[A-Za-z0-9_\-.]+)\])?"
    r"(?::(?P<point>[a-z0-9]+))?$"
)
# A sweep level, or one of the named points.
_POINT = re.compile(r"^(operating|peak|c\d+)$")


class RefError(ValueError):
    """A reference that names no measure, or asks a measure for something it
    cannot be asked (a statistic it has none of, a qualifier it takes none of)."""


@dataclass(frozen=True)
class MeasureRef:
    measure: str
    stat: str | None = None
    qualifier: str | None = None
    point: str | None = None
    scenario: str | None = None

    @property
    def key(self) -> str:
        """The name of this number inside one scenario at one point — what an
        adapter's output is keyed by."""
        out = self.measure
        if self.stat:
            out += f"@{self.stat}"
        if self.qualifier:
            out += f"[{self.qualifier}]"
        return out

    def __str__(self) -> str:
        out = self.key
        if self.point and self.point != "operating":
            out += f":{self.point}"
        if self.scenario:
            out = f"{self.scenario}/{out}"
        return out


def is_measure_ref(text: str) -> bool:
    """True when `text` is meant as a measure, False for a raw LLMBench key.
    A raw key always has a module and a `.`; a measure reference never does."""
    return "." not in text and bool(_REF.match(text))


def parse(text: str) -> MeasureRef:
    """Parse and validate against the catalogue. The statistic defaults to the
    measure's own default; a measure without one must be given a statistic."""
    match = _REF.match((text or "").strip())
    if not match:
        raise RefError(f"not a measure reference: {text!r}")
    name = ALIASES.get(match["measure"], match["measure"])
    spec = BY_NAME.get(name)
    if spec is None:
        raise RefError(f"unknown measure {match['measure']!r}")
    stat = match["stat"] or spec.default_stat
    if spec.stats:
        if stat is None:
            raise RefError(
                f"{name} needs a statistic, one of {', '.join(spec.stats)}: "
                f"e.g. {name}@{spec.stats[-1]}"
            )
        if stat not in spec.stats:
            raise RefError(f"{name} has no statistic {stat!r}; it has {', '.join(spec.stats)}")
    elif match["stat"]:
        raise RefError(f"{name} takes no statistic")
    qualifier = match["qualifier"]
    if qualifier is not None:
        if not spec.qualifier:
            raise RefError(f"{name} takes no qualifier")
        if spec.qualifier_values and qualifier not in spec.qualifier_values:
            raise RefError(
                f"{name} has no {spec.qualifier} {qualifier!r}; "
                f"it has {', '.join(spec.qualifier_values)}"
            )
    elif spec.qualifier and not spec.qualifier_values:
        # A qualifier with an open set (an accuracy suite's tasks) is required:
        # "accuracy" alone names no number.
        raise RefError(f"{name} needs a {spec.qualifier}: e.g. {name}[gpqa_diamond]")
    point = match["point"]
    if point is not None and not _POINT.match(point):
        raise RefError(f"unknown point {point!r}: use operating, peak or a sweep level like c8")
    return MeasureRef(
        measure=name,
        stat=stat if spec.stats else None,
        qualifier=qualifier,
        point=None if point == "operating" else point,
        scenario=match["scenario"],
    )


def label(ref: MeasureRef) -> str:
    """A reader's name for it: "Time to first token p99 (c1)"."""
    spec = lookup(ref.measure)
    text = spec.label if spec else ref.measure
    if ref.stat and ref.stat != (spec.default_stat if spec else None):
        text += f" {ref.stat}"
    if ref.qualifier:
        text += f", {ref.qualifier.replace('_', '–')}"
    if ref.point:
        text += f" ({ref.point})"
    return text
