import json
import sys
import types
from pathlib import Path

import pytest

from plugin.compat import assert_runtime_host

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_host_pair_loads_plugin_context():
    context = json.loads(
        (ROOT / "tests/compat/fixtures/kitsu_plugin_context.json").read_text()
    )
    lock = json.loads((ROOT / "environment/host-lock.yaml").read_text())

    assert lock["official_test_image"] == "cgwire/cgwire:1.0.70-1.0.94-01"
    assert context["production_id"] in context["url"]
    assert context["episode_id"] in context["url"]
    assert context["authorization_source"] == "server"


def test_lock_contains_immutable_refs():
    lock = json.loads((ROOT / "environment/host-lock.yaml").read_text())

    for key in ("kitsu_commit", "zou_commit"):
        value = lock[key]
        assert len(value) == 40
        assert all(character in "0123456789abcdef" for character in value)


def test_lock_records_only_rehearsed_zou_compatibility_pairs():
    lock = json.loads((ROOT / "environment/host-lock.yaml").read_text())
    pairs = {(item["zou_version"], item["zou_commit"]) for item in lock["verified_compatibility"]}

    assert pairs == {
        ("1.0.94", "f5fc34c367a39d58d7741ab2790a5f349c312175"),
        ("1.0.96", "720ef2f1f87a53bd373282f2795757256249c169"),
    }
    assert lock["verified_compatibility"][1]["host_image_digest"].startswith("sha256:")


@pytest.mark.parametrize("version", ["1.0.94", "1.0.96"])
def test_runtime_guard_accepts_only_verified_zou_versions(monkeypatch, version):
    monkeypatch.setitem(sys.modules, "zou", types.SimpleNamespace(__version__=version))
    assert_runtime_host()


def test_runtime_guard_rejects_unverified_zou_version(monkeypatch):
    monkeypatch.setitem(sys.modules, "zou", types.SimpleNamespace(__version__="1.0.95"))
    with pytest.raises(RuntimeError):
        assert_runtime_host()
