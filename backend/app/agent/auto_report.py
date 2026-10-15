"""A performance report written by the platform, from the comparison alone.

The perf-report skill writes a report as prose around blocks; the blocks draw
every chart, table and command from the frozen comparison, captions and
definitions included. This module writes the same report with no author: the
skill's structure and blocks, and short sentences that read the numbers the
blocks show, in English and Chinese. Nothing in it is judged or explained;
it states what was measured, as the skill's style rules ask.

Used where a report is needed and nobody has written one: a model-catalog
export of a tuned variant ships its report beside the entry.
"""

from __future__ import annotations

import math
from typing import Any

from app.schemas.agent import ComparisonDocument

TPS = "total_tps_per_machine"


def _sig3(x: float) -> str:
    """Throughput rounded to 3 significant figures, with separators."""
    if not x:
        return "0"
    digits = math.floor(math.log10(abs(x)))
    rounded = round(x, -(digits - 2)) if digits >= 2 else round(x, 2 - digits)
    return f"{rounded:,.0f}" if digits >= 2 else f"{rounded:,.{max(0, 2 - digits)}f}"


def _pct(p: float) -> str:
    rounded = math.floor(abs(p) * 10 + 0.5 + 1e-6) / 10
    sign = "" if rounded == 0 else "+" if p > 0 else "−"
    return f"{sign}{rounded:.1f}%"


def _rows(doc: ComparisonDocument) -> list[dict[str, Any]]:
    """Per attempt and scenario: normalized throughput at each config's best
    concurrency within SLO, and the change."""
    labels = {s.key: s.label or s.key for s in doc.baseline.results.scenarios}
    base_best = {s.key: s.best_level for s in doc.baseline.results.scenarios}
    out = []
    for a in doc.attempts:
        att_best = {s.key: s.best_level for s in a.results.scenarios}
        for sc in a.deltas.scenarios:
            d = sc.best_level.get(TPS)
            if d is None or d.baseline is None or d.attempt is None:
                continue
            out.append(
                {
                    "attempt": a.position,
                    "scenario": labels.get(sc.key, sc.label or sc.key),
                    "baseline": d.baseline,
                    "value": d.attempt,
                    "pct": d.pct,
                    "base_conc": (base_best.get(sc.key) or {}).get("concurrency"),
                    "conc": (att_best.get(sc.key) or {}).get("concurrency"),
                }
            )
    return out


def _quality(doc: ComparisonDocument) -> list[tuple[str, float, float]]:
    out = []
    for a in doc.attempts[:1]:
        for key, d in a.deltas.quality.items():
            if d.baseline is None or d.attempt is None:
                continue
            if 0 <= d.baseline <= 1 and 0 <= d.attempt <= 1:
                out.append((key.split(".", 1)[-1], d.baseline, d.attempt))
    return out


def _changed_flags(doc: ComparisonDocument, position: int) -> list[str]:
    a = next((x for x in doc.attempts if x.position == position), None)
    if a is None:
        return []
    diff = a.diff_vs_baseline.engine_args or {}
    names = [*diff.get("added", {}), *diff.get("changed", {}), *diff.get("removed", {})]
    return [f"--{str(n).replace('_', '-')}" for n in names]


_TEXT = {
    "en": {
        "title": "Optimizing {model} Inference on {card}: Experimental Evaluation",
        "conclusion": "Conclusion",
        "lead": (
            "This report compares the Baseline and Optimized configurations of {model} on "
            "{card} GPUs under the benchmark's SLO. At each configuration's best concurrency "
            "within SLO, normalized throughput (8 GPUs) changes as follows: {changes}."
        ),
        "change": "{scenario} **{a} → {b} tok/s ({pct})**",
        "not_comparable": (
            "The platform does not consider these runs comparable ({why}); read the numbers "
            "below with that in mind."
        ),
        "optimized": "Optimized configuration",
        "setup": "1. Experimental setup and methodology",
        "env": "1.1 Experimental environment",
        "vars": "1.2 Optimization variables",
        "vars_text": "The Optimized configuration changes {flags} relative to the Baseline.",
        "vars_none": "The table above lists every setting the Optimized configuration changes.",
        "scenarios": "1.3 Scenario definitions",
        "results": "2. Experimental results",
        "capacity": "2.1 Serving capacity under the SLO",
        "capacity_row": (
            "In {scenario}, the best concurrency within SLO moves from **{bc} → {c}** and "
            "normalized throughput from **{a} → {b} tok/s ({pct})**."
        ),
        "scaling": "2.2 Concurrency scaling and latency trade-offs",
        "quality": "2.3 Answer quality",
        "quality_row": (
            "{name} moves from **{a:.2%} → {b:.2%}**, **{pp:+.2f} percentage points ({pct})**."
        ),
        "appendix": "Appendix: serving commands",
        "baseline": "Baseline",
        "join": "; ",
        "generated": "*Written by the platform from the measured runs.*",
    },
    "zh": {
        "title": "{model} 在 {card} 上的推理优化:实验评估",
        "conclusion": "结论",
        "lead": (
            "本报告在基准的 SLO 约束下,比较 {model} 在 {card} 上的基线与优化配置。"
            "在各自的 SLO 内最佳并发下,归一化吞吐(8 卡)的变化为:{changes}。"
        ),
        "change": "{scenario} **{a} → {b} tok/s({pct})**",
        "not_comparable": "平台判定这些运行不可直接比较({why}),请结合这一点阅读下面的数据。",
        "optimized": "优化配置",
        "setup": "1. 实验设置与方法",
        "env": "1.1 实验环境",
        "vars": "1.2 优化变量",
        "vars_text": "优化配置相对基线改动了 {flags}。",
        "vars_none": "优化配置改动的设置见上表。",
        "scenarios": "1.3 场景定义",
        "results": "2. 实验结果",
        "capacity": "2.1 SLO 约束下的服务容量",
        "capacity_row": (
            "在{scenario}中,SLO 内最佳并发从 **{bc} → {c}**,"
            "归一化吞吐从 **{a} → {b} tok/s({pct})**。"
        ),
        "scaling": "2.2 并发扩展与延迟权衡",
        "quality": "2.3 回答质量",
        "quality_row": "{name} 从 **{a:.2%} → {b:.2%}**,**{pp:+.2f} 个百分点({pct})**。",
        "appendix": "附录:启动命令",
        "baseline": "基线",
        "join": ";",
        "generated": "*本报告由平台根据实测运行自动生成。*",
    },
}


def title(doc: ComparisonDocument, lang: str = "en") -> str:
    model, card = _model_card(doc)
    return _TEXT[lang]["title"].format(model=model, card=card)


def _model_card(doc: ComparisonDocument) -> tuple[str, str]:
    cfg = doc.baseline.launch.config
    model = cfg.served_model_name or cfg.model_path.rstrip("/").rsplit("/", 1)[-1] or "the model"
    card = doc.baseline.environment.card_type or cfg.gpu_type or "GPU"
    return model, card


def markdown(doc: ComparisonDocument, lang: str = "en") -> str:
    """The report's markdown. Every block in it is one blocks.available() offers
    for this comparison, so it saves and renders like an agent's."""
    t = _TEXT[lang]
    model, card = _model_card(doc)
    rows = _rows(doc)
    first = [r for r in rows if r["attempt"] == 1]
    changes = (
        t["join"].join(
            t["change"].format(
                scenario=r["scenario"],
                a=_sig3(r["baseline"]),
                b=_sig3(r["value"]),
                pct=_pct(r["pct"] or 0.0),
            )
            for r in first
        )
        or "—"
    )
    md: list[str] = [f"# {title(doc, lang)}", "", f"## {t['conclusion']}", ""]
    if not doc.comparable:
        why = "; ".join(r.detail or r.code for r in doc.reasons) or "—"
        md += [t["not_comparable"].format(why=why), ""]
    md += [
        t["lead"].format(model=model, card=card, changes=changes),
        "",
        "```chart\ntype: summary\n```",
        "",
    ]
    for a in doc.attempts:
        md += [
            f"### {t['optimized']}" + (f" {a.position}" if len(doc.attempts) > 1 else ""),
            "",
            f"```command\nconfig: {a.position}\n```",
            "",
        ]
    md += [
        f"## {t['setup']}",
        "",
        f"### {t['env']}",
        "",
        "```table\ntype: setup\n```",
        "",
        "```table\ntype: slo\n```",
        "",
        f"### {t['vars']}",
        "",
    ]
    for a in doc.attempts:
        md += [f"```table\ntype: diff\nattempt: {a.position}\n```", ""]
        flags = _changed_flags(doc, a.position)
        md += [
            (
                t["vars_text"].format(flags=", ".join(f"`{f}`" for f in flags))
                if flags
                else t["vars_none"]
            ),
            "",
        ]
    md += [
        f"### {t['scenarios']}",
        "",
        "```table\ntype: scenarios\n```",
        "",
        f"## {t['results']}",
        "",
        f"### {t['capacity']}",
        "",
        "```table\ntype: summary\n```",
        "",
    ]
    for r in first:
        md += [
            t["capacity_row"].format(
                scenario=r["scenario"],
                bc=r["base_conc"] if r["base_conc"] is not None else "—",
                c=r["conc"] if r["conc"] is not None else "—",
                a=_sig3(r["baseline"]),
                b=_sig3(r["value"]),
                pct=_pct(r["pct"] or 0.0),
            ),
            "",
        ]
    if doc.baseline.results.scenarios:
        md += [f"### {t['scaling']}", ""]
    for s in doc.baseline.results.scenarios:
        kind = "agentic" if s.kind == "replay" else "sweep"
        md += [
            f"#### {s.label or s.key}",
            "",
            f"```chart\ntype: {kind}\nscenario: {s.key}\n```",
            "",
        ]
    quality = _quality(doc)
    if quality:
        md += [f"### {t['quality']}", "", "```table\ntype: quality\n```", ""]
        for name, b, a in quality:
            pct = (a - b) / b * 100 if b else 0.0
            md += [
                t["quality_row"].format(name=name, a=b, b=a, pp=(a - b) * 100, pct=_pct(pct)),
                "",
            ]
    md += [
        f"## {t['appendix']}",
        "",
        f"### {t['baseline']}",
        "",
        "```command\nconfig: baseline\n```",
        "",
        t["generated"],
        "",
    ]
    return "\n".join(md)
