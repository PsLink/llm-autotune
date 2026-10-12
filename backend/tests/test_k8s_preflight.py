"""A cluster machine's campaign preflight: probe Jobs on the machine's node
pull every image the campaign runs and mount the model as a run would, and
the pods' own state says what a run would have hit."""

from app.control.launch.base import MachineInfo
from app.control.launch.k8s import PREFLIGHT_LABEL, K8sDriver, render_preflight_job
from app.control.launch.k8s_api import K8sApi
from app.core.config import get_settings

ENGINE = "registry.example.com/sglang:v1"
POLICY = "registry.example.com/policy-random-search:0.1.0"
MODEL = "/mnt/models/qwen"


def _node() -> dict:
    return {
        "metadata": {"name": "gpu-node-1", "labels": {}},
        "spec": {},
        "status": {
            "allocatable": {"nvidia.com/gpu": "8"},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _terminated(name: str) -> dict:
    return {"name": name, "state": {"terminated": {"exitCode": 0, "reason": "Completed"}}}


def _waiting(name: str, reason: str, message: str = "") -> dict:
    return {"name": name, "state": {"waiting": {"reason": reason, "message": message}}}


class ProbeApi(K8sApi):
    """A cluster whose image probe pod shows `statuses` and `events`, and whose
    model probe pod shows `model_statuses` and `model_events` (by default, a
    mount that worked)."""

    def __init__(self, statuses=None, events=None, log="", model_statuses=None,
                 model_events=None):
        self.statuses = statuses or []
        self.events = events or []
        self.model_statuses = model_statuses or [_terminated("engine")]
        self.model_events = model_events if model_events is not None else [
            {"reason": "Pulled", "type": "Normal"}]
        self.log = log
        self.applied: list[dict] = []
        self.deleted: list[tuple[str, str]] = []
        self.log_selectors: list[str] = []

    def apply(self, manifest):
        self.applied.append(manifest)

    def get(self, resource, name):
        return None

    def delete(self, resource, name):
        self.deleted.append((resource, name))

    def list(self, resource, label_selector=""):
        if resource == "nodes":
            return [_node()]
        if resource == "pods":
            assert label_selector.startswith(PREFLIGHT_LABEL)
            model = label_selector.endswith("-model")
            return [{
                "metadata": {"name": "model-pod" if model else "probe-pod"},
                "spec": {"nodeName": "gpu-node-1"},
                "status": {"containerStatuses":
                           self.model_statuses if model else self.statuses},
            }]
        if resource == "events":
            return ([{"involvedObject": {"kind": "Pod", "name": "probe-pod"}, **e}
                     for e in self.events]
                    + [{"involvedObject": {"kind": "Pod", "name": "model-pod"}, **e}
                       for e in self.model_events])
        return []

    def logs(self, label_selector, tail=200):
        self.log_selectors.append(label_selector)
        return self.log


def _settings(**over):
    return get_settings().model_copy(update={
        "k8s_api_mode": "client", "k8s_namespace": "runs", **over})


def _machine() -> MachineInfo:
    return MachineInfo(name="gpu-node-1", host="k8s", gpu_count=8, driver="k8s",
                       node_selector="kubernetes.io/hostname=gpu-node-1")


def _run(api: ProbeApi, timeout: float = 5, **settings) -> dict:
    driver = K8sDriver(api=api, settings=_settings(**settings))
    checks = driver.preflight(_machine(), image=ENGINE, model_path=MODEL,
                              policy_images=[POLICY], widest_candidate_cards=2,
                              timeout=timeout)
    return {c.key: c for c in checks}


def test_everything_present_passes_and_cleans_up():
    api = ProbeApi([_terminated("engine"), _terminated("policy-0")],
                   [{"reason": "Pulled", "type": "Normal"}])
    checks = _run(api)
    assert checks["node"].status == "pass"
    assert checks["gpus"].status == "pass"
    assert checks["image"].status == "pass"
    assert checks[f"policy_image:{POLICY}"].status == "pass"
    assert checks["model_path"].status == "pass"
    assert len(api.applied) == 2
    assert [kind for kind, _ in api.deleted] == ["job", "job"]


def test_an_image_the_node_cannot_pull_fails():
    api = ProbeApi([_waiting("engine", "ErrImagePull", "manifest unknown"),
                    _terminated("policy-0")])
    checks = _run(api)
    assert checks["image"].status == "fail"
    assert "manifest unknown" in checks["image"].detail
    assert checks[f"policy_image:{POLICY}"].status == "pass"
    assert api.deleted


def test_a_missing_model_fails_the_mount():
    api = ProbeApi(
        [_terminated("engine"), _terminated("policy-0")],
        model_statuses=[_waiting("engine", "ContainerCreating")],
        model_events=[{"reason": "FailedMount", "type": "Warning",
                       "message": "hostPath type check failed: /mnt/models/qwen is not "
                                  "a directory"}],
    )
    checks = _run(api)
    assert checks["model_path"].status == "fail"
    assert "not a directory" in checks["model_path"].detail
    # The images are pulled by a pod of their own, so they are still checked.
    assert checks["image"].status == "pass"
    assert len(api.deleted) == 2


def test_a_wrong_image_and_a_wrong_path_are_both_found_in_one_round():
    api = ProbeApi(
        [_waiting("engine", "ErrImagePull", "manifest unknown"), _terminated("policy-0")],
        model_statuses=[_waiting("engine", "ContainerCreating")],
        model_events=[{"reason": "FailedMount", "type": "Warning",
                       "message": "hostPath type check failed"}],
    )
    checks = _run(api)
    assert checks["image"].status == "fail"
    assert checks["model_path"].status == "fail"


def test_only_the_model_probe_mounts_the_model():
    _run(api := ProbeApi([_terminated("engine"), _terminated("policy-0")]))
    images, model = (job["spec"]["template"]["spec"] for job in api.applied)
    assert "volumes" not in images
    assert [c["name"] for c in images["containers"]] == ["engine", "policy-0"]
    assert [c["name"] for c in model["containers"]] == ["engine"]
    assert model["volumes"][0]["hostPath"]["path"] == MODEL


def test_a_slow_pull_warns_and_is_left_to_finish():
    api = ProbeApi(
        [_waiting("engine", "ContainerCreating"), _terminated("policy-0")],
        [{"reason": "Pulling", "type": "Normal"}],
        model_statuses=[_waiting("engine", "ContainerCreating")],
        model_events=[{"reason": "Pulling", "type": "Normal"}],
    )
    checks = _run(api, timeout=0)
    assert checks["image"].status == "warn"
    # The pull started, so the mount had already worked.
    assert checks["model_path"].status == "pass"
    # The image probe is left to finish its pull; the model probe is done.
    assert [name for _, name in api.deleted] == [api.applied[1]["metadata"]["name"]]


def test_a_weights_claim_is_checked_from_inside():
    api = ProbeApi([_terminated("engine"), _terminated("policy-0")], log="model-missing\n")
    checks = _run(api, k8s_model_pvc="weights", k8s_model_pvc_root="/mnt/models")
    assert checks["model_path"].status == "fail"
    pod = api.applied[1]["spec"]["template"]["spec"]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == "weights"
    assert "/weights/qwen" in pod["containers"][0]["command"][2]
    assert api.log_selectors and api.log_selectors[0].endswith("-model")


def test_a_weights_claim_is_not_judged_when_the_engine_image_is_missing():
    api = ProbeApi([_waiting("engine", "ErrImagePull", "not found"), _terminated("policy-0")],
                   model_statuses=[_waiting("engine", "ErrImagePull", "not found")],
                   model_events=[{"reason": "Pulling", "type": "Normal"}])
    checks = _run(api, k8s_model_pvc="weights", k8s_model_pvc_root="/mnt/models")
    assert checks["image"].status == "fail"
    assert checks["model_path"].status == "skip"
    assert "could not pull" in checks["model_path"].detail


def test_the_probe_runs_where_a_run_would():
    job, reads_log = render_preflight_job(
        "autotune-preflight-x", _machine(),
        [("engine", "", "image", ENGINE), ("policy-0", "", "p", POLICY)],
        MODEL, _settings())
    pod = job["spec"]["template"]["spec"]
    assert not reads_log
    assert pod["nodeSelector"] == {"kubernetes.io/hostname": "gpu-node-1"}
    assert any(t["key"] == "nvidia.com/gpu" for t in pod["tolerations"])
    assert pod["volumes"] == [{"name": "model",
                               "hostPath": {"path": MODEL, "type": "Directory"}}]
    engine, policy = pod["containers"]
    assert engine["image"] == ENGINE and "volumeMounts" in engine
    assert policy["image"] == POLICY and "volumeMounts" not in policy
    assert {"name": "NVIDIA_VISIBLE_DEVICES", "value": "void"} in engine["env"]
    assert "nvidia.com/gpu" not in str(engine["resources"])
    assert job["spec"]["ttlSecondsAfterFinished"] > 0


def test_a_container_that_ran_counts_while_the_next_image_pulls():
    """The pod's status lags while the kubelet pulls the second image; the
    first container's events show it already ran."""
    def event(container, reason, message=""):
        return {"reason": reason, "type": "Normal", "message": message,
                "involvedObject": {"kind": "Pod", "name": "probe-pod",
                                   "fieldPath": f"spec.containers{{{container}}}"}}

    api = ProbeApi(
        [_waiting("engine", "ContainerCreating"), _waiting("policy-0", "ContainerCreating")],
        [event("engine", "Pulled", "already present on machine"), event("engine", "Started"),
         event("policy-0", "Pulling")],
    )
    checks = _run(api, timeout=0)
    assert checks["image"].status == "pass"
    assert checks[f"policy_image:{POLICY}"].status == "warn"
    assert checks["model_path"].status == "pass"


def test_a_pull_failure_event_fails_that_image():
    api = ProbeApi(
        [_waiting("engine", "ContainerCreating"), _terminated("policy-0")],
        [{"reason": "Failed", "type": "Warning",
          "message": 'Failed to pull image "x": not found',
          "involvedObject": {"kind": "Pod", "name": "probe-pod",
                             "fieldPath": "spec.containers{engine}"}}],
    )
    checks = _run(api)
    assert checks["image"].status == "fail" and "not found" in checks["image"].detail
