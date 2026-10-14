"""model-catalog export: measured runs as a `catalog.swiss/v1` version file.

Pick the runs to publish (a campaign's baseline and its winner, or any runs
with a benchmark result), get back the version file and, for a model the
catalog does not have yet, its metadata.yaml. Nothing is written or pushed:
the response carries the text, the UI downloads it, and publishing is a pull
request someone opens against model-catalog. The builder lives in
app/control/catalog_export; this module only turns run ids into
LaunchConfigs and evidence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import comparison as cmp
from app.agent import export as report_export
from app.agent.loader import load_run
from app.control.catalog_export import (
    DEFAULT_CHART_VERSION,
    CatalogOptions,
    VariantSource,
    build_catalog_entry,
    slug,
    split_image,
)
from app.control.launch_config import LaunchConfig
from app.core.auth import Principal, get_principal
from app.datasets.pinning import dataset_of
from app.db.base import get_async_session
from app.db.models import AgentReport, Campaign, Candidate, Event, Machine, Result, Run, RunStatus
from app.hardware import normalize_gpu_type
from app.objective import direction, target_metric
from app.schemas.catalog_export import (
    CatalogExportOut,
    CatalogExportRequest,
    CatalogVariantIn,
)

router = APIRouter(prefix="/catalog-export", tags=["catalog-export"])

_LOADING = {RunStatus.LAUNCHING.value, RunStatus.WAITING_READY.value}
_LOADED = {RunStatus.HEALTH_CHECK.value, RunStatus.BENCHING.value, RunStatus.SERVING.value}


async def _load_seconds(session: AsyncSession, run_id: int) -> float | None:
    """Launch to healthy for one run, from its transition events: what the
    catalog's startup budget is sized against."""
    events = (
        (
            await session.execute(
                select(Event)
                .where(Event.run_id == run_id, Event.kind == "run_transition")
                .order_by(Event.id)
            )
        )
        .scalars()
        .all()
    )
    start = next((e.ts for e in events if (e.payload or {}).get("to") in _LOADING), None)
    ready = next((e.ts for e in events if (e.payload or {}).get("to") in _LOADED), None)
    if start is None or ready is None or ready <= start:
        return None
    return (ready - start).total_seconds()


def _digest_of(snapshot: dict | None, image: str) -> str:
    """The manifest digest the run pulled, when the snapshot has one for THIS
    image's repository (`repo@sha256:...` from RepoDigests)."""
    ref = str((snapshot or {}).get("image_digest") or "")
    repo, _, digest = ref.partition("@")
    return digest if digest and repo == split_image(image)[0] else ""


async def _latest_result(session: AsyncSession, run_id: int) -> Result | None:
    return (
        (
            await session.execute(
                select(Result)
                .where(Result.run_id == run_id, Result.source == "llmbench")
                .order_by(Result.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _card_type(session: AsyncSession, run: Run) -> str:
    snap = run.env_snapshot or {}
    card = snap.get("card_type") or snap.get("machine_gpu_type") or ""
    if not card and run.machine_id:
        machine = await session.get(Machine, run.machine_id)
        card = machine.gpu_type if machine is not None else ""
    return normalize_gpu_type(card or "")


async def _from_run(session: AsyncSession, item: CatalogVariantIn) -> tuple[VariantSource, str]:
    run = await session.get(Run, item.run_id)
    campaign = await session.get(Campaign, run.campaign_id) if run is not None else None
    candidate = await session.get(Candidate, run.candidate_id) if run is not None else None
    if run is None or campaign is None or candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no run {item.run_id} on record")
    result = await _latest_result(session, run.id)
    if result is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"run {run.id} has no benchmark result; nothing was measured"
        )
    metric = target_metric(campaign.objective or {})
    value = result.objective_value
    if value is None:
        value = (result.metrics or {}).get(metric)
    source = VariantSource(
        config=LaunchConfig.from_campaign(
            campaign, dict(candidate.config or {}), gpu_type=await _card_type(session, run)
        ),
        role=item.role,
        label=item.label or f"{campaign.name} run {run.id}",
        nodes=item.nodes or 1,
        evidence={
            "metric": metric,
            "value": value,
            "benchmark": campaign.benchmark_slug,
            # How this result is comparable to others: the replayed build.
            "dataset_build": (dataset_of(result.metrics or {}) or ("", ""))[0],
            "feasible": result.feasible,
            "direction": direction(campaign.objective or {}),
        },
        load_seconds=await _load_seconds(session, run.id),
        image_digest=_digest_of(run.env_snapshot, campaign.image),
        origin={"kind": "campaign_run", "campaign_id": campaign.id, "run_id": run.id},
        id=item.id,
        description=item.description,
        gpu_product=list(item.gpu_product),
    )
    return source, campaign.served_model_name


# Quality run fields that are timings or token counts, not scores.
_QUALITY_NOT_SCORES = {
    "elapsed_s",
    "gen_tokens",
    "output_tps",
    "service_tps",
    "tpot_mean_ms",
    "ttft_mean_ms",
    "score",
}
_QUALITY_NAMES = {"gpqa_diamond": "GPQA Diamond"}


async def _attach_comparison(session: AsyncSession, sources: list[VariantSource]) -> None:
    """Give every optimized variant the numbers its report would show: the
    change in normalized throughput at each scenario's best concurrency within
    the SLO, and the change in each quality score, against the baseline
    variant. The same comparison the reports are written from, so an entry
    and its report can never disagree. Best-effort: without that, the
    description falls back to the single ranking metric."""
    base = next((s for s in sources if s.role == "baseline"), None)
    if base is None:
        return
    base_bundle = await load_run(session, int(base.origin["run_id"]))
    if base_bundle is None:
        return
    for src in sources:
        if src.role != "optimized":
            continue
        bundle = await load_run(session, int(src.origin["run_id"]))
        if bundle is None:
            continue
        reasons = cmp.comparability(base_bundle, [bundle])
        doc = cmp.build_comparison(base_bundle, [bundle], reasons=reasons).model_dump()
        attempt = doc["attempts"][0]
        labels = {
            sc["key"]: sc.get("label") or sc["key"]
            for sc in doc["baseline"]["results"]["scenarios"]
        }
        scenarios = []
        for sc in attempt["deltas"]["scenarios"]:
            change = ((sc.get("best_level") or {}).get("total_tps_per_machine")) or {}
            if change.get("pct") is not None:
                scenarios.append(
                    {
                        "key": sc["key"],
                        "label": labels.get(sc["key"], sc["key"]),
                        "pct": float(change["pct"]),
                    }
                )
        quality = []
        for key, change in (attempt["deltas"].get("quality") or {}).items():
            short = key.split(".", 1)[-1]
            if short in _QUALITY_NOT_SCORES or not change:
                continue
            b, a = change.get("baseline"), change.get("attempt")
            if (
                isinstance(b, (int, float))
                and isinstance(a, (int, float))
                and 0 <= b <= 1
                and 0 <= a <= 1
            ):
                quality.append(
                    {
                        "name": _QUALITY_NAMES.get(short, short),
                        "baseline": float(b),
                        "attempt": float(a),
                    }
                )
        src.evidence = {
            **(src.evidence or {}),
            "scenarios": scenarios,
            "quality": quality,
            "not_comparable": [
                str(r.get("detail") or r.get("code") or r) for r in doc.get("reasons") or []
            ],
        }


async def _report_for(
    session: AsyncSession, report_id: int, sources: list[VariantSource], name: str
) -> tuple[str, str, list[str]]:
    """The saved report's self-contained HTML, and where it goes in the
    catalog: refused unless it compares exactly the runs being exported."""
    report = await session.get(AgentReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no report {report_id}")
    frozen = report.comparison or {}
    got = {int((frozen.get("baseline") or {}).get("run_id") or 0)} | {
        int(a.get("run_id") or 0) for a in frozen.get("attempts") or []
    }
    want = {int(s.origin["run_id"]) for s in sources}
    errors = []
    if got != want:
        errors.append(
            f"report {report_id} compares runs {sorted(got)}, the entry is from runs {sorted(want)}"
        )
    root = report.translation_of or report.id
    rows = (
        (
            await session.execute(
                select(AgentReport)
                .where((AgentReport.id == root) | (AgentReport.translation_of == root))
                .order_by(AgentReport.id)
            )
        )
        .scalars()
        .all()
    )
    try:
        html = report_export.render_html(report, list(rows) or [report])
    except report_export.RendererMissing as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    card = next((s.config.gpu_type for s in sources if s.config.gpu_type), "").lower() or "gpu"
    return f"models/{name}/{name}-{card}-report.html", html, errors


@router.post("/preview", response_model=CatalogExportOut)
async def preview_catalog_export(
    body: CatalogExportRequest,
    _: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_async_session),
):
    """The catalog entry for the given runs, validated against the catalog's
    schema. Nothing is saved."""
    sources: list[VariantSource] = []
    model_names: list[str] = []
    for item in body.variants:
        source, model_name = await _from_run(session, item)
        sources.append(source)
        model_names.append(model_name)
    await _attach_comparison(session, sources)

    name = body.name.strip() or slug(next((n for n in model_names if n), ""))
    opts = CatalogOptions(
        name=name,
        version=body.version.strip() or "1.0.0",
        served_name=body.served_name.strip(),
        chart_version=body.chart_version.strip() or DEFAULT_CHART_VERSION,
        default_variant=body.default_variant.strip(),
        include_env=body.include_env,
        include_volumes=body.include_volumes,
        startup_minutes=body.startup_minutes,
        with_metadata=body.with_metadata,
        hf=body.hf.strip(),
        hf_revision=body.hf_revision.strip(),
        display_name=body.display_name.strip(),
        description=body.description.strip(),
        family=body.family.strip(),
        tags=list(body.tags),
        license=body.license.strip(),
        homepage=body.homepage.strip(),
        model_label=next((n for n in model_names if n), ""),
        gpu_product=[p.strip() for p in body.gpu_product if p.strip()],
        base_text=body.base_entry,
    )
    try:
        export = build_catalog_entry(sources, opts)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    report_path = report_html = ""
    if body.report_id is not None:
        report_path, report_html, report_errors = await _report_for(
            session, body.report_id, sources, export.document["name"]
        )
        export.errors += report_errors
    meta = export.metadata or {}
    return CatalogExportOut(
        **export.payload(),
        report_path=report_path,
        report_html=report_html,
        name=export.document["name"],
        version=export.document["version"],
        hf=(meta.get("source") or {}).get("hf", ""),
        display_name=meta.get("displayName", ""),
        description=meta.get("description", ""),
        family=meta.get("family", ""),
        tags=list(meta.get("tags") or []),
    )
