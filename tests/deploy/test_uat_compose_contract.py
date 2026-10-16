from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UAT = ROOT / "deploy" / "uat"


def test_uat_compose_has_no_external_route_or_non_loopback_ui_binding():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")

    assert "internal: true" in compose
    assert 'com.docker.network.bridge.enable_ip_masquerade: "false"' in compose
    assert '"127.0.0.1:${VFX_FILES_UAT_HTTP_PORT:-55280}:80"' in compose
    assert "5432:" not in compose
    assert "6379:" not in compose
    assert "google_drive" not in compose
    assert "VFX_FILES_ENABLED: ${VFX_FILES_ENABLED:-false}" in compose
    assert "VFX_FILES_WORKER_ENABLED: ${VFX_FILES_WORKER_ENABLED:-false}" in compose
    assert "The Worker service remains opt-in via its profile" in compose


def test_api_shares_a_default_deny_guard_and_worker_stays_on_backend_network():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    guard = compose.split("  egress-guard:\n", 1)[1].split("  api:\n", 1)[0]
    api = compose.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]
    worker = compose.split("  worker:\n", 1)[1].split("networks:\n", 1)[0]

    assert 'network_mode: "service:egress-guard"' in api
    assert 'iptables -P OUTPUT DROP' in guard
    assert 'ip6tables -P OUTPUT DROP' in guard
    assert "cap_add: [NET_ADMIN]" in guard
    assert "cap_drop: [ALL]" in api
    assert "cap_add: [NET_BIND_SERVICE, CHOWN, SETUID, SETGID]" in api
    assert "cap_drop: [ALL]" in worker
    assert "tmpfs: [/tmp, /run, /var/log/nginx]" in api
    assert "networks: [uat]" in worker


def test_uat_api_worker_share_image_and_durable_fake_storage_volumes():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    api = compose.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]
    worker = compose.split("  worker:\n", 1)[1].split("networks:\n", 1)[0]

    api_image = next(line.strip() for line in api.splitlines() if line.strip().startswith("image:"))
    worker_image = next(line.strip() for line in worker.splitlines() if line.strip().startswith("image:"))
    assert api_image == worker_image
    assert "profiles: [worker]" in worker
    for mount in ("staging_data:/var/lib/vfx-files/staging", "fake_storage:/var/lib/vfx-files/fake"):
        assert mount in api
        assert mount in worker
    for mount in ("zou_previews:/opt/zou/previews", "zou_postgresql_unused:/var/lib/postgresql:ro"):
        assert mount in api
        assert mount in worker


def test_every_service_declares_enforced_container_resource_limits():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    names = ("postgres", "redis", "egress-guard", "api", "worker")
    for index, service in enumerate(names):
        section = compose.split(f"  {service}:\n", 1)[1]
        for following in names[index + 1 :]:
            marker = f"\n  {following}:\n"
            if marker in section:
                section = section.split(marker, 1)[0]
        if "\nnetworks:\n" in section:
            section = section.split("\nnetworks:\n", 1)[0]
        assert "mem_limit:" in section
        assert "cpus:" in section
        assert "pids_limit:" in section
        assert "logging: *uat-logging" in section

    assert 'max-size: "10m"' in compose
    assert 'max-file: "3"' in compose


def test_removal_is_project_scoped_and_preserves_backup_directories():
    remove = (UAT / "scripts" / "remove.ps1").read_text(encoding="utf-8")

    assert "com.docker.compose.project" in remove
    assert "ConfirmUnit" in remove
    assert "docker system prune" not in remove
    assert "-Recurse" not in remove
    assert "backup directories were preserved" in remove
    assert "@('--profile', 'worker', 'down', '--volumes', '--remove-orphans')" in remove


def test_init_and_build_require_clean_committed_source_for_image_provenance():
    init = (UAT / "scripts" / "init.ps1").read_text(encoding="utf-8")
    build = (UAT / "scripts" / "build.ps1").read_text(encoding="utf-8")

    assert "status --porcelain --untracked-files=all" in init
    assert "status --porcelain --untracked-files=all" in build


def test_dynamic_network_selection_avoids_host_and_docker_route_collisions():
    common = (UAT / "scripts" / "common.ps1").read_text(encoding="utf-8")

    assert "Get-NetRoute -AddressFamily IPv4" in common
    assert "docker network inspect" in common


def test_synthetic_admin_creation_uses_container_password_environment_without_cli_secret():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    init_host = (UAT / "scripts" / "init-host.ps1").read_text(encoding="utf-8")

    assert "VFX_FILES_E2E_TEST_PASSWORD: ${VFX_FILES_UAT_TEST_PASSWORD:?" in compose
    assert '--password "$VFX_FILES_E2E_TEST_PASSWORD"' in init_host
    assert "--password $envMap" not in init_host


def test_host_initialization_starts_network_guard_before_api_one_shot_commands():
    init_host = (UAT / "scripts" / "init-host.ps1").read_text(encoding="utf-8")

    assert "@('up', '-d', 'postgres', 'redis', 'egress-guard')" in init_host
    assert "@('run', '--rm', '--no-deps', 'api', '/opt/zou/env/bin/zou', 'init-db')" in init_host
    assert "Wait-UatServicesHealthy -Context $ctx -Services @('postgres', 'redis', 'egress-guard')" in init_host
    assert 'zou is-db-ready' not in init_host


def test_scripts_wait_for_api_health_after_recreate():
    for script in ("install-plugin.ps1", "activate.ps1", "restore.ps1"):
        body = (UAT / "scripts" / script).read_text(encoding="utf-8")
        assert "Wait-UatServicesHealthy -Context $ctx -Services @('api')" in body


def test_activation_enables_upload_readiness_without_starting_worker():
    activate = (UAT / "scripts" / "activate.ps1").read_text(encoding="utf-8")
    start = (UAT / "scripts" / "start-worker.ps1").read_text(encoding="utf-8")

    assert "$envMap['VFX_FILES_WORKER_ENABLED'] = 'true'" in activate
    assert "This command does not start a Worker" in activate
    assert "--profile', 'worker'" in start


def test_installed_page_seed_materializes_only_an_inactive_synthetic_storage_mirror():
    seeder = (ROOT / "tests" / "e2e" / "seed_installed_page_flow.py").read_text(encoding="utf-8")
    seed_script = (UAT / "scripts" / "seed.ps1").read_text(encoding="utf-8")

    assert "KitsuSyncDesiredConfiguration(" in seeder
    assert '"activation": {"enabled": False, "activated_at": None, "notification_epoch": 0}' in seeder
    assert '"root_state": "verified"' in seeder
    assert '"Artist A ID: $($seed.artist_a_id)"' in seed_script
    assert '"Artist C ID: $($seed.artist_c_id)"' in seed_script


def test_restart_waits_for_services_and_only_restarts_previously_enabled_worker():
    start = (UAT / "scripts" / "start.ps1").read_text(encoding="utf-8")

    assert "@('up', '-d', 'postgres', 'redis', 'egress-guard')" in start
    assert "Wait-UatServicesHealthy -Context $ctx -Services @('postgres', 'redis', 'egress-guard')" in start
    assert "Wait-UatServicesHealthy -Context $ctx -Services @('api')" in start
    assert "if ($envMap['VFX_FILES_WORKER_ENABLED'] -ceq 'true')" in start
    assert "start-worker.ps1" in start


def test_api_image_redirects_nginx_logs_to_container_streams():
    dockerfile = (UAT / "Dockerfile").read_text(encoding="utf-8")

    assert "access_log /dev/stdout" in dockerfile
    assert "error_log /dev/stderr" in dockerfile
    assert "tests/e2e/restore_state_report.py" in dockerfile


def test_restore_starts_egress_guard_before_one_shot_api_restore_commands():
    snapshot = (UAT / "tools" / "snapshot.py").read_text(encoding="utf-8")

    assert '"up", "-d", "postgres", "redis", "egress-guard"' in snapshot


def test_normal_stop_shuts_down_api_worker_guard_and_backends():
    stop = (UAT / "scripts" / "stop.ps1").read_text(encoding="utf-8")

    assert "@('stop', 'worker', 'api', 'egress-guard', 'redis', 'postgres')" in stop


def test_zou_plugin_install_uses_a_distinct_source_and_runtime_tree():
    dockerfile = (UAT / "Dockerfile").read_text(encoding="utf-8")
    installer = (UAT / "scripts" / "install-plugin.ps1").read_text(encoding="utf-8")

    assert "COPY plugin/ /opt/vfx-files-uat/plugin-source/" in dockerfile
    assert "COPY plugin/ /opt/zou/plugins/vfx_files/" in dockerfile
    assert "'/opt/vfx-files-uat/plugin-source'" in installer


def test_api_and_worker_use_the_installed_plugin_folder_independent_of_working_directory():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    api = compose.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]
    worker = compose.split("  worker:\n", 1)[1].split("networks:\n", 1)[0]

    assert "PLUGIN_FOLDER: /opt/zou/plugins" in api
    assert "PLUGIN_FOLDER: /opt/zou/plugins" in worker


def test_api_readiness_requires_the_installed_files_route_and_auth_guard():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    dockerfile = (UAT / "Dockerfile").read_text(encoding="utf-8")
    probe = (UAT / "tools" / "check_api_health.py").read_text(encoding="utf-8")

    assert '"/opt/vfx-files-uat/check_api_health.py"' in compose
    assert "COPY deploy/uat/tools/check_api_health.py" in dockerfile
    assert "vfx_files/health" in probe
    assert "return status == 401" in probe


def test_files_api_readiness_rejects_missing_route_and_auth_regressions():
    from deploy.uat.tools.check_api_health import validate_plugin_auth_status

    assert validate_plugin_auth_status(401)
    assert not validate_plugin_auth_status(200)
    assert not validate_plugin_auth_status(404)


def test_fake_storage_root_matches_the_synthetic_seeder_contract():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")

    assert compose.count("VFX_FILES_FAKE_ROOT_ID: synthetic-root-id") == 2
    assert compose.count("VFX_FILES_FAKE_DRIVE_ID: synthetic-drive-id") == 2


def test_uat_api_receives_fail_closed_kitsusync_service_identity_configuration():
    compose = (UAT / "compose.yaml").read_text(encoding="utf-8")
    init = (UAT / "scripts" / "init.ps1").read_text(encoding="utf-8")
    api = compose.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]

    names = ("ISSUER", "AUDIENCE", "SUBJECT", "CLIENT_ID")
    for name in names:
        key = f"VFX_FILES_KITSUSYNC_{name}"
        assert f"{key}: ${{{key}:-}}" in api
        assert key in init
    assert "VFX_FILES_KITSUSYNC_CLIENT_ID=vfx-files-uat-$Unit" in init
