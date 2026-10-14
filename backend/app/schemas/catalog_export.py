"""model-catalog export: request and response."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CatalogVariantIn(BaseModel):
    """One config to publish as a variant: a measured run (a campaign's
    baseline, its winner, or any other run with a benchmark result)."""

    run_id: int
    role: str = ""  # baseline | optimized | ""
    # A human name for the provenance lines; the campaign's name and the run
    # id when empty.
    label: str = ""
    id: str = ""  # variant id; derived (<engine>-<parallelism>-<card>-<role>) when empty
    description: str = ""  # derived from the measurement when empty
    # Exact nvidia.com/gpu.product values (e.g. NVIDIA-H100-80GB-HBM3). The
    # platform keeps only the normalized card name, so this is never guessed.
    gpu_product: list[str] = Field(default_factory=list)
    nodes: int | None = None  # pods per group; 1 when empty


class CatalogExportRequest(BaseModel):
    variants: list[CatalogVariantIn] = Field(min_length=1)
    name: str = ""  # catalog id; derived from the served model name when empty
    version: str = "1.0.0"
    served_name: str = ""
    chart_version: str = ""
    default_variant: str = ""
    include_env: bool = True
    include_volumes: bool = False
    # Empty = 90 minutes, raised to 3x the measured load when that is longer.
    startup_minutes: int | None = Field(default=None, ge=1, le=240)
    # nvidia.com/gpu.product labels for every variant without its own; empty =
    # the known label of the card each config was measured on.
    gpu_product: list[str] = Field(default_factory=list)
    with_metadata: bool = True
    hf: str = ""
    hf_revision: str = ""
    display_name: str = ""
    description: str = ""
    family: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    homepage: str = ""
    # The latest published version of this model, as YAML text. Given, the
    # export is that version extended (its variants and default kept, the new
    # ones appended, the version bumped); omit for a model new to the catalog.
    base_entry: str = ""
    # A saved perf report (either language) to ship next to the entry. It must
    # compare exactly the runs being exported.
    report_id: int | None = None


class CatalogExportOut(BaseModel):
    path: str
    text: str
    document: dict[str, Any]
    metadata_path: str = ""
    metadata_text: str = ""
    metadata: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    valid: bool
    # Where each variant came from, for the pull request description.
    provenance: list[str] = Field(default_factory=list)
    # The report file to commit next to the entry, when report_id was given.
    report_path: str = ""
    report_html: str = ""
    # What the form should show when the caller left fields empty.
    name: str
    version: str
    hf: str = ""
    display_name: str = ""
    description: str = ""
    family: str = ""
    tags: list[str] = Field(default_factory=list)
