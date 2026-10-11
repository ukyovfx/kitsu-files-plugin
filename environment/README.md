# Isolated host environment

`host-lock.yaml` records immutable Kitsu/Zou source refs and the official combined Docker test image. The image is disposable, local-only and uses no mounted volumes or external credentials. Runtime behavior is not supported for Production until plugin install, authentication, API and migration checks pass against this exact image.

The image was pulled locally by digest and started without a volume. Its Zou CLI package reports `1.0.94`, its Python runtime is `3.10.12`, and the unauthenticated `/api` readiness response returned HTTP 200. This confirms image identity and basic service startup only; plugin installation, authenticated route behavior and migrations remain implementation acceptance gates.

The frontend build runs on Node 24.14.0 / npm 11.11.0 with the versions in `frontend/package-lock.json`. The plugin uses a small `fetch` wrapper because the official JavaScript client's token-provider and transport behavior has not been proven against this host. It reads the host bearer token for each request, never enables cookie credentials, and does not persist tokens.
