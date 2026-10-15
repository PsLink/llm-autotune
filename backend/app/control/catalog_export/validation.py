"""CI routing sidecar; catalog YAML and its schema stay unchanged."""

import hashlib
import json
from pathlib import PurePosixPath

from app.control.catalog_export import CatalogExport, VariantSource


def validation_file(export: CatalogExport, sources: list[VariantSource]) -> tuple[str, str]:
    if not export.valid:
        return "", ""
    if any(not source.origin.get("track_slug") for source in sources):
        export.warnings.append(
            "CI validation file unavailable: every selected run must belong to an original track."
        )
        return "", ""
    variants = [
        {
            "variant_id": variant_id,
            "track_slug": source.origin["track_slug"],
            "source_run_id": source.origin["run_id"],
            "role": "baseline" if source.role == "baseline" else "optimized",
        }
        for variant_id, source in zip(export.source_variant_ids, sources, strict=True)
    ]
    path = PurePosixPath(export.path)
    document = {
        "schema_version": 1,
        "catalog_file": path.name,
        "catalog_sha256": hashlib.sha256(export.text.encode("utf-8")).hexdigest(),
        "variants": variants,
    }
    return str(path.with_suffix(".validation.json")), json.dumps(
        document, ensure_ascii=False, indent=2
    ) + "\n"
