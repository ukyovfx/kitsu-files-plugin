from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UAT = ROOT / "deploy" / "uat"


def test_each_runtime_service_can_join_the_operator_managed_aggregate_cgroup():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    assert compose.count("cgroup_parent: ${VFX_FILES_UAT_CGROUP_PARENT:-}") == 5
    assert "VFX_FILES_UAT_CGROUP_PARENT=" in (UAT / ".env.example").read_text(encoding="utf-8")


def test_linux_handoff_preserves_fail_closed_server_and_issue_5_gates():
    guide = (UAT / "linux" / "README.md").read_text(encoding="utf-8")
    assert "official Kitsu/Zou host security gate remains **FAIL**" in guide
    assert "quota-enforced filesystem" in guide
    assert "BLOCKED" in guide
    assert "Do not implement a task drawer in this UAT package" in guide
    assert "Production → Assets → VFX Files" in guide
