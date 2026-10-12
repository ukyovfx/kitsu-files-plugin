# Third-party notices — DRAFT FOR RIGHTS-HOLDER REVIEW

This draft describes third-party runtime code bundled in the current plugin page. It is not legal advice, a final notice, or permission to redistribute. The plugin's own license and copyright ownership remain unconfirmed; no root `LICENSE` file is present.

## Bundled frontend runtime inventory

| Package | Locked version | License identifier | Use in the current distribution candidate |
|---|---:|---|---|
| Vue (`vue`, including the Vue runtime packages bundled by Vite) | 3.5.43 | MIT | Frontend UI runtime |
| `@noble/hashes` | 1.8.0 | MIT | Browser-side SHA-256 support |

The package lock and the build-environment SBOM are retained in this repository. The lockfile SBOM also includes test and build tools that are not shipped in the plugin archive. Confirm the exact bundled transitive components and required copyright/license text from the corresponding upstream package releases before distribution.

## Host-provided software

Kitsu and Zou are external host software and are not included in the plugin archive. The tested disposable pair is recorded in `environment/host-lock.yaml`; this is not a supported-version guarantee. Obtain and follow the applicable upstream notices for any host image distributed separately.

## Required owner review

- Confirm plugin source copyright and authority to license it.
- Confirm the actual release artifact contents and exact bundled dependency inventory.
- Add required third-party license texts and notices after review.
- Decide and add the plugin's license only after rights-holder approval.
