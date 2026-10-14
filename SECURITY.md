# Security policy

## Reporting a vulnerability

Do not disclose security issues in public issues, discussions, or pull requests. Use GitHub private vulnerability reporting only after the repository owner has enabled and verified it. Until that channel is available, contact a maintainer through a verified private path; do not send technical details through a public channel or an unverified address. The repository owner must enable the private reporting feature and verify this policy before making the repository public.

Include the affected commit or release, impact, and a minimal synthetic reproduction. Do not send production media, credentials, access tokens, database dumps, or other users' data. Maintainers aim to acknowledge reports within five business days when a private channel is available. This is a response target, not a guaranteed service level.

## Supported security baseline

The only currently exercised host baseline is Kitsu 1.0.70 with Zou 1.0.94, as pinned by `environment/host-lock.yaml`. This is an isolated test baseline, not a claim of support for a studio host or for every Kitsu/Zou pairing. The product is still under development, the locked host dependency security gate remains open, and live Google Drive behavior, host compatibility, backup/restore operations, and deployment remain separate acceptance gates.

Keep the plugin feature disabled until its storage root, credentials, authorization, and worker configuration have been reviewed. Use a dedicated Google identity or Shared Drive membership with access limited to the configured root. The plugin does not need public link permissions. Do not use real credentials in tests or issue reports.
