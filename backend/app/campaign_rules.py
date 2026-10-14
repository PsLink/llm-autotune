"""What a campaign must satisfy to be saved, whoever creates it.

The API's create path (the form, an imported spec, a clone, a posted draft)
and plugins' managed campaigns (app/control/managed.py) both check a
campaign here, so a rule added once holds for every way a campaign is made.
Every check is static: it reads the body and the database, never LLMBench or
a machine.
"""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.control.orchestrator import schedule as sched
from app.control.search.space import space_errors
from app.datasets import pinning
from app.db.models import Machine, MachineGroup, MachineGroupMember, Policy
from app.schemas.core import CampaignCreate
from app.schemas.policy import PolicySettings

ALLOWED_ENGINES = frozenset({"sglang", "vllm"})


def campaign_errors(session: Session, body: CampaignCreate) -> list[str]:
    """Every reason this campaign cannot be saved, in the order a person
    would fix them; empty when it can."""
    if body.engine not in ALLOWED_ENGINES:
        return ["engine must be sglang|vllm"]
    errors: list[str] = []
    # With no policy the platform simply enumerates the declared space; with
    # one, the container decides what to try and the space is only the bounds
    # it must stay inside.
    if body.policy_id is not None:
        if session.get(Policy, body.policy_id) is None:
            errors.append(f"no policy with id {body.policy_id}")
        else:
            try:
                PolicySettings.model_validate(body.policy_settings or {})
            except ValidationError as exc:
                errors.append(f"policy_settings: {exc.errors()}")
    if body.confirm_top_k < 0 or body.confirm_repeats < 1:
        errors.append("confirm_top_k must be >= 0 and confirm_repeats >= 1")
    errors += staging_errors(body)
    errors += node_group_errors(session, body)
    # A malformed space does not fail loudly — it quietly expands to a different
    # candidate list than the one that was reviewed. Catch it at creation, not
    # at 2am when the search has already spent half the night on it.
    space = space_errors(body.search_space or {})
    if space:
        errors.append("; ".join(space))
    # A schedule that does not parse leaves the campaign asleep forever, and
    # the only symptom is nothing happening at 23:00 — the worst possible time
    # to discover a typo.
    schedule = sched.errors(body.daily_start, body.daily_end, body.schedule_timezone)
    if schedule:
        errors.append("; ".join(schedule))
    return errors


def staging_errors(body: CampaignCreate) -> list[str]:
    """Half a staged campaign is worse than none.

    A top-k with no benchmark would re-run the winners against the same cheap
    test and present the result as verification; a benchmark with no top-k is
    configuration that silently never fires. Both read, at 8am, as "the second
    stage did not happen" with nothing to say why.
    """
    errors: list[str] = []
    if body.verify_top_k < 0:
        errors.append("verify_top_k must be >= 0")
    if body.verify_top_k > 0 and not body.verify_benchmark_slug.strip():
        errors.append(
            "verify_top_k needs verify_benchmark_slug — which benchmark should "
            "the best candidates be re-measured against?"
        )
    if body.verify_benchmark_slug.strip() and body.verify_top_k <= 0:
        errors.append(
            "verify_benchmark_slug needs verify_top_k > 0, or nothing is ever "
            "sent to it"
        )
    if body.verify_top_k > 0 and body.verify_max_run_minutes < 1:
        errors.append("verify_max_run_minutes must be >= 1")
    if body.dataset_policy and body.dataset_policy not in pinning.POLICIES:
        errors.append(f"dataset_policy must be one of {list(pinning.POLICIES)}")
    # No longer tied to the verify stage: when the single benchmark IS the
    # replay, the primary stage pins the dataset. A profile set on a campaign
    # whose benchmark never replays is harmless — the pin is computed and simply
    # never matched against — so it is not worth refusing here.
    return errors


def node_group_errors(session: Session, body: CampaignCreate) -> list[str]:
    """Validate a declared node group at save time.

    Cheap and static: the group must exist, and vLLM is refused outright — its
    multi-node topology (a Ray head plus workers, or external_launcher) is a
    different bootstrap that the platform does not have, and half-wiring it would
    fail at launch with a config that looks fine.
    """
    name = (body.node_group or "").strip()
    if not name:
        return []
    if body.engine != "sglang":
        return [
            f"{body.engine} multi-node deployment is not supported yet; use sglang "
            "for a node group"
        ]
    group = session.scalars(select(MachineGroup).where(MachineGroup.name == name)).first()
    if group is None:
        return [f"no node group named {name!r} — create one on the Resources page"]
    # A gang on the cluster is one LeaderWorkerSet. Until the k8s driver
    # renders that, launching N separate Deployments would be N single-node
    # engines rather than the deployment that was asked for, so the pin is
    # refused here instead of silently mislaunching it.
    drivers = session.scalars(
        select(Machine.driver)
        .join(MachineGroupMember, MachineGroupMember.machine_id == Machine.id)
        .where(MachineGroupMember.group_id == group.id)
    ).all()
    non_ssh = sorted({(driver or "ssh_docker") for driver in drivers} - {"ssh_docker"})
    if non_ssh:
        return [
            f"node group {name!r} runs on {', '.join(non_ssh)}, and multi-node "
            "deployment is only implemented for ssh+docker hosts so far"
        ]
    return []
