"""model-catalog export: measured configs → one `catalog.swiss/v1` version file.

The public model catalog (github.com/modelsphere/model-catalog) describes how
a model should be served, and nothing about where. One version file per model
version, `models/<name>/<name>-<version>.yaml`, holding the variants it can be
served as. This module turns N measured LaunchConfigs (a baseline and the
config a campaign tuned from it, or several tuned configs) into that file, one
variant per config, plus a `metadata.yaml` for a model the catalog does not
have yet.

A published version is immutable, so there is no file to edit line by line:
we write a NEW document, dump it whole, and validate it against the catalog's
own schema (vendored under schema/ from model-catalog; re-copy when the
catalog changes it).

What is deliberately left out, because the catalog forbids or cannot use it:
- `model.localPath`, host paths, namespaces, scheduling, replicas: site- and
  deploy-owned layers, refused by swiss. The weights are named by `source.hf`
  in metadata.yaml, never by our model_path.
- Our extra_volumes are host paths, so they are dropped unless asked for --
  except one an engine flag reads from (a draft model), which the config
  cannot start without; the catalog's kimi-k3 entry mounts its draft that way.
- env is what the run had: the launch driver's defaults plus extra_env.
- Placement flags (model path, served name, host, port) are chart-owned; the
  chart renders them from `model.*`. `--context-length` moves to
  `model.contextLength` for the same reason.
- `gpuProduct` needs the exact node label ("NVIDIA-H100-80GB-HBM3"), and the
  platform only keeps the normalized card name, so it is written only when the
  caller supplies the labels.

Evidence (which run measured what, and by how much the optimized config won)
cannot live in the closed schema, so it rides in the variant `description` and
in a YAML comment header. Comments do not reach index.json, but they are in
the file a reviewer reads in the pull request.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.control.engine_command import PLACEMENT_FLAGS
from app.control.engines.base import MODEL_MOUNT
from app.control.engines.flags import negated_flag, render_flag
from app.control.launch_config import LaunchConfig

API_VERSION = "catalog.swiss/v1"
SCHEMA_DIR = Path(__file__).parent / "schema"
DEFAULT_CHART_VERSION = "0.7.1"
# Where the sglang chart mounts the weights when an entry does not say
# (values.yaml: model.mountPath). Every path a flag reads must live under it or
# under one of the entry's own volumeMounts.
CHART_MODEL_MOUNT = "/models"
ENGINES = ("sglang", "vllm")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
HF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")

# Chart-owned flags never go into extraArgs; context_length moves to
# model.contextLength instead of being dropped.
CHART_OWNED: frozenset[str] = frozenset(PLACEMENT_FLAGS | {"enable_metrics"})
# Every launch carries these; dropping them is expected, not worth a warning.
_ROUTINE_CHART_OWNED = frozenset({"model_path", "model", "served_model_name", "host", "port"})
_CONTEXT_KEYS = ("context_length", "max_model_len")
# Switches every entry of an engine serves with (see _variant).
SERVING_CONTRACT: dict[str, tuple[str, ...]] = {
    "sglang": ("enable_cache_report", "stream_response_default_include_usage"),
}

# Env the platform sets per launch, or that is plainly a credential: never
# published in a public catalog.
_PLATFORM_ENV = frozenset({"CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES"})
_SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|CREDENTIAL", re.I)

# nvidia.com/gpu.product values, as the catalog's own entries spell them. The
# platform keeps only the normalized card name ("H100"), so this is the one
# place a card is taught its node label; a card missing here gets no
# gpuProduct and a warning, never a guessed label that matches no node.
GPU_PRODUCTS: dict[str, list[str]] = {
    "A100": ["NVIDIA-A100-SXM4-80GB"],
    "H100": ["NVIDIA-H100-80GB-HBM3"],
    "H800": ["NVIDIA-H800"],
    "B300": ["NVIDIA-B300-SXM6-AC"],
}

# Startup budget: the newest catalog entries give every model 90 minutes
# (180 x 30s). A measured load only ever raises it — the platform's load is a
# warm one (cached image, often cached weights); a cold node is slower.
STARTUP_FLOOR_MINUTES = 90
STARTUP_MEASURED_FACTOR = 3
PROBE_PERIOD_SECONDS = 30

# A tag that reads as a release (v0.5.19, 0.5.15-cu129, v0.5.10.post1). Anything
# else (dev-dsv41, latest, a branch name) is likely to be moved to another build.
_RELEASE_TAG = re.compile(r"^v?\d+\.\d+(\.\d+)?([.-][0-9A-Za-z]+)*$")
_DEV_WORDS = re.compile(r"dev|latest|nightly|test|tmp|wip|snapshot", re.I)

# Top-level chart value keys a catalog entry must not write (swiss refuses
# them on load): the site profile's and the deploy form's layers.
_FOREIGN_VALUE_KEYS = frozenset(
    {
        "namespace",
        "replicaCount",
        "scaler",
        "modelRoute",
        "cart",
        "sloRequirement",
        "affinity",
        "nodeSelector",
        "tolerations",
        "cache",
    }
)


def fmt_value(value: Any) -> str:
    """A knob value as a flag spells it: 2.0 from a float-snapping policy is 2."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(value)


@dataclass
class VariantSource:
    """One config to publish as a variant, and what we know about it."""

    config: LaunchConfig
    role: str = ""  # baseline | optimized | "" — shapes the id and description
    label: str = ""  # a human name ("Qwen3.5 baseline v2")
    nodes: int = 1
    # {"metric", "value", "benchmark", "dataset_build", "stage", "feasible"}
    evidence: dict[str, Any] = field(default_factory=dict)
    # {"kind": "campaign_run", "campaign_id", "run_id", ...}
    origin: dict[str, Any] = field(default_factory=dict)
    id: str = ""  # explicit variant id; derived when empty
    description: str = ""  # explicit; derived when empty
    gpu_product: list[str] = field(default_factory=list)
    # How long the measured run took from launch to healthy, when known.
    load_seconds: float | None = None
    # sha256 manifest digest of the engine image the run pulled, when known.
    image_digest: str = ""


@dataclass
class CatalogOptions:
    name: str
    version: str = "1.0.0"
    served_name: str = ""  # "" → derived from the configs' served_model_name
    chart_version: str = DEFAULT_CHART_VERSION
    default_variant: str = ""  # an id; "" → the optimized one, else the first
    include_env: bool = True
    include_volumes: bool = False
    startup_minutes: int | None = None  # None → the floor, raised by a measured load
    # metadata.yaml — written when `hf` is known (a new model needs one)
    with_metadata: bool = True
    hf: str = ""
    hf_revision: str = ""
    display_name: str = ""
    description: str = ""
    family: str = ""
    tags: list[str] = field(default_factory=list)
    license: str = ""
    homepage: str = ""
    # The model's human name, for the drafted metadata.
    model_label: str = ""
    # Applied to every variant that has none of its own.
    gpu_product: list[str] = field(default_factory=list)
    # An already-published version of this model (its YAML text). When given,
    # the export is that version extended: its variants kept byte-for-byte and
    # still the default, the new ones appended, the version bumped.
    base_text: str = ""


@dataclass
class CatalogExport:
    path: str
    text: str
    document: dict[str, Any]
    metadata_path: str = ""
    metadata_text: str = ""
    metadata: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Where each variant came from (campaign / run ids, measured numbers).
    # Platform ids have no place in a public, immutable file, so this is for
    # the pull request description, not the entry.
    provenance: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def payload(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "text": self.text,
            "document": self.document,
            "metadata_path": self.metadata_path,
            "metadata_text": self.metadata_text,
            "metadata": self.metadata,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "valid": self.valid,
            "provenance": list(self.provenance),
        }


# -- small derivations ---------------------------------------------------------


def slug(text: str, limit: int = 63) -> str:
    """A catalog id: lowercase, [a-z0-9._-], starting alphanumeric."""
    out = re.sub(r"[^a-z0-9._-]+", "-", (text or "").lower()).strip("-._")
    return out[:limit].rstrip("-._")


def _id_slug(text: str) -> str:
    """A variant id segment: [a-z0-9-] only (no dots)."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def guess_hf(model_path: str) -> str:
    """`org/name` from a weights path like /models/Qwen/Qwen3.5-35B-A3B, when
    its last two segments look like one. A guess — the caller confirms it."""
    parts = [p for p in (model_path or "").rstrip("/").split("/") if p]
    if len(parts) >= 2:
        candidate = f"{parts[-2]}/{parts[-1]}"
        if HF_RE.match(candidate) and parts[-2] not in ("models", "model", "data", "mnt", "root"):
            return candidate
    return ""


def split_image(image: str) -> tuple[str, str, str]:
    """repository, tag, digest from `repo[:tag][@sha256:…]`. The tag separator
    is the last colon after the last slash, so a registry port survives."""
    ref, _, digest = (image or "").partition("@")
    slash = ref.rfind("/")
    colon = ref.rfind(":")
    if colon > slash:
        return ref[:colon], ref[colon + 1 :], digest
    return ref, "", digest


def _int(args: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in args:
            try:
                return max(1, int(float(args[key])))
            except (TypeError, ValueError):
                return 1
    return 1


def parallelism(args: dict[str, Any]) -> str:
    """ "tp8", "tp8-pp2", "tp4-dp4" — the id segment a variant is known by."""
    tp = _int(args, ("tp", "tp_size", "tensor_parallel_size"))
    pp = _int(args, ("pp", "pp_size", "pipeline_parallel_size"))
    dp = _int(args, ("dp", "dp_size", "data_parallel_size"))
    parts = [f"tp{tp}"]
    if pp > 1:
        parts.append(f"pp{pp}")
    if dp > 1:
        parts.append(f"dp{dp}")
    return "-".join(parts)


def extra_args(engine: str, args: dict[str, Any]) -> tuple[list[str], str, list[str]]:
    """Engine args as `--flag=value` items (the catalog's spelling), the
    context length pulled out for model.contextLength, and what was dropped.

    Sorted like render_args, so the same config always renders the same list.
    False switches render the engine's --no-x where one exists and are
    omitted otherwise — the same rule the launch command follows."""
    items: list[str] = []
    dropped: list[str] = []
    context = ""
    for key, value in sorted(args.items()):
        if key in _CONTEXT_KEYS:
            context = fmt_value(value)
            continue
        if key in CHART_OWNED:
            dropped.append(key)
            continue
        flag = render_flag(engine, key)
        if isinstance(value, bool):
            if value:
                items.append(flag)
            else:
                negated = negated_flag(engine, key)
                if negated is not None:
                    items.append(negated)
        elif value is None or value == "":
            continue
        else:
            items.append(f"{flag}={fmt_value(value)}")
    return items, context, dropped


# -- the builder ---------------------------------------------------------------


def _variant_id(src: VariantSource, gpus: int) -> str:
    if src.id:
        return _id_slug(src.id)[:63]
    cfg = src.config
    parts = [cfg.engine, parallelism(cfg.engine_args)]
    if cfg.gpu_type:
        parts.append(cfg.gpu_type)
    if src.nodes > 1:
        parts.append("lws")
    if src.role:
        parts.append(src.role)
    return _id_slug("-".join(parts))[:63] or f"variant-{gpus}"


def _fmt_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1000 else f"{value:,.0f}"
    return str(value)


_WORDS = {1: "one", 2: "two", 4: "four", 8: "eight"}


def features(args: dict[str, Any]) -> list[str]:
    """What a config does beyond its parallelism, in the catalog's words."""
    out = []
    quant = str(args.get("quantization") or "")
    if quant:
        out.append(f"{quant} weights")
    kv = str(args.get("kv_cache_dtype") or "")
    if kv and kv != "auto":
        out.append(f"{kv.split('_')[0]} KV cache")
    spec = str(args.get("speculative_algorithm") or "")
    if spec:
        out.append(f"{spec.upper()} speculative decoding")
    if args.get("enable_hierarchical_cache") is True:
        out.append("hierarchical KV cache")
    if args.get("enable_dp_attention") is True:
        out.append("DP attention")
    return out


def gain_pct(src: VariantSource, reference: VariantSource | None) -> float | None:
    """How much better this variant measured than the baseline one, in percent
    along the objective's own direction, when both were measured on the same
    metric and benchmark."""
    ev = src.evidence or {}
    ref = (reference.evidence or {}) if reference is not None else {}
    if (
        reference is None
        or reference is src
        or not ev.get("metric")
        or ref.get("metric") != ev.get("metric")
        or ref.get("benchmark", "") != ev.get("benchmark", "")
        or not isinstance(ev.get("value"), (int, float))
        or not isinstance(ref.get("value"), (int, float))
        or not ref["value"]
    ):
        return None
    raw = (float(ev["value"]) / float(ref["value"]) - 1.0) * 100.0
    return -raw if ev.get("direction") == "minimize" else raw


def _describe(src: VariantSource, gpus: int, reference: VariantSource | None) -> str:
    """Hardware and features, the way the catalog's entries read ("TP8 with
    DCP8 on one eight-GPU B300 node, fp8 KV cache"), plus the measured gain over
    the baseline variant. No metric keys or benchmark names: the file is public
    and frozen, and those are specific to one installation."""
    if src.description:
        return src.description[:512]
    cfg = src.config
    shape = parallelism(cfg.engine_args).upper().replace("-", " ")
    card = cfg.gpu_type or "GPU"
    count = _WORDS.get(gpus, str(gpus))
    noun = "GPU" if gpus == 1 else "GPUs"
    if src.nodes > 1:
        where = f"{count} {card} {noun} per node, {src.nodes} nodes"
    else:
        where = f"{count} {card} {noun}, single node"
    head = {"baseline": "Baseline", "optimized": "Tuned by LLM AutoTune"}.get(src.role, "")
    body = f"{shape} on {where}"
    extra = features(cfg.engine_args)
    if extra:
        body += ", " + ", ".join(extra)
    text = f"{head}: {body}." if head else f"{body}."
    scenarios = (src.evidence or {}).get("scenarios") or []
    if src.role == "optimized" and scenarios:
        # Every scenario, not only the ranking one: a +89% headline on the long
        # sweep next to +5% on the short one is a different claim.
        parts = [
            f"{pct_text(s['pct'])} ({s['label']})" for s in scenarios if s.get("pct") is not None
        ]
        if parts:
            text += (
                " Normalized throughput within the SLO vs the baseline variant: "
                + ", ".join(parts)
                + "."
            )
        return text[:512]
    gain = gain_pct(src, reference)
    if gain is not None:
        text += f" {gain:+.0f}% on the tuning benchmark vs the baseline variant."
    return text[:512]


def _env_items(env: dict[str, str], warnings: list[str], vid: str) -> list[dict[str, str]]:
    out = []
    for name, value in env.items():
        if name in _PLATFORM_ENV:
            continue
        if _SECRET_ENV.search(name):
            warnings.append(f"{vid}: env {name} looks like a credential and was left out")
            continue
        out.append({"name": name, "value": str(value)})
    return out


def runtime_env() -> dict[str, str]:
    """Env every launch driver sets under a config's own extra_env."""
    from app.control.launch import k8s, ssh_docker

    return {**ssh_docker.DEFAULT_ENV, **k8s.DEFAULT_ENV}


# Engine flags that read a directory the config mounts, and the volume name the
# catalog's own entries give it.
_PATH_FLAG_VOLUMES = {"speculative_draft_model_path": "draft-model"}


def _referenced_volumes(volumes: dict[str, str], engine_args: dict[str, Any]) -> dict[str, str]:
    """The host volumes some engine flag's value points into."""
    values = [str(v) for v in engine_args.values() if isinstance(v, str) and v.startswith("/")]
    needed = {}
    for host, container in volumes.items():
        path = str(container).partition(":")[0].rstrip("/")
        if path and any(v == path or v.startswith(path + "/") for v in values):
            needed[host] = container
    return needed


def _volume_names(volumes: dict[str, str], cfg: LaunchConfig) -> dict[str, str]:
    names = {}
    for host, container in volumes.items():
        path = str(container).partition(":")[0].rstrip("/")
        for flag, name in _PATH_FLAG_VOLUMES.items():
            value = str((cfg.engine_args or {}).get(flag) or "")
            if value == path or value.startswith(path + "/"):
                names[host] = name
    return names


def _volumes(
    volumes: dict[str, str], vid: str, names: dict[str, str] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    vols, mounts = [], []
    for index, (host, container) in enumerate(volumes.items()):
        container, _, mode = str(container).partition(":")
        name = (names or {}).get(host) or (
            _id_slug(Path(container).name or f"vol-{index}")[:50] or f"vol-{index}"
        )
        vols.append({"name": name, "hostPath": {"path": host, "type": "Directory"}})
        mount: dict[str, Any] = {"name": name, "mountPath": container}
        if mode == "ro" or name == "draft-model":
            mount["readOnly"] = True
        mounts.append(mount)
    return vols, mounts


def startup_minutes(src: VariantSource, opts: CatalogOptions) -> int:
    """The startup budget: the caller's, else the catalog's 90-minute floor,
    raised to 3x the measured load when that is longer. Rounded up to 5."""
    if opts.startup_minutes:
        return int(opts.startup_minutes)
    minutes = STARTUP_FLOOR_MINUTES
    if src.load_seconds:
        minutes = max(minutes, math.ceil(src.load_seconds * STARTUP_MEASURED_FACTOR / 60))
    return min(240, 5 * math.ceil(minutes / 5))


def _variant(
    src: VariantSource,
    opts: CatalogOptions,
    reference: VariantSource | None,
    warnings: list[str],
) -> dict[str, Any]:
    cfg = src.config
    nodes = max(1, int(src.nodes or 1))
    total = max(1, cfg.cards)
    gpus = total // nodes if total % nodes == 0 else total
    if total % nodes:
        warnings.append(f"{cfg.engine}: {total} cards do not split over {nodes} nodes evenly")
    vid = _variant_id(src, gpus)

    variant: dict[str, Any] = {"id": vid}
    variant["description"] = _describe(src, gpus, reference)
    variant["engine"] = cfg.engine
    variant["chart"] = {"name": cfg.engine, "version": opts.chart_version}

    repository, tag, digest = split_image(cfg.image)
    digest = digest or src.image_digest
    if repository and tag:
        variant["image"] = {"repository": repository, "tag": tag}
        if re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
            variant["image"]["digest"] = digest
        if not _RELEASE_TAG.match(tag) or _DEV_WORDS.search(tag):
            warnings.append(
                f"{vid}: image tag {tag!r} does not look like a release; a moved tag changes"
                " what this frozen version deploys"
                + (
                    " (the measured digest is pinned)"
                    if "digest" in variant["image"]
                    else " -- use a release tag, or pin image.digest"
                )
            )
    else:
        warnings.append(
            f"{vid}: image {cfg.image!r} has no tag, so no image was written"
            " (the chart's default applies)"
        )

    requires: dict[str, Any] = {"gpus": gpus}
    if nodes > 1:
        requires.update({"nodes": nodes, "topology": "lws", "rdma": True})
    else:
        requires["topology"] = "single-node"
    products = src.gpu_product or opts.gpu_product or GPU_PRODUCTS.get(cfg.gpu_type, [])
    if products:
        requires["gpuProduct"] = list(dict.fromkeys(products))
    else:
        warnings.append(
            f"{vid}: no gpuProduct -- no known node label for {cfg.gpu_type or 'this card'!r},"
            " so the variant can land on any card of the vendor; pass the label"
        )
    variant["requires"] = requires

    engine_args = dict(cfg.engine_args)
    # The serving contract every catalog entry carries: clients read cached
    # tokens and streamed usage from the response. A config measured
    # without them still measured the same throughput (they only add fields),
    # so they are added rather than leaving an entry that answers differently
    # from every other one. An explicit False is the config's choice and stays.
    missing = [key for key in SERVING_CONTRACT.get(cfg.engine, ()) if key not in engine_args]
    if missing:
        engine_args.update(dict.fromkeys(missing, True))
        warnings.append(
            f"{vid}: added the serving-contract flags the config lacked: {', '.join(missing)}"
        )
    args, context, dropped = extra_args(cfg.engine, engine_args)
    dropped = [key for key in dropped if key not in _ROUTINE_CHART_OWNED]
    if dropped:
        warnings.append(f"{vid}: chart-owned flags left to the chart: {', '.join(dropped)}")

    # The chart-0.7.1 shape of the catalog's newest entries (qwen3.6-35b-a3b,
    # kimi-k3, glm5.3): weights preflight, scratch and shm volumes, ephemeral
    # storage, a startup budget sized for a cold load, and a drain on the way
    # out so a scale-down or rollout does not cut requests in flight.
    values: dict[str, Any] = {
        # Never pinned unless the config pinned it: a number here silently caps
        # the model.
        "model": {"hostPathType": "Directory", "contextLength": context},
        "modelCheck": {"enabled": True, "requiredGlobs": ["config.json", "*.safetensors"]},
    }
    # A flag that reads a file out of the weights (--chat-template=/model/...)
    # depends on where the chart mounts them, so the mount is pinned whenever
    # one does, as the catalog's hand-written entries pin it.
    if any(str(v).startswith(MODEL_MOUNT + "/") for v in engine_args.values()):
        values["model"] = {"mountPath": MODEL_MOUNT, **values["model"]}
    if nodes > 1:
        values["lws"] = {"enabled": True, "size": nodes}
    values["extraArgs"] = args
    if opts.include_env:
        # The env the run actually had: the launch driver's defaults under the
        # config's own. A config measured without extra_env still ran with the
        # defaults, and an entry that drops them is not what was measured.
        env = _env_items({**runtime_env(), **cfg.extra_env}, warnings, vid)
        if env:
            values["env"] = env

    shm = "32Gi" if gpus >= 4 else "16Gi"
    volumes: list[dict[str, Any]] = [
        {"name": "dot-cache", "emptyDir": {"sizeLimit": "10Gi"}},
        {"name": "dshm", "emptyDir": {"medium": "Memory", "sizeLimit": shm}},
    ]
    mounts: list[dict[str, Any]] = [
        {"name": "dot-cache", "mountPath": "/root/.cache"},
        {"name": "dshm", "mountPath": "/dev/shm"},
    ]
    if cfg.extra_volumes:
        if opts.include_volumes:
            extra_vols, extra_mounts = _volumes(dict(cfg.extra_volumes), vid)
            volumes = extra_vols + volumes
            mounts = extra_mounts + mounts
            warnings.append(
                f"{vid}: host-path volumes are site facts in a public catalog: "
                + ", ".join(cfg.extra_volumes)
            )
        else:
            # A volume an engine flag points into (a draft model) is part of the
            # config: without it the flag names an empty path and the engine
            # exits at startup. The catalog's own kimi-k3 entry mounts its draft
            # the same way. Everything else stays site-owned.
            needed = _referenced_volumes(dict(cfg.extra_volumes), dict(cfg.engine_args))
            if needed:
                extra_vols, extra_mounts = _volumes(needed, vid, names=_volume_names(needed, cfg))
                volumes = extra_vols + volumes
                mounts = extra_mounts + mounts
                warnings.append(
                    f"{vid}: kept host volume(s) an engine flag reads from: "
                    + ", ".join(f"{h}:{c}" for h, c in needed.items())
                    + " -- the host path is this site's; a deployer must have the files there"
                )
            rest = {h: c for h, c in cfg.extra_volumes.items() if h not in needed}
            if rest:
                warnings.append(
                    f"{vid}: volumes not exported (host paths are site-owned): "
                    + ", ".join(f"{h}:{c}" for h, c in rest.items())
                )
    values["volumes"] = volumes
    values["volumeMounts"] = mounts

    storage = "28Gi" if nodes > 1 else "16Gi"
    values["resources"] = {
        "requests": {"ephemeral-storage": storage},
        "limits": {"ephemeral-storage": storage},
    }
    if nodes > 1:
        values["resources"]["limits"]["rdma/hca_shared"] = "1"

    # Cold-load time is a property of the model, which is why probes live in
    # the catalog: a default failureThreshold kills a long load in 45 seconds.
    minutes = startup_minutes(src, opts)
    values["progressDeadlineSeconds"] = minutes * 60 + 1800
    values["startupProbe"] = {
        "httpGet": {"path": "/health", "port": "http"},
        "periodSeconds": PROBE_PERIOD_SECONDS,
        "timeoutSeconds": 10,
        "failureThreshold": math.ceil(minutes * 60 / PROBE_PERIOD_SECONDS),
    }
    values["terminationGracePeriodSeconds"] = 3600
    values["lifecycle"] = {
        "preStop": {"drainSeconds": 600, "pollIntervalSeconds": 5},
        "forceShutdown": True,
        "preStopKill": True,
    }
    values["hangWatcher"] = {"enabled": True}
    variant["values"] = values
    return variant


def build_catalog_entry(sources: list[VariantSource], opts: CatalogOptions) -> CatalogExport:
    """The version file (and metadata.yaml) for these configs, validated."""
    warnings: list[str] = []
    errors: list[str] = []
    if not sources:
        raise ValueError("at least one config is needed")

    name = opts.name if NAME_RE.match(opts.name or "") else slug(opts.name)
    if not name:
        raise ValueError(f"catalog name {opts.name!r} has nothing usable in it")
    if name != opts.name:
        warnings.append(f"catalog name {opts.name!r} normalized to {name!r}")

    for src in sources:
        if src.config.engine not in ENGINES:
            errors.append(f"engine {src.config.engine!r} is not one the catalog serves")

    served = opts.served_name or next(
        (s.config.served_model_name for s in sources if s.config.served_model_name), ""
    )
    others = {s.config.served_model_name for s in sources if s.config.served_model_name}
    if len(others) > 1:
        warnings.append(
            f"the configs serve different names ({', '.join(sorted(others))});"
            f" one entry has one servedName, {served!r} was used"
        )

    reference = next((s for s in sources if s.role == "baseline"), None)
    variants = [_variant(s, opts, reference, warnings) for s in sources]
    for variant, src in zip(variants, sources, strict=True):
        warnings += judge(variant["id"], src)

    seen: dict[str, int] = {}
    for variant in variants:
        base = variant["id"]
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            variant["id"] = f"{base[:60]}-{seen[base]}"

    if opts.base_text.strip():
        return _extend(opts, name, served, sources, variants, warnings, errors)

    default_id = opts.default_variant
    if not default_id:
        optimized = [v for v, s in zip(variants, sources, strict=True) if s.role == "optimized"]
        default_id = (optimized[0] if optimized else variants[0])["id"]
    if default_id not in {v["id"] for v in variants}:
        errors.append(f"default variant {default_id!r} is not one of the exported variants")
    variants = _mark_default(variants, default_id)

    document: dict[str, Any] = {"apiVersion": API_VERSION, "name": name, "version": opts.version}
    if served and served != name:
        document["servedName"] = served
    document["variants"] = variants

    path = f"models/{name}/{name}-{opts.version}.yaml"
    text = HEADER + dump(document)
    errors += validate(document, "version")
    errors += cross_check(document)

    export = CatalogExport(
        path=path,
        text=text,
        document=document,
        warnings=warnings,
        errors=errors,
        provenance=provenance(sources),
    )

    if opts.with_metadata:
        hf = opts.hf or next((guess_hf(s.config.model_path) for s in sources), "")
        if not hf:
            warnings.append(
                "no Hugging Face repo id (source.hf) given or guessable from the model path;"
                " metadata.yaml was not generated — needed only if the model is new to the catalog"
            )
        else:
            if not opts.hf:
                warnings.append(f"source.hf {hf!r} was guessed from the model path; confirm it")
            draft = suggest_metadata(sources, opts.model_label or name)
            display = opts.display_name or draft["display_name"]
            description = opts.description or draft["description"]
            family = opts.family or draft["family"]
            tags = opts.tags or draft["tags"]
            meta: dict[str, Any] = {"apiVersion": API_VERSION, "name": name}
            if display:
                meta["displayName"] = display[:128]
            if description:
                meta["description"] = description[:2048]
            if opts.homepage:
                meta["homepage"] = opts.homepage
            if opts.license:
                meta["license"] = opts.license
            if family:
                meta["family"] = _id_slug(family)
            if tags:
                meta["tags"] = list(dict.fromkeys(_id_slug(t) for t in tags if _id_slug(t)))
            meta["source"] = {"hf": hf}
            if opts.hf_revision:
                meta["source"]["revision"] = opts.hf_revision
            export.metadata = meta
            export.metadata_path = f"models/{name}/metadata.yaml"
            export.metadata_text = dump(meta)
            export.errors += [f"metadata: {e}" for e in validate(meta, "metadata")]
    return export


def _mark_default(variants: list[dict[str, Any]], default_id: str) -> list[dict[str, Any]]:
    """`default` sits right after `id`, as in the catalog's examples."""
    return [
        {
            "id": v["id"],
            "default": True,
            **{k: x for k, x in v.items() if k not in ("id", "default")},
        }
        if v["id"] == default_id
        else {k: x for k, x in v.items() if k != "default"}
        for v in variants
    ]


def pct_text(pct: float) -> str:
    """A change rounded the way the perf report prints it (frontend/src/report/
    data.ts pctText: half up to one decimal), so an entry and its report never
    differ in the last digit -- 119.85 is +119.9%, where Python's own float
    formatting gives +119.8%."""
    rounded = math.floor(abs(pct) * 10 + 0.5 + 1e-6) / 10
    sign = "" if rounded == 0 else "+" if pct > 0 else "-"
    return f"{sign}{rounded:.1f}%"


def judge(vid: str, src: VariantSource) -> list[str]:
    """What a reviewer must see before this variant is published as better than
    the baseline: a scenario it does not win, answer quality it loses, or a
    comparison the platform itself calls not comparable."""
    ev = src.evidence or {}
    out = []
    for reason in ev.get("not_comparable") or []:
        out.append(f"{vid}: the baseline and this config are not comparable -- {reason}")
    if src.role != "optimized":
        return out
    for sc in ev.get("scenarios") or []:
        pct = sc.get("pct")
        if pct is not None and pct <= 0:
            out.append(
                f"{vid}: does NOT beat the baseline on {sc['label']} "
                f"({pct_text(pct)} normalized throughput within the SLO)"
            )
    for q in ev.get("quality") or []:
        points = (q["attempt"] - q["baseline"]) * 100
        if points < 0:
            out.append(
                f"{vid}: answer quality drops on {q['name']}: "
                f"{q['baseline']:.2%} -> {q['attempt']:.2%} ({points:+.2f} points)"
            )
    return out


def _version_tuple(v: str) -> tuple[int, int, int]:
    m = VERSION_RE.match(v or "")
    if not m:
        return (0, 0, 0)
    major, minor, patch = (int(x) for x in v.split("-", 1)[0].split("."))
    return (major, minor, patch)


def _extend(
    opts: CatalogOptions,
    name: str,
    served: str,
    sources: list[VariantSource],
    variants: list[dict[str, Any]],
    warnings: list[str],
    errors: list[str],
) -> CatalogExport:
    """A new version of an entry the catalog already has: the published
    version's text kept as it is -- its variants, its comments, its default --
    with the new variants appended after it and the version bumped.

    Published versions are immutable and other people's variants are theirs,
    so nothing in the base is rewritten; the only line that changes is
    `version:`. The new variants never become the default unless the caller
    names one of them."""
    base_text = opts.base_text.strip("\n") + "\n"
    try:
        base = yaml.safe_load(base_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the existing version is not valid YAML: {exc}") from exc
    if not isinstance(base, dict) or not isinstance(base.get("variants"), list):
        raise ValueError("the existing version has no variants list")
    if base.get("name") != name:
        errors.append(f"the existing version is {base.get('name')!r}, not {name!r}")
    if list(base)[-1] != "variants":
        raise ValueError("the existing version must end with its variants: block to be extended")

    base_version = str(base.get("version") or "")
    base_ids = {v.get("id") for v in base["variants"]}
    new_ids = {v["id"] for v in variants}
    # Same ids as published variants: a corrected re-publication of THOSE
    # variants (a patch release). All-new ids: more variants (a minor release).
    # A mix is ambiguous and refused.
    replacing = new_ids <= base_ids
    if new_ids & base_ids and not replacing:
        errors.append(
            f"variant ids {', '.join(sorted(new_ids & base_ids))} are already in {base_version} "
            "but "
            f"{', '.join(sorted(new_ids - base_ids))} are not: replace published variants or add "
            "new ones, not both -- pass explicit ids"
        )
    major, minor, patch = _version_tuple(base_version)
    bumped = f"{major}.{minor}.{patch + 1}" if replacing else f"{major}.{minor + 1}.0"
    version = opts.version
    if _version_tuple(version) <= _version_tuple(base_version):
        version = bumped
        warnings.append(
            f"version {opts.version!r} is not above the existing {base_version}; using {version}"
        )

    base_served = base.get("servedName") or base.get("name")
    if served and served != base_served:
        warnings.append(
            f"the measured runs served {served!r}; the entry keeps its servedName {base_served!r}"
        )

    base_default = next((v.get("id") for v in base["variants"] if v.get("default") is True), None)
    wanted = opts.default_variant or base_default or ""
    if wanted and wanted not in new_ids | base_ids:
        errors.append(f"default variant {wanted!r} is in neither the existing nor the new variants")

    if replacing:
        # Rewritten variant by variant from the measured runs; the rest of the
        # document (other variants, top-level keys) is the published one.
        fresh = {v["id"]: v for v in variants}
        merged = [fresh.get(v.get("id"), v) for v in base["variants"]]
        document = {**base, "version": version, "variants": _mark_default(merged, wanted)}
        comments = [
            line
            for line in base_text.splitlines()
            if line.lstrip().startswith("#") and line != HEADER.strip()
        ]
        if comments:
            warnings.append(
                f"{len(comments)} comment line(s) of {base_version} are not carried into {version}"
            )
        warnings.append(
            f"replaces {', '.join(sorted(new_ids))} of {base_version} with what was measured"
        )
        text = (HEADER if base_text.startswith(HEADER.strip()) else "") + dump(document)
        errors += validate(document, "version")
        errors += cross_check(document)
        return CatalogExport(
            path=f"models/{name}/{name}-{version}.yaml",
            text=text,
            document=document,
            warnings=warnings,
            errors=errors,
            provenance=provenance(sources),
        )

    if wanted in new_ids:
        # The caller moved the default to a new variant: the base's marker goes.
        base_text = re.sub(r"(?m)^    default: true\n", "", base_text)
        variants = _mark_default(variants, wanted)
    else:
        variants = _mark_default(variants, "")

    charts = {str((v.get("chart") or {}).get("version")) for v in base["variants"]}
    if charts and opts.chart_version not in charts:
        warnings.append(
            f"the existing variants use chart {', '.join(sorted(charts))}; "
            f"the new ones use {opts.chart_version}"
        )

    text = re.sub(r"(?m)^version: .*$", f"version: {version}", base_text, count=1)
    block = dump({"variants": variants}).split("\n", 1)[1]
    text = (
        text.rstrip("\n")
        + f"\n\n  # Added in {version}: measured, and where marked tuned, by LLM AutoTune.\n"
        + block
    )
    document = yaml.safe_load(text)
    errors += validate(document, "version")
    errors += cross_check(document)
    return CatalogExport(
        path=f"models/{name}/{name}-{version}.yaml",
        text=text,
        document=document,
        warnings=warnings,
        errors=errors,
        provenance=provenance(sources),
    )


HEADER = "# Serving configs measured, and where marked tuned, by LLM AutoTune.\n"


def provenance(sources: list[VariantSource]) -> list[str]:
    """One line per variant: which campaign and run measured it and what it
    scored. For the pull request description -- never the file."""
    lines = []
    for src in sources:
        origin = src.origin or {}
        ev = src.evidence or {}
        who = src.label or src.role or src.config.engine
        refs = ", ".join(
            f"{k.replace('_id', '')} {origin[k]}"
            for k in ("campaign_id", "run_id")
            if origin.get(k)
        )
        metric = (
            f"; {ev['metric']}={_fmt_metric(ev['value'])}"
            if ev.get("metric") and ev.get("value") is not None
            else ""
        )
        extra = "".join(
            f"; {k}={ev[k]}" for k in ("benchmark", "dataset_build", "stage") if ev.get(k)
        )
        load = f"; loaded in {src.load_seconds / 60:.0f} min" if src.load_seconds else ""
        lines.append(f"{who}: {refs or 'no run on record'}{metric}{extra}{load}")
    return lines


def suggest_metadata(sources: list[VariantSource], model_label: str) -> dict[str, Any]:
    """A first draft of metadata.yaml's cosmetic fields, from what the platform
    knows: the model's name and the configs being published. Every field is
    only a default — the caller's value always wins — and nothing is invented
    that the configs do not show (a reasoning tag needs a reasoning parser)."""
    label = (model_label or "").strip()
    lowered = label.lower()
    family_match = re.match(r"[a-z]+", lowered)
    family = family_match.group(0) if family_match else ""

    tags = ["chat"]
    if re.search(r"(?<![a-z0-9])a\d+(\.\d+)?b(?![a-z0-9])", lowered) or "moe" in lowered:
        tags.append("moe")
    args_all = [s.config.engine_args or {} for s in sources]
    if any(a.get("reasoning_parser") for a in args_all):
        tags.append("reasoning")
    if any(a.get("tool_call_parser") for a in args_all):
        tags.append("tool-use")
    if re.search(r"(?<![a-z])(vl|vision)(?![a-z])", lowered):
        tags.append("vision")
    if any(s.nodes > 1 for s in sources):
        tags.append("multi-node")
    if any(a.get("speculative_algorithm") for a in args_all):
        tags.append("speculative-decoding")
    for quant in ("fp8", "fp4"):
        if any(quant in str(a.get("quantization") or "") for a in args_all):
            tags.append(quant)

    # Metadata is shared by every version, so it describes the model and the
    # hardware it runs on -- never "2 variants: ...", which the next version
    # would make wrong.
    cards = []
    for src in sources:
        cfg = src.config
        noun = "GPU" if cfg.cards == 1 else "GPUs"
        where = f"{cfg.cards} {cfg.gpu_type} {noun}" if cfg.gpu_type else f"{cfg.cards} {noun}"
        if src.nodes > 1:
            where = f"{src.nodes} nodes of {cfg.cards // src.nodes} {cfg.gpu_type or ''} GPUs"
        if where not in cards:
            cards.append(where)
    feats = []
    for src in sources:
        for f in features(src.config.engine_args):
            if f not in feats:
                feats.append(f)
    served = next(
        (src.config.served_model_name for src in sources if src.config.served_model_name), ""
    )
    parts = [
        f"{label or 'This model'} on {' or '.join(cards)}"
        + (f', served as "{served}"' if served and served != label else "")
        + "."
    ]
    if feats:
        parts.append("Configs use " + ", ".join(feats) + ".")
    if any(src.nodes > 1 for src in sources):
        parts.append(
            "Does not fit on one node: a LeaderWorkerSet group, which needs the LWS"
            " controller and RDMA (rdma-shared-dev-plugin)."
        )
    return {
        "display_name": label,
        "description": " ".join(parts),
        "family": family,
        "tags": tags,
    }


# -- serialization and validation ---------------------------------------------


class _Dumper(yaml.SafeDumper):
    """Block style with sequences indented under their key, the way the
    catalog's hand-written entries are."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def dump(document: dict[str, Any]) -> str:
    return yaml.dump(
        document,
        Dumper=_Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )


_SCHEMAS: dict[str, dict[str, Any]] = {}


def schema(kind: str) -> dict[str, Any]:
    if kind not in _SCHEMAS:
        _SCHEMAS[kind] = json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text())
    return _SCHEMAS[kind]


def validate(document: dict[str, Any], kind: str) -> list[str]:
    """Schema errors, as `path: message` lines. Empty means it validates."""
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(schema(kind))
    out = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        out.append(f"{where}: {error.message}")
    return out


def cross_check(document: dict[str, Any]) -> list[str]:
    """The rules model-catalog's validate.sh adds on top of the schema, plus
    the layer ownership swiss enforces when it loads an entry."""
    out = []
    variants = document.get("variants") or []
    ids = [v.get("id") for v in variants]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        out.append(f"duplicate variant ids: {', '.join(dupes)}")
    defaults = sum(1 for v in variants if v.get("default") is True)
    if defaults > 1:
        out.append(f"{defaults} variants marked default, at most one allowed")
    for v in variants:
        values = v.get("values") or {}
        lws = values.get("lws") or {}
        if lws.get("enabled") and lws.get("size") != (v.get("requires") or {}).get("nodes"):
            out.append(f"{v.get('id')}: lws.size != requires.nodes")
        foreign = sorted(_FOREIGN_VALUE_KEYS & set(values))
        if foreign:
            out.append(f"{v.get('id')}: values writes site/deploy-owned keys: {', '.join(foreign)}")
        model = values.get("model") or {}
        for key in ("localPath", "gpus"):
            if key in model:
                out.append(f"{v.get('id')}: values.model.{key} is not the catalog's to set")
        for arg in unmounted_paths(v):
            out.append(
                f"{v.get('id')}: {arg} reads a path no mount provides (weights at "
                f"{model.get('mountPath') or CHART_MODEL_MOUNT}); the engine would not find it"
            )
    return out


def unmounted_paths(variant: dict[str, Any]) -> list[str]:
    """extraArgs whose absolute path lies under neither the weights mount (the
    chart's default when the entry pins none) nor one of the entry's volumes --
    the flag names a file that will not exist in the container."""
    values = variant.get("values") or {}
    mounts = [((values.get("model") or {}).get("mountPath")) or CHART_MODEL_MOUNT]
    mounts += [str(m.get("mountPath") or "") for m in values.get("volumeMounts") or []]
    roots = [m.rstrip("/") for m in mounts if m]
    out = []
    for arg in values.get("extraArgs") or []:
        _, eq, value = str(arg).partition("=")
        if (
            eq
            and value.startswith("/")
            and not any(value == r or value.startswith(r + "/") for r in roots)
        ):
            out.append(str(arg))
    return out
