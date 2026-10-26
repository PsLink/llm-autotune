"""Port-pool allocation uses the API server as the cross-worker arbiter."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, RLock

import pytest

from app.control.launch.base import LaunchDeferred
from app.control.launch.k8s_api import K8sError, NodePortAllocated
from app.core.nodeports import parse_nodeport_pool
from app.schemas.clusters import ClusterIn
from tests.test_k8s_driver import FakeK8sApi, _clone, _driver, _spec


@pytest.mark.parametrize(("value", "expected"), [
    ("", []), ("  ", []), ("31080", [31080]),
    ("31080-31082,31100,31081", [31080, 31081, 31082, 31100]),
    (" 31080 - 31081 , 31100 ", [31080, 31081, 31100]),
])
def test_parse_pool(value, expected):
    assert parse_nodeport_pool(value) == expected


@pytest.mark.parametrize("value", [
    "0", "65536", "-1", "31081-31080", "1-65535", "abc", "31080,",
    "31080,,31081", "31080.0", "31080-31081-31082",
])
def test_reject_malformed_or_oversized_pool(value):
    with pytest.raises(ValueError):
        parse_nodeport_pool(value)


def test_api_accepts_integer_lists_and_rejects_noninteger_entries():
    assert ClusterIn(name="gpu", service_nodeport_pool=[31080, 31081]).service_nodeport_pool == (
        "31080,31081"
    )
    for value in [[True], [31080.5], ["31080"], [0], None]:
        with pytest.raises(ValueError):
            ClusterIn(name="gpu", service_nodeport_pool=value)


class AllocatingApi(FakeK8sApi):
    """An atomic allocator shared by drivers; no real cluster calls."""

    def __init__(self, settings):
        super().__init__(settings)
        self.lock = RLock()
        self.busy = {}
        self.attempts = []
        self.conflicts = 0
        self.barrier = None
        self.fail_deployment = False
        self.fail_service = False

    def apply(self, manifest):
        items = manifest if isinstance(manifest, list) else [manifest]
        for item in items:
            with self.lock:
                name = item["metadata"]["name"]
                if item["kind"] == "Service":
                    if self.fail_service:
                        raise K8sError("Forbidden")
                    port = item["spec"]["ports"][0]["nodePort"]
                    self.attempts.append(port)
                    if port in self.busy and self.busy[port] != name:
                        self.conflicts += 1
                        raise NodePortAllocated("provided port is already allocated")
                    self.busy[port] = name
                elif item["kind"] == "Deployment" and self.fail_deployment:
                    raise K8sError("deployment quota exceeded")
                super().apply(deepcopy(item))

    def list(self, resource, label_selector=""):
        if resource != "services":
            return super().list(resource, label_selector)
        with self.lock:
            snapshot = [deepcopy(v) for (kind, _), v in self.objects.items() if kind == "service"]
        if self.barrier:
            self.barrier.wait(timeout=5)
        return snapshot

    def delete(self, resource, name):
        with self.lock:
            if resource == "service":
                self.busy = {p: owner for p, owner in self.busy.items() if owner != name}
            super().delete(resource, name)


def _pool_settings(pool="31080-31081,31100"):
    return _clone(
        k8s_workload_kind="deployment", k8s_node_host="198.51.100.10",
        k8s_service_nodeport=32000, k8s_service_nodeport_pool=pool,
    )


def test_pool_overrides_fixed_port_and_retries_cluster_wide_conflicts():
    settings = _pool_settings()
    api = AllocatingApi(settings)
    # A Service outside our namespace is invisible to list(), but still owns
    # this port cluster-wide. The server rejects it and the driver tries next.
    api.busy[31080] = "other-namespace-service"
    handle, command = _driver(settings, api).launch(_spec())
    assert api.attempts == [31080, 31081]
    assert handle.endpoint_url == "http://198.51.100.10:31081"
    assert [item["kind"] for item in api.applied] == ["Service", "Deployment"]
    assert '"nodePort": 31081' in command


def test_exhaustion_waits_without_starting_engine_and_retries_after_release():
    settings = _pool_settings("31080")
    api = AllocatingApi(settings)
    driver = _driver(settings, api)
    first, _ = driver.launch(_spec())
    second = _spec(run_id=8)
    with pytest.raises(LaunchDeferred, match="waiting for a free NodePort"):
        driver.launch(second)
    assert ("deployment", second.container_name) not in api.objects
    assert ("service", second.container_name) not in api.objects
    driver.teardown(first)
    handle, _ = driver.launch(second)
    assert handle.endpoint_url == "http://198.51.100.10:31080"


def test_two_workers_racing_for_a_port_get_distinct_endpoints():
    settings = _pool_settings("31080-31081")
    api = AllocatingApi(settings)
    api.barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(
            lambda run_id: _driver(settings, api).launch(_spec(run_id=run_id)), [7, 8],
        ))
    assert {handle.endpoint_url for handle, _ in results} == {
        "http://198.51.100.10:31080", "http://198.51.100.10:31081",
    }
    assert api.conflicts == 1


@pytest.mark.parametrize("new_pool", ["31100", ""])
def test_restart_and_pool_edit_preserve_existing_service_port(new_pool):
    settings = _pool_settings("31080")
    api = AllocatingApi(settings)
    _driver(settings, api).launch(_spec())
    restarted = _driver(_pool_settings(new_pool), api)
    assert restarted.attach(_spec()).endpoint_url == "http://198.51.100.10:31080"
    handle, _ = restarted.launch(_spec())
    assert handle.endpoint_url == "http://198.51.100.10:31080"
    assert set(api.busy) == {31080}


def test_service_only_reservation_can_resume_or_be_cancelled():
    settings = _pool_settings("31080")
    api = AllocatingApi(settings)
    driver = _driver(settings, api)
    driver.launch(_spec())
    api.delete("deployment", _spec().container_name)  # crash before engine creation
    handle = _driver(settings, api).attach(_spec())
    assert handle is not None
    driver.request_stop(handle)
    assert not api.busy
    assert driver.attach(_spec()) is None


def test_deployment_creation_failure_releases_reserved_port():
    settings = _pool_settings()
    api = AllocatingApi(settings)
    api.fail_deployment = True
    with pytest.raises(K8sError, match="quota exceeded"):
        _driver(settings, api).launch(_spec())
    assert not api.busy
    assert not api.objects


def test_permission_errors_are_not_treated_as_pool_exhaustion():
    settings = _pool_settings()
    api = AllocatingApi(settings)
    api.fail_service = True
    with pytest.raises(K8sError, match="Forbidden") as exc:
        _driver(settings, api).launch(_spec())
    assert not isinstance(exc.value, LaunchDeferred)
    assert not api.objects


def test_supervisor_cleans_reservation_when_deployment_disappears():
    from sqlalchemy import select

    from app.db.models import Run, RunStatus
    from tests.test_supervisor import make_supervisor

    supervisor, factory = make_supervisor()
    supervisor.tick()
    settings = _pool_settings("31080")
    api = AllocatingApi(settings)
    driver = _driver(settings, api)
    supervisor.driver = driver
    with factory() as session:
        run = session.scalars(select(Run)).one()
        spec = supervisor._spec_for(session, run)
        driver.launch(spec)
        api.delete("deployment", spec.container_name)
        supervisor._advance_one(session, run)
        assert run.status == RunStatus.FAILED.value
        assert run.failure_class == "gone"
        assert not api.busy
        assert not api.objects
