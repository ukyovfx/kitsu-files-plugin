from deploy.uat.tools.measure_resources import (
    parse_memory_usage,
    parse_size,
    parse_volume_sizes,
    summarize_samples,
)


def test_parse_docker_sizes_and_memory_usage():
    assert parse_size("1.5GiB") == int(1.5 * 1024**3)
    assert parse_size("12.0 MB") == 12_000_000
    assert parse_memory_usage("96.25MiB / 4GiB") == (round(96.25 * 1024**2), 4 * 1024**3)


def test_volume_parser_only_attributes_selected_project():
    output = """Local Volumes space usage:
VOLUME NAME                          LINKS     SIZE
vfx-files-uat-unit_postgres_data     1         12.5MiB
vfx-files-uat-unit_fake_storage      1         128B
other_project_data                   1         9GiB
"""
    assert parse_volume_sizes(output, "vfx-files-uat-unit") == {
        "vfx-files-uat-unit_postgres_data": int(12.5 * 1024**2),
        "vfx-files-uat-unit_fake_storage": 128,
    }


def test_usage_summary_keeps_sampled_maximum_separate_from_median():
    services = {"api": {"state": "running"}}
    samples = [
        {"services": {"api": {"cpu_percent_of_one_host_cpu": 12.0, "memory_usage_bytes": 100, "pids": 2}}},
        {"services": {"api": {"cpu_percent_of_one_host_cpu": 88.0, "memory_usage_bytes": 500, "pids": 8}}},
        {"services": {"api": {"cpu_percent_of_one_host_cpu": 20.0, "memory_usage_bytes": 200, "pids": 3}}},
    ]
    result = summarize_samples(samples, services)["api"]
    assert result["cpu_percent_max"] == 88.0
    assert result["memory_bytes_max"] == 500
    assert result["memory_bytes_median"] == 200
