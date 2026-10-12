from contextlib import nullcontext
import signal
import sys
from types import ModuleType

import pytest

from plugin import worker_runtime


class App:
    def app_context(self):
        return nullcontext()


def install_fake_zou(monkeypatch):
    zou_module = ModuleType("zou")
    zou_app_module = ModuleType("zou.app")
    zou_app_module.create_app = App
    zou_module.app = zou_app_module
    monkeypatch.setitem(sys.modules, "zou", zou_module)
    monkeypatch.setitem(sys.modules, "zou.app", zou_app_module)


def test_poll_mode_runs_existing_forever_loop(monkeypatch):
    install_fake_zou(monkeypatch)
    monkeypatch.setattr(worker_runtime, "assert_restore_validation_complete", lambda: None)
    monkeypatch.setattr(worker_runtime, "build_runtime", lambda _app: "runtime")
    captured = {}

    def run_forever(runtime, *, stop_event, idle_seconds):
        captured.update(runtime=runtime, stop_event=stop_event, idle_seconds=idle_seconds)
        stop_event.set()

    monkeypatch.setattr("plugin.worker.main.run_forever", run_forever)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)

    assert worker_runtime.main(["--poll", "--idle-seconds", "1.5"]) == 0
    assert captured["runtime"] == "runtime"
    assert captured["idle_seconds"] == 1.5
    assert captured["stop_event"].is_set()


def test_once_mode_remains_supported(monkeypatch):
    install_fake_zou(monkeypatch)
    monkeypatch.setattr(worker_runtime, "assert_restore_validation_complete", lambda: None)
    monkeypatch.setattr(worker_runtime, "build_runtime", lambda _app: "runtime")
    monkeypatch.setattr("plugin.worker.main.process_one", lambda runtime: "completed" if runtime == "runtime" else None)

    assert worker_runtime.main(["--once"]) == 0


@pytest.mark.parametrize("args", [[], ["--once", "--poll"]])
def test_worker_cli_rejects_missing_or_ambiguous_execution_mode(args):
    with pytest.raises(SystemExit):
        worker_runtime.main(args)


def test_worker_cli_rejects_poll_interval_outside_bounds(monkeypatch):
    install_fake_zou(monkeypatch)
    monkeypatch.setattr(worker_runtime, "assert_restore_validation_complete", lambda: None)
    monkeypatch.setattr(worker_runtime, "build_runtime", lambda _app: "runtime")

    with pytest.raises(SystemExit):
        worker_runtime.main(["--poll", "--idle-seconds", "0.1"])
