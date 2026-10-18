"""A cold image over ssh: pulled in the background, the launch waits for it.

`docker run` on an image the machine does not have pulls it inside the launch
command's 180 s, and a multi-GB image does not make it: the run failed with an
ssh timeout on a machine that was fine. Now the pull runs detached on the
machine and the launch is deferred until the image is there."""

import pytest

from app.control.launch.base import LaunchDeferred, MachineInfo
from app.control.launch.ssh_docker import SshDockerDriver


class _Result:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _driver(answer: str):
    driver = SshDockerDriver()
    calls: list[str] = []

    def ssh(machine, remote_cmd, timeout=60):
        calls.append(remote_cmd)
        return _Result(answer if "docker image inspect" in remote_cmd else "")

    driver._ssh = ssh
    return driver, calls


MACHINE = MachineInfo(name="nv-1", host="10.0.0.1")
IMAGE = "registry.example/sglang:v0.5.19"


def test_an_image_on_the_machine_launches_at_once():
    driver, _ = _driver("present")
    driver.ensure_image(MACHINE, IMAGE)


def test_a_missing_image_starts_one_background_pull_and_defers():
    driver, calls = _driver("started")
    with pytest.raises(LaunchDeferred, match="pulling .*sglang:v0.5.19 onto 10.0.0.1"):
        driver.ensure_image(MACHINE, IMAGE)
    assert "nohup docker pull" in calls[0]


def test_a_pull_in_progress_keeps_deferring_until_its_budget(monkeypatch):
    driver, _ = _driver("pulling 600")
    with pytest.raises(LaunchDeferred, match="10 min so far"):
        driver.ensure_image(MACHINE, IMAGE)
    driver, calls = _driver("pulling 99999")
    with pytest.raises(RuntimeError, match="did not finish pulling") as exc:
        driver.ensure_image(MACHINE, IMAGE)
    assert not isinstance(exc.value, LaunchDeferred)
    assert any(c.startswith("kill ") for c in calls)


def test_a_failed_pull_fails_the_launch_with_its_output():
    driver, _ = _driver("failed\nError response from daemon: manifest unknown")
    with pytest.raises(RuntimeError, match="manifest unknown") as exc:
        driver.ensure_image(MACHINE, IMAGE)
    assert not isinstance(exc.value, LaunchDeferred)


def test_a_deferred_launch_keeps_the_run_pending_and_says_why():
    from sqlalchemy import select

    from app.db.models import Event, Run, RunStatus
    from tests.test_supervisor import make_supervisor

    supervisor, factory = make_supervisor()
    real_launch = supervisor.driver.launch
    pulls = {"left": 2}

    def launch(spec):
        if pulls["left"]:
            pulls["left"] -= 1
            raise LaunchDeferred(f"pulling img onto {spec.machine.host}")
        return real_launch(spec)

    supervisor.driver.launch = launch
    supervisor.tick()
    supervisor.tick()
    with factory() as s:
        run = s.scalars(select(Run)).one()
        assert run.status == RunStatus.PENDING.value and run.waiting_since is not None
        reasons = [e.payload.get("reason") for e in s.scalars(select(Event)).all()
                   if e.kind == "run_waiting_for_cluster"]
        assert reasons == ["pulling img onto 10.0.0.1"]
    supervisor.tick()
    with factory() as s:
        assert s.scalars(select(Run)).one().status == RunStatus.LAUNCHING.value
