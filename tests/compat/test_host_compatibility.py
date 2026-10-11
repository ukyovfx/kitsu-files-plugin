import json
from pathlib import Path


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
