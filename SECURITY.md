# Security policy

## Reporting a vulnerability

Please do not disclose security issues in public issues, discussions, or pull requests. This repository is currently private and GitHub private vulnerability reporting is not enabled. If you already have a private contact path to the repository maintainers, use it; otherwise wait for a verified private reporting channel rather than posting technical details publicly. Before the repository becomes public, its owner must enable and verify GitHub private vulnerability reporting and confirm that this Security policy is visible.

Include the affected commit or release, impact, and a minimal synthetic reproduction. Do not send production media, credentials, access tokens, database dumps, or other users' data. Maintainers will acknowledge a report within five business days when a private channel is available and will coordinate remediation and disclosure with the reporter. This is a response target, not a guaranteed service level.

## Supported security baseline

The only currently exercised host baseline is Kitsu 1.0.70 with Zou 1.0.94, as pinned by `environment/host-lock.yaml`. This is an isolated test baseline, not a claim of support for an unverified Production host or for every Kitsu/Zou pairing. Google Drive live-provider behavior, Production compatibility, backup/restore operations, and deployment remain separate acceptance gates.

Keep the plugin feature disabled until its storage root, credentials, authorization, and worker configuration have been reviewed. Use a dedicated Google identity or Shared Drive membership with access limited to the configured root. The plugin does not need public link permissions. Do not use real credentials in tests or issue reports.
