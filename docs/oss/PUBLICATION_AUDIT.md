# GitHub publication audit

## Scope

- Repository: `ukyovfx/vfx-kitsu-files-plugin`
- Source checkpoint: `bb732e3c3932f7488053e7e82ea5243d2358c121` (`codex/fake-shared-drive-identity`, Draft PR #12 head)
- Scan scope: all locally available Git refs with `gitleaks git --log-opts=--all`, plus the source tree at that checkpoint.
- This review did not connect to Production, Staging, KitsuSync data, or Google Drive. It did not verify or use any credential.

## Findings

- **Secret scan:** five low-confidence `generic-api-key` findings occur in test fixtures across three test files. The values are fixed synthetic test data. No actual credentials were confirmed in Git history or in the source tree by this scan. The matches were not copied into this report.
- **Environment examples:** `deploy/uat/.env.example` is a tracked template, not an active `.env`; its values are example settings and generated placeholders. Review its subnet and port defaults against public documentation before publication. It does not contain passwords or active credentials.
- **Build output:** `plugin/frontend/dist/` is tracked because the plugin serves the compiled UI. It is product distribution output, not a local build cache; its embedded dependency notices still need exact-artifact reconciliation.
- **External identifiers:** source and history include synthetic Google Drive folder URL fixtures and email-shaped test identities. They are test inputs, not proof of external account access. An earlier private review recorded a non-reserved Google Drive folder URL in retained test material; confirm with its owner that it is synthetic before public visibility. This scan did not contact it.
- **Author metadata:** the reviewed history has 98 commits; 97 use an address that is not GitHub's noreply form, with two distinct author addresses present. This exposes contributor identity metadata. Review with repository contributors before public visibility; no address values were copied here, and no history rewrite or force push was done.
- **Third-party licensing:** the root has no project `LICENSE`. `plugin/manifest.toml` says `MIT`, but ownership and redistribution rights remain unverified. The attached notice draft covers identified frontend runtime packages only; it is not a complete distribution SBOM or permission to publish.
- **Security reporting:** an earlier review recorded private vulnerability reporting as not enabled. The current GitHub API response did not expose the setting, so its present value is unverified. The repository owner must verify the setting and reporting workflow before making the repository public.
- **Product readiness:** the product is still in Draft PRs. The official locked host dependency gate is open and live Drive, Production compatibility, and external security review remain unverified. Do not describe `main` as a stable release.

## Tracked-file review

The tracked tree at this checkpoint contains a deployment `.env.example`, compiled frontend assets, tests, and source. No active `.env`, key file, database, archive, or local `node_modules` directory is tracked. The `.env.example` and compiled UI are intentional templates/distribution assets and should remain covered by release-specific review.

## Before repository visibility changes

1. Obtain written rights-holder and copyright confirmation. Add a root `LICENSE` only after authorization.
2. Reconcile all shipped runtime/build components and include their applicable notices in the exact package; do not rely on this preliminary frontend-only notice draft.
3. Review author metadata and the historical Drive URL with the relevant owners. Any history rewrite requires a separate plan because it changes existing refs and open pull requests.
4. Enable and verify private vulnerability reporting and confirm the policy appears as intended.
5. Close the locked-host dependency gate and clearly document that Production and real-provider acceptance remain separate.
6. Re-scan all refs immediately before any public visibility change. The current work only prepares documentation and a draft pull request; it does not change repository visibility.

## Description and topic proposal

Suggested repository description: **In-development Kitsu/Zou plugin for Production-scoped Working files, Publish, QC, approvals, and handoffs.**

Suggested topics: `kitsu`, `zou`, `vfx`, `digital-content-creation`, `google-drive`, `workflow-automation`.

Keep “in-development” in the description while product changes remain unmerged and release gates remain open. These are proposals only; repository metadata was not changed.
