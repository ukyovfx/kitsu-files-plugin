#!/usr/bin/env python3
"""Read-only CPU, memory, cgroup and disk inventory for one Compose UAT unit.

The report contains no environment variables, container command lines, or logs.
Docker statistics are instantaneous samples; callers should measure separate
named phases and should not interpret a short sample as a workload peak.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


SIZE = re.compile(r"^\s*([0-9.]+)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*$", re.I)
UNIT_BYTES = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.PIPE).strip()


def parse_size(value: str) -> int:
    match = SIZE.match(value)
    if not match:
        raise ValueError(f"Unsupported Docker size value: {value!r}")
    return round(float(match.group(1)) * UNIT_BYTES[match.group(2).lower()])


def parse_memory_usage(value: str) -> tuple[int, int]:
    used, separator, limit = value.partition(" /")
    if not separator:
        raise ValueError("Docker stats memory usage has no limit separator")
    return parse_size(used), parse_size(limit)


def inspect_project(project: str) -> dict[str, dict]:
    ids = docker("ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}").splitlines()
    if not ids:
        raise RuntimeError("No containers found for the selected UAT Compose project")
    result = {}
    for container_id in ids:
        row = json.loads(docker("inspect", "-s", container_id))[0]
        service = row.get("Config", {}).get("Labels", {}).get("com.docker.compose.service")
        if not service or service in result:
            raise RuntimeError("UAT project has a missing or duplicate Compose service label")
        host = row["HostConfig"]
        result[service] = {
            "container_id": row["Id"],
            "image_id": row["Image"],
            "state": row["State"]["Status"],
            "memory_limit_bytes": host.get("Memory", 0),
            "nano_cpus": host.get("NanoCpus", 0),
            "pids_limit": host.get("PidsLimit", 0),
            "writable_layer_bytes": row.get("SizeRw"),
            "log_driver": host.get("LogConfig", {}).get("Type"),
            "volume_names": sorted(
                mount["Name"] for mount in row.get("Mounts", []) if mount.get("Type") == "volume"
            ),
        }
    return result


def sample_stats(services: dict[str, dict]) -> dict[str, dict]:
    ids = [item["container_id"] for item in services.values() if item["state"] == "running"]
    if not ids:
        return {}
    rows = docker("stats", "--no-stream", "--format", "{{json .}}", *ids).splitlines()
    samples = {}
    by_id = {
        key: name
        for name, item in services.items()
        for key in (item["container_id"], item["container_id"][:12])
    }
    for line in rows:
        row = json.loads(line)
        name = by_id.get(row.get("ID"))
        if not name:
            continue
        used, _limit = parse_memory_usage(row["MemUsage"])
        cpu = float(row["CPUPerc"].rstrip("%"))
        samples[name] = {
            "cpu_percent_of_one_host_cpu": cpu,
            "memory_usage_bytes": used,
            "pids": int(row.get("PIDs", "0")),
            "block_io": row.get("BlockIO", ""),
            "net_io": row.get("NetIO", ""),
        }
    return samples


def summarize_samples(samples: list[dict], services: dict[str, dict]) -> dict[str, dict]:
    summary = {}
    for name in services:
        values = [row["services"][name] for row in samples if name in row["services"]]
        if not values:
            summary[name] = {"samples": 0, "state": services[name]["state"]}
            continue
        summary[name] = {
            "samples": len(values),
            "cpu_percent_max": max(item["cpu_percent_of_one_host_cpu"] for item in values),
            "cpu_percent_mean": round(sum(item["cpu_percent_of_one_host_cpu"] for item in values) / len(values), 3),
            "memory_bytes_max": max(item["memory_usage_bytes"] for item in values),
            "memory_bytes_median": sorted(item["memory_usage_bytes"] for item in values)[len(values) // 2],
            "pids_max": max(item["pids"] for item in values),
        }
    return summary


def collect_log_bytes(services: dict[str, dict]) -> dict[str, int | None]:
    # Count bytes in a pipe and discard them. Never persist or print log content.
    result = {}
    for name, service in services.items():
        try:
            process = subprocess.Popen(
                ["docker", "logs", service["container_id"]],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            count = 0
            assert process.stdout is not None
            for block in iter(lambda: process.stdout.read(65536), b""):
                count += len(block)
            code = process.wait(timeout=30)
            result[name] = count if code == 0 else None
        except (OSError, subprocess.SubprocessError):
            result[name] = None
    return result


def parse_volume_sizes(output: str, project: str) -> dict[str, int]:
    lines = output.splitlines()
    in_volumes = False
    found_header = False
    result = {}
    for line in lines:
        if line.startswith("Local Volumes space usage:"):
            in_volumes = True
            continue
        if in_volumes and line.endswith("space usage:"):
            break
        if not in_volumes or not line.strip():
            continue
        fields = line.split()
        if not found_header:
            found_header = fields[:3] == ["VOLUME", "NAME", "LINKS"] or fields[:2] == ["VOLUME", "NAME"]
            continue
        if len(fields) < 3 or not fields[0].startswith(project + "_"):
            continue
        # Docker CLI renders the table as name, link count, human size.
        try:
            result[fields[0]] = parse_size(" ".join(fields[2:]))
        except ValueError:
            continue
    return result


def collect_disk(services: dict[str, dict], project: str) -> dict:
    output = docker("system", "df", "--verbose")
    volumes = parse_volume_sizes(output, project)
    image_ids = sorted({item["image_id"] for item in services.values()})
    images = []
    for image_id in image_ids:
        row = json.loads(docker("image", "inspect", image_id))[0]
        images.append({"image_id": image_id, "virtual_size_bytes": row.get("Size")})
    return {
        "project_volume_bytes": volumes,
        "project_volume_total_bytes": sum(volumes.values()),
        "service_image_virtual_sizes": images,
        "image_virtual_size_is_not_unique_physical_usage": True,
        "service_writable_layer_bytes": {
            name: item["writable_layer_bytes"] for name, item in services.items()
        },
        "service_log_bytes": collect_log_bytes(services),
        "docker_system_df_summary": docker("system", "df"),
        "limitations": [
            "Docker image virtual sizes may share physical layers and must not be summed as unique disk usage.",
            "Docker system df is daemon-wide; only project-labeled named-volume rows are attributed to this UAT.",
            "Build cache and host backup paths are outside the project volume inventory.",
        ],
    }


def measure(project: str, phase: str, duration: float, interval: float) -> dict:
    services = inspect_project(project)
    samples = []
    deadline = time.monotonic() + duration
    while True:
        sample = sample_stats(services)
        if sample:
            samples.append({"timestamp_utc": datetime.now(timezone.utc).isoformat(), "services": sample})
        if time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    if not samples:
        raise RuntimeError("No running service samples were collected")

    return {
        "schema_version": 1,
        "phase": phase,
        "started_at_utc": samples[0]["timestamp_utc"],
        "duration_seconds": duration,
        "sample_interval_seconds": interval,
        "sample_count": len(samples),
        "project": project,
        "cgroup_limits_by_service": services,
        "usage_summary_by_service": summarize_samples(samples, services),
        "samples": samples,
        "disk": collect_disk(services, project),
        "interpretation": "sampled usage is not an unsampled instantaneous peak; report each named phase separately",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Exact Docker Compose project name")
    parser.add_argument("--phase", required=True, help="Safe phase label, e.g. idle or restore")
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, help="Optional JSON report path; output contains resource data only")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.phase):
        parser.error("phase label must contain only letters, numbers, dot, underscore or hyphen")
    if not (1 <= args.duration_seconds <= 3600) or not (0.5 <= args.interval_seconds <= 60):
        parser.error("duration must be 1..3600 seconds and interval 0.5..60 seconds")
    report = measure(args.project, args.phase, args.duration_seconds, args.interval_seconds)
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
