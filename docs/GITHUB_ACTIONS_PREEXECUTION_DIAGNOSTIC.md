# GitHub Actions pre-execution diagnostic

Status: **BLOCKED — cause unknown; no CI pass is claimed.** This is a sanitized owner/support handoff, not evidence that billing or quota caused the failure.

## Observed evidence

- Example run: [run 38218249340](https://github.com/ukyovfx/vfx-kitsu-files-plugin/actions/runs/38218249340).
- The recorded jobs failed before any workflow step began; the step lists were empty and the job-log endpoint returned `log not found`.
- This means the run did not execute project tests. It is neither a code-test failure nor a CI pass.
- The available evidence does not distinguish account/organization policy, runner assignment, service-side failure, billing/usage limits, or another pre-job condition. Billing is only a hypothesis and has not been verified.

## Smallest owner diagnostic

1. Open the example run while signed in as a repository or organization Actions owner and inspect the run/job annotations and repository/org runner availability.
2. If GitHub provides no cause in the run UI, contact GitHub Support with the run URL and ask why job execution was rejected before the first step. Do not include credentials, JWTs, cookies, production data, or private logs.
3. Check billing only if GitHub identifies a usage/budget error. Do not change budgets or account settings merely because the steps are empty.
4. Restore hosted Actions or explicitly approve a separately reviewed equivalent validation route; keep required checks visible and do not mark unexecuted workflows successful.

Official guidance: [Troubleshooting workflows](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows), [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
