"""model-catalog export: measured configs become one catalog.swiss/v1 version
file that the catalog's own schema accepts, carrying nothing site-owned."""

from itertools import count

import httpx
import pytest
import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.control.catalog_export import (
    CatalogOptions,
    VariantSource,
    build_catalog_entry,
    extra_args,
    guess_hf,
    split_image,
    validate,
)
from app.control.launch_config import LaunchConfig
from app.core.auth import create_token
from app.db.base import Base, get_async_session
from app.db.models import Campaign, Candidate, Result, Run, User
from app.main import app

_counter = count()


def _config(**overrides) -> LaunchConfig:
    base = dict(
        engine="sglang",
        image="registry.example.com:5000/team/sglang:v0.5.15-cu129",
        model_path="/models/Qwen/Qwen3.5-35B-A3B",
        served_model_name="qwen3.5",
        engine_args={
            "tp": 2,
            "mem_fraction_static": 0.83,
            "enable_cache_report": True,
            "host": "0.0.0.0",
            "port": 28200,
        },
        extra_env={"PYTORCH_ALLOC_CONF": "expandable_segments:True", "CUDA_VISIBLE_DEVICES": "0,1"},
        extra_volumes={"/root/moe-config": "/moe-config:ro"},
        gpu_type="H100",
    )
    return LaunchConfig(**{**base, **overrides}).normalized()


def _pair() -> list[VariantSource]:
    baseline = _config()
    tuned = _config(
        engine_args={**baseline.engine_args, "mem_fraction_static": 0.88, "context_length": 131072}
    )
    return [
        VariantSource(
            baseline,
            role="baseline",
            evidence={"metric": "tps", "value": 100.0, "benchmark": "b82"},
        ),
        VariantSource(
            tuned, role="optimized", evidence={"metric": "tps", "value": 125.0, "benchmark": "b82"}
        ),
    ]


def test_a_baseline_and_its_optimized_sibling_are_two_variants_of_one_version():
    out = build_catalog_entry(_pair(), CatalogOptions(name="qwen3.5-35b-a3b", version="1.1.0"))
    assert out.errors == []
    assert out.path == "models/qwen3.5-35b-a3b/qwen3.5-35b-a3b-1.1.0.yaml"
    doc = yaml.safe_load(out.text)
    assert doc == out.document, "the text on disk is the document validated"
    assert doc["servedName"] == "qwen3.5"
    base, tuned = doc["variants"]
    assert base["id"] == "sglang-tp2-h100-baseline"
    assert tuned["id"] == "sglang-tp2-h100-optimized"
    assert tuned["default"] is True and "default" not in base
    assert tuned["description"] == (
        "Tuned by LLM AutoTune: TP2 on two H100 GPUs, single node."
        " +25% on the tuning benchmark vs the baseline variant."
    )
    assert base["image"] == {
        "repository": "registry.example.com:5000/team/sglang",
        "tag": "v0.5.15-cu129",
    }
    assert base["requires"] == {
        "gpus": 2,
        "topology": "single-node",
        "gpuProduct": ["NVIDIA-H100-80GB-HBM3"],
    }


def test_nothing_site_owned_or_chart_owned_is_published():
    out = build_catalog_entry(_pair(), CatalogOptions(name="q"))
    base, tuned = out.document["variants"]
    values = base["values"]
    assert values["extraArgs"] == [
        "--enable-cache-report",
        "--mem-fraction-static=0.83",
        "--stream-response-default-include-usage",
        "--tp-size=2",
    ], "host/port are the chart's; the serving contract is always there"
    assert "localPath" not in values["model"]
    assert values["model"]["contextLength"] == "", "never pinned unless the config pinned it"
    assert tuned["values"]["model"]["contextLength"] == "131072"
    assert not any("context-length" in a for a in tuned["values"]["extraArgs"])
    assert values["env"] == [
        {"name": "NVIDIA_DISABLE_REQUIRE", "value": "1"},
        {"name": "PYTORCH_ALLOC_CONF", "value": "expandable_segments:True"},
    ], "the driver's defaults ride along; CUDA_VISIBLE_DEVICES is the platform's"
    assert [v["name"] for v in values["volumes"]] == ["dot-cache", "dshm"], "no host paths"
    assert any("volumes not exported" in w for w in out.warnings)
    assert "/models/" not in out.text


def test_a_config_without_extra_env_still_exports_the_env_it_ran_with():
    out = build_catalog_entry(
        [VariantSource(_config(extra_env={}), role="baseline")], CatalogOptions(name="q")
    )
    env = out.document["variants"][0]["values"]["env"]
    assert env == [
        {"name": "NVIDIA_DISABLE_REQUIRE", "value": "1"},
        {"name": "PYTORCH_ALLOC_CONF", "value": "expandable_segments:True"},
    ]
    own = build_catalog_entry(
        [
            VariantSource(
                _config(extra_env={"PYTORCH_ALLOC_CONF": "x", "SGLANG_FOO": "1"}), role="baseline"
            )
        ],
        CatalogOptions(name="q"),
    )
    assert own.document["variants"][0]["values"]["env"] == [
        {"name": "NVIDIA_DISABLE_REQUIRE", "value": "1"},
        {"name": "PYTORCH_ALLOC_CONF", "value": "x"},
        {"name": "SGLANG_FOO", "value": "1"},
    ], "the config's own value wins over the default"


def test_a_volume_an_engine_flag_reads_from_is_kept():
    cfg = _config(
        engine_args={
            "tp": 2,
            "speculative_algorithm": "EAGLE3",
            "speculative_draft_model_path": "/spec-model",
        },
        extra_volumes={
            "/mnt/disk0/models/EAGLE3-x": "/spec-model",
            "/root/moe-config": "/moe-config:ro",
        },
    )
    out = build_catalog_entry([VariantSource(cfg, role="optimized")], CatalogOptions(name="q"))
    assert out.errors == []
    values = out.document["variants"][0]["values"]
    assert values["volumes"][0] == {
        "name": "draft-model",
        "hostPath": {"path": "/mnt/disk0/models/EAGLE3-x", "type": "Directory"},
    }
    assert values["volumeMounts"][0] == {
        "name": "draft-model",
        "mountPath": "/spec-model",
        "readOnly": True,
    }
    assert [v["name"] for v in values["volumes"]] == ["draft-model", "dot-cache", "dshm"], (
        "moe-config stays out"
    )
    assert any("an engine flag reads from" in w for w in out.warnings)
    assert any("volumes not exported" in w and "/root/moe-config" in w for w in out.warnings)


def test_the_serving_contract_is_added_and_a_weights_file_pins_the_mount():
    cfg = _config(engine_args={"tp": 8, "chat_template": "/model/chat_template.jinja"})
    out = build_catalog_entry([VariantSource(cfg, role="baseline")], CatalogOptions(name="q"))
    values = out.document["variants"][0]["values"]
    assert "--enable-cache-report" in values["extraArgs"]
    assert "--stream-response-default-include-usage" in values["extraArgs"]
    assert any("serving-contract" in w for w in out.warnings)
    assert values["model"]["mountPath"] == "/model", "--chat-template reads from the weights"
    plain = build_catalog_entry(
        [
            VariantSource(
                _config(engine_args={"tp": 2, "enable_cache_report": False}), role="baseline"
            )
        ],
        CatalogOptions(name="q"),
    )
    pv = plain.document["variants"][0]["values"]
    assert "mountPath" not in pv["model"]
    assert "--enable-cache-report" not in pv["extraArgs"], (
        "an explicit False is the config's choice"
    )


def test_volumes_only_on_request():
    out = build_catalog_entry(_pair()[:1], CatalogOptions(name="q", include_volumes=True))
    values = out.document["variants"][0]["values"]
    assert values["volumes"][0]["hostPath"]["path"] == "/root/moe-config"
    assert values["volumeMounts"][0] == {
        "name": "moe-config",
        "mountPath": "/moe-config",
        "readOnly": True,
    }
    assert out.errors == []


def test_multi_node_is_an_lws_group_sized_per_pod():
    cfg = _config(engine_args={"tp": 8, "pp_size": 2})
    out = build_catalog_entry([VariantSource(cfg, nodes=2)], CatalogOptions(name="kimi-k2.5"))
    v = out.document["variants"][0]
    assert {k: x for k, x in v["requires"].items() if k != "gpuProduct"} == {
        "gpus": 8,
        "nodes": 2,
        "topology": "lws",
        "rdma": True,
    }
    assert v["values"]["resources"]["limits"]["rdma/hca_shared"] == "1"
    assert v["values"]["lws"] == {"enabled": True, "size": 2}
    assert out.errors == []


def test_metadata_needs_a_repo_id_and_says_when_it_guessed():
    out = build_catalog_entry(
        _pair(), CatalogOptions(name="q", family="Qwen", tags=["MoE", "chat"])
    )
    assert out.metadata["source"] == {"hf": "Qwen/Qwen3.5-35B-A3B"}
    assert out.metadata["family"] == "qwen" and out.metadata["tags"] == ["moe", "chat"]
    assert any("guessed" in w for w in out.warnings)
    assert validate(out.metadata, "metadata") == []

    none = build_catalog_entry(
        [VariantSource(_config(model_path="/data/weights"))], CatalogOptions(name="q")
    )
    assert none.metadata is None and none.metadata_text == ""


def test_the_schema_catches_what_the_builder_cannot_fix():
    out = build_catalog_entry(_pair(), CatalogOptions(name="q", version="v1"))
    assert any(e.startswith("version:") for e in out.errors)
    too_big = build_catalog_entry(
        [VariantSource(_config(engine_args={"tp": 16}))], CatalogOptions(name="q")
    )
    assert any("requires/gpus" in e for e in too_big.errors), "16 cards on one pod is refused"


def test_small_helpers():
    assert split_image("reg:5000/a/b:t@sha256:" + "a" * 64)[1] == "t"
    assert split_image("reg:5000/a/b") == ("reg:5000/a/b", "", "")
    assert guess_hf("/models/glm-5") == ""
    args, context, dropped = extra_args(
        "sglang", {"disable_radix_cache": False, "max_model_len": 8}
    )
    assert context == "8" and "--disable-radix-cache" not in args


# -- the endpoint ---------------------------------------------------------------


@pytest_asyncio.fixture
async def stack():
    name = f"ce_{next(_counter)}"
    db = f"sqlite+aiosqlite:///file:{name}?mode=memory&cache=shared&uri=true"
    engine = create_async_engine(db)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    async with factory() as session:
        session.add(User(id=1, username="admin", password_hash="x", role="admin"))
        await session.commit()
        token = create_token(await session.get(User, 1))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        http.headers["Authorization"] = f"Bearer {token}"
        yield http, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory):
    """A campaign with a baseline run and a tuned run, both measured, and a
    third run that never got a result."""
    async with factory() as s:
        s.add(
            Campaign(
                id=9,
                owner_id=1,
                name="qwen3.5 h800",
                engine="sglang",
                image="registry.example.com/team/sglang:v0.5.15",
                model_path="/models/Qwen/Qwen3.5-35B-A3B",
                served_model_name="Qwen3.5-35B-A3B",
                search_space={"grid": {"mem_fraction_static": [0.83, 0.88, 0.9]}},
                objective={"target_metric": "request_output_tps", "direction": "maximize"},
                benchmark_slug="bench-82",
            )
        )
        for run_id, mem, value in ((101, 0.83, 1000.0), (102, 0.88, 1200.0), (103, 0.9, None)):
            s.add(
                Candidate(
                    id=run_id,
                    campaign_id=9,
                    config_hash=f"h{run_id}",
                    config={"tp": 2, "mem_fraction_static": mem},
                )
            )
            s.add(
                Run(
                    id=run_id,
                    campaign_id=9,
                    candidate_id=run_id,
                    env_snapshot={"card_type": "H800"},
                )
            )
            if value is not None:
                s.add(
                    Result(
                        run_id=run_id,
                        source="llmbench",
                        objective_value=value,
                        feasible=True,
                        metrics={"request_output_tps": value},
                    )
                )
        await s.commit()


async def test_preview_exports_a_baseline_run_and_a_tuned_run(stack):
    http, factory = stack
    await _seed(factory)
    res = await http.post(
        "/api/catalog-export/preview",
        json={
            "variants": [
                {"run_id": 101, "role": "baseline"},
                {"run_id": 102, "role": "optimized", "label": "tuned"},
            ],
            "version": "1.0.0",
        },
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["valid"], out["errors"]
    assert out["name"] == "qwen3.5-35b-a3b", "named after the served model"
    assert out["path"] == "models/qwen3.5-35b-a3b/qwen3.5-35b-a3b-1.0.0.yaml"
    assert out["hf"] == "Qwen/Qwen3.5-35B-A3B"
    assert "campaign 9, run 101" in out["provenance"][0]
    assert out["provenance"][1].startswith("tuned: ")
    assert "run 101" not in out["text"], "platform ids stay out of the public file"
    ids = [v["id"] for v in out["document"]["variants"]]
    assert ids == ["sglang-tp2-h800-baseline", "sglang-tp2-h800-optimized"]
    assert (
        "+20% on the tuning benchmark vs the baseline variant"
        in out["document"]["variants"][1]["description"]
    )


@pytest.mark.parametrize(
    "variants,code",
    [
        ([{"run_id": 103}], 409),  # never measured
        ([{"run_id": 999}], 404),
        ([{"role": "baseline"}], 422),  # no run
        ([], 422),
    ],
)
async def test_preview_refuses_what_it_cannot_publish(stack, variants, code):
    http, factory = stack
    await _seed(factory)
    res = await http.post("/api/catalog-export/preview", json={"variants": variants})
    assert res.status_code == code, res.text


def test_metadata_is_drafted_from_the_model_and_the_configs():
    out = build_catalog_entry(
        _pair(), CatalogOptions(name="qwen3.5-35b-a3b", model_label="Qwen3.5-35B-A3B")
    )
    meta = out.metadata
    assert meta["displayName"] == "Qwen3.5-35B-A3B"
    assert meta["family"] == "qwen"
    assert meta["tags"] == ["chat", "moe"], "A3B reads as MoE; no parser, no reasoning tag"
    assert "variant" not in meta["description"], "metadata outlives any one version"
    assert meta["description"].startswith("Qwen3.5-35B-A3B on 2 H100 GPUs")

    # The caller's values always win over the draft.
    given = build_catalog_entry(
        _pair(),
        CatalogOptions(
            name="q",
            model_label="Qwen3.5",
            display_name="Mine",
            tags=["x"],
            description="hand-written",
            homepage="https://example.com",
        ),
    )
    assert given.metadata["displayName"] == "Mine"
    assert given.metadata["tags"] == ["x"]
    assert given.metadata["description"] == "hand-written"
    assert given.metadata["homepage"] == "https://example.com"
    assert given.errors == []


def test_the_values_follow_the_catalogs_newest_entries():
    src = _pair()[1]
    out = build_catalog_entry([src], CatalogOptions(name="q"))
    v = out.document["variants"][0]
    values = v["values"]
    assert v["chart"]["version"] == "0.7.1"
    assert values["modelCheck"]["enabled"] is True
    assert values["resources"]["limits"]["ephemeral-storage"] == "16Gi"
    assert values["lifecycle"]["preStop"]["drainSeconds"] == 600
    assert values["terminationGracePeriodSeconds"] == 3600
    assert values["hangWatcher"] == {"enabled": True}
    probe = values["startupProbe"]
    assert probe["periodSeconds"] * probe["failureThreshold"] == 90 * 60, "the 90-minute floor"
    assert out.text.startswith("# Serving configs measured"), "a short header"
    assert "run " not in out.text and "Review before" not in out.text
    assert out.errors == []


def test_a_measured_load_only_raises_the_startup_budget():
    slow = VariantSource(_config(), load_seconds=45 * 60)  # 3 x 45 = 135 minutes
    fast = VariantSource(_config(), load_seconds=5 * 60)
    out = build_catalog_entry([slow, fast], CatalogOptions(name="q"))
    a, b = (v["values"]["startupProbe"] for v in out.document["variants"])
    assert a["failureThreshold"] * 30 == 135 * 60
    assert b["failureThreshold"] * 30 == 90 * 60


def test_unknown_cards_and_dev_tags_are_flagged_not_guessed():
    cfg = _config(gpu_type="H20", image="registry.example.com/team/sglang:dev-dsv41")
    out = build_catalog_entry([VariantSource(cfg)], CatalogOptions(name="q"))
    v = out.document["variants"][0]
    assert "gpuProduct" not in v["requires"]
    assert any("no gpuProduct" in w for w in out.warnings)
    assert any("does not look like a release" in w for w in out.warnings)

    pinned = build_catalog_entry(
        [VariantSource(cfg, image_digest="sha256:" + "b" * 64)],
        CatalogOptions(name="q", gpu_product=["NVIDIA-H20"]),
    )
    v = pinned.document["variants"][0]
    assert v["image"]["digest"] == "sha256:" + "b" * 64
    assert v["requires"]["gpuProduct"] == ["NVIDIA-H20"]
    assert pinned.errors == []


def test_speculative_and_quantized_configs_are_described_and_tagged():
    cfg = _config(
        engine_args={
            "tp": 8,
            "speculative_algorithm": "eagle",
            "quantization": "fp8",
            "kv_cache_dtype": "fp8_e4m3",
        }
    )
    out = build_catalog_entry(
        [VariantSource(cfg, role="optimized")], CatalogOptions(name="q", model_label="MiMo-V2.5")
    )
    desc = out.document["variants"][0]["description"]
    assert "fp8 weights, fp8 KV cache, EAGLE speculative decoding" in desc
    assert "speculative-decoding" in out.metadata["tags"] and "fp8" in out.metadata["tags"]
    assert out.provenance and "run " not in out.text


# -- trust checks (2026-09-29) --------------------------------------------------


def test_a_flag_path_no_mount_provides_is_an_error():
    # The chart mounts weights at /models unless the entry pins model.mountPath.
    # A variant that reads /model/... without pinning it cannot find the file.
    from app.control.catalog_export import cross_check

    variant = {
        "id": "v",
        "values": {
            "model": {"hostPathType": "Directory"},
            "extraArgs": ["--chat-template=/model/chat_template.jinja", "--tp-size=8"],
            "volumeMounts": [{"name": "dshm", "mountPath": "/dev/shm"}],
        },
    }
    errors = cross_check({"variants": [variant]})
    assert any("/model/chat_template.jinja" in e and "no mount" in e for e in errors)
    variant["values"]["model"]["mountPath"] = "/model"
    assert cross_check({"variants": [variant]}) == []
    # a draft model is fine when a volume is mounted where the flag points
    variant["values"]["extraArgs"].append("--speculative-draft-model-path=/spec-model")
    assert cross_check({"variants": [variant]})
    variant["values"]["volumeMounts"].append({"name": "draft-model", "mountPath": "/spec-model"})
    assert cross_check({"variants": [variant]}) == []


def _judged_pair(scenarios, quality):
    pair = _pair()
    pair[1].evidence = {**pair[1].evidence, "scenarios": scenarios, "quality": quality}
    return pair


def test_the_description_carries_every_scenario_and_losses_are_flagged():
    pair = _judged_pair(
        [
            {"key": "a", "label": "50k + 1.5k", "pct": 89.49},
            {"key": "b", "label": "8k + 1k", "pct": -4.2},
        ],
        [{"name": "GPQA Diamond", "baseline": 0.9, "attempt": 0.8384}],
    )
    out = build_catalog_entry(pair, CatalogOptions(name="q"))
    tuned = out.document["variants"][1]
    assert "+89.5% (50k + 1.5k), -4.2% (8k + 1k)" in tuned["description"]
    assert "on the tuning benchmark" not in tuned["description"], "not the single ranking metric"
    assert any("does NOT beat the baseline on 8k + 1k" in w for w in out.warnings)
    assert any(
        "answer quality drops on GPQA Diamond" in w and "-6.16 points" in w for w in out.warnings
    )
    assert out.errors == [], "a reviewer decides; the export still validates"


def test_a_winning_pair_raises_no_judgement():
    pair = _judged_pair(
        [
            {"key": "a", "label": "50k + 1.5k", "pct": 5.5},
            {"key": "b", "label": "8k + 1k", "pct": 8.1},
        ],
        [{"name": "GPQA Diamond", "baseline": 0.8182, "attempt": 0.8485}],
    )
    out = build_catalog_entry(pair, CatalogOptions(name="q"))
    assert not any("does NOT beat" in w or "quality drops" in w for w in out.warnings)


BASE = """apiVersion: catalog.swiss/v1
name: q
version: 1.0.0
servedName: qwen
variants:
  - id: sglang-tp2-h100
    default: true
    engine: sglang
    # a reviewer's comment that must survive
    description: TP2 on two H100 GPUs.
    chart:
      name: sglang
      version: 0.7.1
    requires:
      gpus: 2
      topology: single-node
    values:
      model:
        mountPath: /models
      extraArgs:
        - --tp-size=2
"""


def test_an_existing_version_is_extended_not_rewritten():
    out = build_catalog_entry(_pair(), CatalogOptions(name="q", base_text=BASE))
    assert out.errors == [], out.errors
    assert out.path == "models/q/q-1.1.0.yaml"
    doc = out.document
    assert doc["version"] == "1.1.0" and doc["servedName"] == "qwen"
    ids = [v["id"] for v in doc["variants"]]
    assert ids == ["sglang-tp2-h100", "sglang-tp2-h100-baseline", "sglang-tp2-h100-optimized"]
    assert [v.get("default", False) for v in doc["variants"]] == [True, False, False], (
        "base default kept"
    )
    # the base's text, comments included, is untouched apart from the version line
    assert BASE.replace("version: 1.0.0", "version: 1.1.0").rstrip("\n") in out.text
    assert "# a reviewer's comment that must survive" in out.text
    assert any("servedName 'qwen'" in w for w in out.warnings)


def test_extending_can_move_the_default_and_refuses_id_clashes():
    moved = build_catalog_entry(
        _pair(),
        CatalogOptions(name="q", base_text=BASE, default_variant="sglang-tp2-h100-optimized"),
    )
    assert moved.errors == []
    assert [v.get("default", False) for v in moved.document["variants"]] == [False, False, True]
    clash = build_catalog_entry(
        _pair(),
        CatalogOptions(
            name="q", base_text=BASE.replace("sglang-tp2-h100\n", "sglang-tp2-h100-baseline\n")
        ),
    )
    assert any("already in 1.0.0" in e for e in clash.errors)
    wrong = build_catalog_entry(
        _pair(), CatalogOptions(name="q", base_text=BASE.replace("name: q", "name: z"))
    )
    assert any("is 'z', not 'q'" in e for e in wrong.errors)


def test_the_same_variant_ids_republish_a_corrected_patch_version():
    # A published 1.0.0 whose variants are the measured pair, but broken: the
    # export re-publishes them from the runs as 1.0.1, keeping the default.
    first = build_catalog_entry(
        _pair(), CatalogOptions(name="q", default_variant="sglang-tp2-h100-baseline")
    )
    broken = first.text.replace("contextLength: ''", "contextLength: '1'")
    out = build_catalog_entry(_pair(), CatalogOptions(name="q", base_text=broken))
    assert out.errors == [], out.errors
    assert out.path == "models/q/q-1.0.1.yaml"
    assert [v.get("default", False) for v in out.document["variants"]] == [True, False], (
        "base default kept"
    )
    assert out.document["variants"][0]["values"]["model"]["contextLength"] == "", (
        "rewritten from the run"
    )
    assert any(
        "replaces sglang-tp2-h100-baseline, sglang-tp2-h100-optimized of 1.0.0" in w
        for w in out.warnings
    )


def test_changes_round_the_way_the_report_prints_them():
    from app.control.catalog_export import pct_text

    assert pct_text(119.85) == "+119.9%", "half up, like the report; not Python's +119.8%"
    assert pct_text(9.99) == "+10.0%" and pct_text(-4.25) == "-4.3%" and pct_text(0.04) == "0.0%"
