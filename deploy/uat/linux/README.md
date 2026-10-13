# Ubuntu isolated UAT handoff

This is an operator runbook for an isolated UAT instance. It is not permission to install on VFX Studio Server. The official Kitsu/Zou host security gate remains **FAIL**. The patched 1.0.70/1.0.94 host is an unofficial candidate and requires separate owner approval before any server use.

## Admission conditions

Do not start the unit until the server owner has supplied and approved all of the following:

1. A dedicated rootful Docker daemon and Unix socket for UAT. Do not point this package at the Production Docker daemon. Configure the daemon with a data-root on a dedicated, quota-enforced filesystem. The filesystem quota must cover image layers, writable layers, named volumes, container logs, build cache, backup and restore staging. Compose has no portable named-volume quota. `df` capacity alone does not prove quota enforcement.
2. Ubuntu with systemd, cgroup v2, Docker Engine using the `systemd` cgroup driver, and the Compose plugin. The UAT Docker daemon service and every UAT container must be children of one owner-created systemd slice. The slice must have owner-approved aggregate CPU and memory limits. Preserve the measured per-container limits in `compose.yaml`; do not lower them to make the aggregate budget appear to fit.
3. The slice limits and quota have been read back from the host (`systemctl show`, cgroup `cpu.max` / `memory.max`, filesystem quota report, and `docker info`). Save sanitized output with the change record. `2 CPU / 2 GiB / 20 GiB` remains provisional and is not accepted by this package as a proven safe budget.
4. A dedicated private path for the UAT state and backups, encrypted or access-controlled under the studio's normal server policy, with capacity for a full backup plus restore validation in addition to the live data.
5. A change window, named UAT owner, synthetic account approval, monitoring/stop owner, and separate approval for the unofficial patched host candidate. No Production Kitsu/Zou connection, real media, KitsuSync internal database, Google credentials, or real Drive operations are permitted.

If any item is unavailable, mark server admission **BLOCKED** and stop. Never attach this stack to an existing Production network or volume.

## Pinned source and image identity

Use a clean checkout of the approved Draft PR #4 source commit. At the current package checkpoint, the source is `a19d913a35917717d1dd1d6a05be94e01a96aa19`; after this run's documentation/package changes, use the final PR #4 HEAD recorded in the PR. Do not use a floating branch name as the deployment identity.

The local Windows Docker daemon currently has an API/Worker image built from the pre-change `a19d913…` source with image ID/digest `sha256:b564cfd791380d398d1e7352832ceb965ad759c32a9fbecd39aec0fe9f5f216d`. This is **not** an Ubuntu server image artifact. A server package is acceptable only after the exact final source commit is built, the resulting API/Worker image ID is read back, recorded in the manifest, and the same immutable image is used for API, Worker, backup restore and readiness checks. Do not claim reproducible image equality from a tag or source label alone.

Build uses `environment/host-lock.yaml` and `environment/patched-candidate.Dockerfile`. The candidate inherits the pinned Kitsu 1.0.70 / Zou 1.0.94 image but changes dependencies. It remains unofficial and does not clear the official host vulnerability gate.

## Isolation and endpoint contract

- Give every unit a new project name (`vfx-files-uat-<unit>`), private state directory, Docker network pair and project-scoped volumes. Never reuse Windows UAT volumes or names.
- Configure `VFX_FILES_UAT_CGROUP_PARENT` to the exact owner-created slice name (ending in `.slice`) for all Compose services. Confirm the Docker daemon itself also runs inside that slice. Read back every container's cgroup path and the effective parent limits before seeding data.
- Use the package's internal backend network and egress guard. PostgreSQL and Redis have no published ports; API/Worker use the same candidate image; Worker remains a separate service. Storage mode is `fake` only.
- Keep the published UI binding as `127.0.0.1:<host-port>:80`. Do not add `0.0.0.0`, a reverse proxy, public DNS, or firewall exposure.
- Create the local state directory with mode `0700` and secret/account/backup files with mode `0600`. Keep it outside the Git checkout. Never print `.env`, account passwords, JWTs, cookies, `docker inspect` environment fields, or raw seeder JSON.
- Restrict Docker socket access to the named UAT operators; Docker access is root-equivalent. Do not grant the UAT account access to the Production daemon socket.

## Ubuntu operator sequence

The following is a gated runbook, not a remote action performed by this repository task. Set `REPO` to the clean checkout and `UNIT` to a new short lowercase identifier. Use only the dedicated UAT Docker socket and state root approved above.

```bash
set -euo pipefail
umask 077
REPO=/srv/vfx-files-uat/source
UNIT=uat01
STATE_ROOT=/srv/vfx-files-uat/state
PROJECT="vfx-files-uat-${UNIT}"
ENV_FILE="${STATE_ROOT}/${UNIT}/uat.env"
COMPOSE="${REPO}/deploy/uat/compose.yaml"
```

Before the first `docker compose` command, verify the source is clean and exact, the Docker socket is the dedicated UAT daemon, its `DockerRootDir` is on the quota filesystem, the daemon is inside the shared systemd slice, and the slice's CPU/memory values and filesystem quota have been read back. Refuse to proceed if Production networks, database endpoints, storage credentials, or non-synthetic secrets appear in the environment. Set `VFX_FILES_UAT_CGROUP_PARENT` in the protected env file to the approved slice.

Initialize a fresh project and protected state directory. Generate four independent random values with `openssl rand -hex 32` for DB, Redis, Kitsu secret, and synthetic test password. Select two unused private `/24` ranges after comparing `ip route` with all Docker networks; never assume a subnet is free. Set the source full/short SHA, candidate image tag, loopback port, `VFX_FILES_ENABLED=false`, and `VFX_FILES_WORKER_ENABLED=false`. Create the file with `install -m 600 /dev/null "$ENV_FILE"` and write values without echoing them. Refuse to overwrite existing state or a project with matching Docker resources.

The current Compose file accepts `VFX_FILES_UAT_CGROUP_PARENT` for all five runtime services. On Linux set it to the pre-created, reviewed slice (for example `vfx-files-uat.slice`); blank is only the local Windows compatibility default. Do not infer that a name alone enforces a quota: read the actual container cgroup paths and parent limits back from the kernel.

Build the pinned host candidate and plugin image from the exact checkout. Read back the application image ID and `org.opencontainers.image.revision`; verify API and Worker resolve to the same image ID. With the protected variables already in `ENV_FILE`, the operator's commands are:

```bash
CANDIDATE="vfx-files-host-candidate:uat-${SOURCE_SHORT}"
docker build --file "$REPO/environment/patched-candidate.Dockerfile" --tag "$CANDIDATE" "$REPO/environment"
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" build api egress-guard
docker image inspect "vfx-files-uat-app:${SOURCE_SHORT}" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" up -d postgres redis egress-guard
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" run --rm --no-deps api /opt/zou/env/bin/zou init-db
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" run --rm --no-deps api /opt/zou/env/bin/zou init-data --domain vfx
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" run --rm --no-deps --entrypoint /bin/sh api -ec '/opt/zou/env/bin/zou create-admin admin@example.com --password "$VFX_FILES_E2E_TEST_PASSWORD"'
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" up -d api
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" run --rm --no-deps api /opt/zou/env/bin/zou install-plugin --path /opt/vfx-files-uat/plugin-source
docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" up -d --force-recreate api
```

Wait until `docker compose ... ps` reports PostgreSQL, Redis, egress guard and API healthy; verify the UI is bound only to `127.0.0.1` and the plugin health route is reachable. Set `VFX_FILES_ENABLED=true` only after this point. The synthetic page seeder has a Linux-safe wrapper that captures its short-lived JSON/JWT response in process memory, suppresses stderr, writes only synthetic account IDs/emails and the shared test password to a new mode-600 file, and never emits tokens:

```bash
python3 "$REPO/deploy/uat/tools/seed_linux.py" --project "$PROJECT" --env-file "$ENV_FILE" --compose "$COMPOSE"
```

The wrapper requires every account email to use the `example.invalid` synthetic domain and refuses to overwrite `ACCOUNTS.txt`. Keep plugin feature and Worker flags disabled until the owner has reviewed the isolated KitsuSync desired configuration. Never run the seeder against a non-loopback or non-disposable host.

Drive connection/root/template selection and activation belong to KitsuSync; the Files UI is read-only for folder bindings. This UAT package does not deploy a KitsuSync service. The current Plugin sync worker also requires a Plugin-owned nested folder tree that KitsuSync v1 does not yet materialize, so folder-provisioning Human UAT is blocked until that integration contract/provider path is completed. Do not create the missing configuration through retired Files endpoints. Enable and start the separate Worker only for a test window with an authorized, synthetic KitsuSync configuration and a supported materialized template. Verify API root, plugin health, Worker process, backend network isolation, and loopback-only port before Human UAT. Record exact source/image identity, Compose config digest, cgroup readbacks, quota evidence, and sanitized readiness results.

### SSH access for the owner

Keep the remote service bound to loopback. On the owner's workstation, forward a local loopback port to the server's loopback port:

```bash
ssh -N -L 127.0.0.1:55280:127.0.0.1:55280 <approved-uat-host>
```

Open `http://127.0.0.1:55280` locally and use the synthetic Manager account through the ordinary Kitsu login. The exact port may differ; use the one recorded in the protected unit configuration. Do not share account credentials in chat or ticket comments. Human UI operations are performed by the owner, not browser automation.

### Stop, restart, backup, restore and removal

- Stop without data loss: `docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" --file "$COMPOSE" --profile worker stop worker api`; leave DB and volumes intact. Restart in order: `up -d postgres redis egress-guard`, wait healthy, then `up -d api`, wait for API health. Start Worker separately only when authorized and only after confirming no restore gate is pending.
- Backup: stop Worker and API first, then run `python3 "$REPO/deploy/uat/tools/snapshot.py" backup --root "$REPO" --compose "$COMPOSE" --env-file "$ENV_FILE" --project "$PROJECT" --output "$STATE_ROOT/$UNIT/backup-<UTC timestamp>"`. Keep the output in the protected quota filesystem and verify manifest, database dump, storage tar and SHA-256 readback. Backups exclude secrets and are not a substitute for a host-level backup.
- Restore: use a new empty unit and the exact captured app image ID. Run `snapshot.py restore` with the matching source SHA, new project/env/Compose, original backup directory and a fresh private validation output directory. It restores DB and FakeStorage/staging while API and Worker are stopped and starts API only for read-only integrity comparison. Confirm record counts, Latest Working/Published/Approved pointers, manifest hashes and each object checksum. Keep Worker stopped until the restore marker's manifest SHA is independently reviewed and explicitly confirmed through the existing restore-gated worker contract. Never downgrade `vxf_0006` as a recovery procedure.
- Removal: verify the exact Compose project label on each container, then remove only that project and its labeled networks/volumes after the approved retention window. Preserve backups unless the owner applies the separate backup-retention policy. Never run `docker system prune`, `docker volume prune`, or unscoped `down --volumes`.

The existing `deploy/uat/scripts/*.ps1` automate the Windows workflow. They are not Ubuntu scripts. Until equivalent Linux lifecycle automation and restore tests have run on an Ubuntu host, treat the sequence above as an operator procedure requiring a reviewed command record; do not represent Ubuntu lifecycle validation as PASS.

## Local validation boundary

This development host exposes a Linux Docker Engine through Docker Desktop, but the host is Windows and the engine reports `cgroupfs`, not Ubuntu systemd. It can validate Linux images/Compose, loopback publication, API/Worker image identity, network isolation and FakeStorage smoke operations. It cannot prove Ubuntu systemd slice enforcement, a dedicated daemon/data-root, filesystem quota, server stop/restart or full server restore behavior. Those items stay **NOT TESTED** locally and **BLOCKED** for server admission until the server owner supplies evidence.

The measured current Compose ceilings sum to 4.75 vCPU and 8.625 GiB RAM across services; they are per-container maxima, not a UAT-wide reservation. Do not assert that 2 CPU / 2 GiB is sufficient without representative peak tests under the enforced parent slice. The 20 GiB disk target requires a dedicated quota filesystem covering the complete Docker data-root and backup/restore staging, plus quota readback; Compose volume settings do not enforce it.

## Issue #5: task-detail panel feasibility only

Do not implement a task drawer in this UAT package. Before a separate design/implementation change, verify against the exact supported Kitsu 1.0.70 source and official extension contract:

1. Is there a documented plugin API to register a tab/panel in Shot task detail (not only a Production project page)? Identify its route/component lifecycle, versioning and permission model from primary source.
2. Can the extension receive stable Production, Shot and Task UUIDs from the selected row without DOM scraping or undocumented global state? Confirm route changes, drawer reuse, refresh, back navigation and concurrent task switches cannot leak state.
3. Can the existing Kitsu session/CSRF and Zou API authorization be reused safely in the embedded panel, with server-side Production/Task authorization unchanged?
4. Can upload/download, Working revisions, Publish/QC, exact-Publish approval and Handoff be presented without duplicating existing backend APIs or bypassing existing role checks?
5. What is the smallest upstream-safe hook if no official task-detail extension point exists? Compare a small upstream Kitsu extension API proposal against a separately maintained core patch; do not use DOM injection.
6. What isolated browser fixture and Manager/Artist A/Artist B/Supervisor E2E would demonstrate the selected API at the pinned Kitsu version? Keep Production → Assets → VFX Files as the current management page until this gate is separately approved.

Record evidence for each question as PASS / FAIL / BLOCKED / NOT TESTED. Issue #5's UX goal remains separate from this server UAT admission.
