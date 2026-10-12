"""Runtime Docker isolation acceptance; opt in with one synthetic UAT unit."""

import json
import os
import subprocess
from pathlib import Path
import urllib.request
from urllib.error import URLError

import pytest


PROJECT = os.environ.get("VFX_FILES_UAT_RUNTIME_PROJECT")
ENV_FILE = os.environ.get("VFX_FILES_UAT_ENV_FILE")
pytestmark = pytest.mark.skipif(not (PROJECT and ENV_FILE), reason="requires an explicitly selected disposable UAT unit")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def project_containers():
    ids = docker("ps", "--quiet", "--filter", f"label=com.docker.compose.project={PROJECT}").splitlines()
    result = {}
    for container_id in ids:
        row = json.loads(docker("inspect", container_id))[0]
        service = row["Config"]["Labels"].get("com.docker.compose.service")
        result[service] = row
    return result


def test_runtime_networks_loopback_egress_and_resource_fences():
    containers = project_containers()
    required = {"postgres", "redis", "egress-guard", "api", "worker"}
    assert required <= containers.keys()
    guard = containers["egress-guard"]
    api = containers["api"]
    worker = containers["worker"]

    backend = f"{PROJECT}_uat"
    ingress = f"{PROJECT}_ingress"
    backend_info = json.loads(docker("network", "inspect", backend))[0]
    ingress_info = json.loads(docker("network", "inspect", ingress))[0]
    assert backend_info["Internal"] is True
    assert ingress_info["Options"].get("com.docker.network.bridge.enable_ip_masquerade") == "false"
    assert ingress_info["Options"].get("com.docker.network.enable_ipv6") == "false"
    assert set(guard["NetworkSettings"]["Networks"]) == {backend, ingress}
    assert set(containers["postgres"]["NetworkSettings"]["Networks"]) == {backend}
    assert set(containers["redis"]["NetworkSettings"]["Networks"]) == {backend}
    assert set(worker["NetworkSettings"]["Networks"]) == {backend}
    assert api["HostConfig"]["NetworkMode"] == f"container:{guard['Id']}"
    assert api["Image"] == worker["Image"]
    assert "--poll" in worker["Config"]["Cmd"]
    env_map = {}
    for line in Path(ENV_FILE).read_text(encoding="utf-8").splitlines():
        key, separator, value = line.rstrip("\r\n").partition("=")
        if separator:
            env_map[key] = value
    image = json.loads(docker("image", "inspect", api["Image"]))[0]
    assert image["Config"]["Labels"]["org.opencontainers.image.revision"] == env_map["VFX_FILES_UAT_SOURCE_HEAD"]
    assert image["Config"]["Labels"]["org.vfx-files.uat-only"] == "true"
    assert set(api["HostConfig"]["CapAdd"]) == {
        "CAP_NET_BIND_SERVICE", "CAP_CHOWN", "CAP_SETUID", "CAP_SETGID"
    }
    assert "CAP_NET_ADMIN" not in api["HostConfig"]["CapAdd"]
    assert set(worker["HostConfig"]["CapDrop"]) == {"ALL"}

    bindings = guard["HostConfig"]["PortBindings"]
    assert set(bindings) == {"80/tcp"}
    assert bindings["80/tcp"] and all(item["HostIp"] == "127.0.0.1" for item in bindings["80/tcp"])
    assert not containers["postgres"]["HostConfig"]["PortBindings"]
    assert not containers["redis"]["HostConfig"]["PortBindings"]

    for service, container in containers.items():
        limits = container["HostConfig"]
        assert limits["Memory"] > 0
        assert limits["NanoCpus"] > 0
        assert limits["PidsLimit"] > 0
        for mount in container["Mounts"]:
            if mount["Type"] == "volume":
                assert mount["Name"].startswith(f"{PROJECT}_")
            else:
                assert service in {"api", "egress-guard"}
                assert mount["Type"] == "tmpfs"

    service_port = bindings["80/tcp"][0]["HostPort"]
    with urllib.request.urlopen(f"http://127.0.0.1:{service_port}/", timeout=5) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "text/html"

    probe = docker(
        "exec", api["Id"], "/opt/zou/env/bin/python", "-c",
        "import socket; s=socket.socket(); s.settimeout(2); print(s.connect_ex(('1.1.1.1',443))==0); s.close()",
    )
    assert probe == "False", "API namespace reached a public TCP destination despite egress-deny policy"


def test_local_host_only_ui_route_does_not_accept_non_loopback_bindings():
    containers = project_containers()
    guard = containers["egress-guard"]
    bindings = guard["HostConfig"]["PortBindings"]["80/tcp"]

    assert bindings
    assert {entry["HostIp"] for entry in bindings} == {"127.0.0.1"}
