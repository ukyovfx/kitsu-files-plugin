# Third-party notices — DRAFT FOR RIGHTS-HOLDER REVIEW

This draft describes third-party runtime code bundled in the current plugin page. It is not legal advice, a final notice, or permission to redistribute. The plugin's own license and copyright ownership remain unconfirmed; no root `LICENSE` file is present.

## Bundled frontend runtime inventory

The offline handoff archive's CycloneDX inventory is `distribution-sbom.cdx.json`. Its file entries must match every tracked `plugin/` path and Git-object SHA-256 at the packaged commit. The runtime components listed below were checked against the generated frontend assets and the exact npm lock/install metadata:

| Bundled runtime component | Locked version | License identifier | Copyright notice observed in package `LICENSE` |
|---|---:|---|---|
| `vue` | 3.5.43 | MIT | Copyright (c) 2018-present, Yuxi (Evan) You |
| `@vue/runtime-dom` | 3.5.43 | MIT | Copyright (c) 2018-present, Yuxi (Evan) You |
| `@vue/runtime-core` | 3.5.43 | MIT | Copyright (c) 2018-present, Yuxi (Evan) You |
| `@vue/reactivity` | 3.5.43 | MIT | Copyright (c) 2018-present, Yuxi (Evan) You |
| `@vue/shared` | 3.5.43 | MIT | Copyright (c) 2018-present, Yuxi (Evan) You |
| `@noble/hashes` | 1.8.0 | MIT | Copyright (c) 2022 Paul Miller (https://paulmillr.com) |

The following license text blocks were copied from the corresponding exact-version npm package `LICENSE` files for review. They describe third-party runtime code only; they do not license this repository's own source.

### Vue runtime packages — MIT

```text
The MIT License (MIT)

Copyright (c) 2018-present, Yuxi (Evan) You

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### `@noble/hashes` — MIT

```text
The MIT License (MIT)

Copyright (c) 2022 Paul Miller (https://paulmillr.com)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

Before publication, the rights holder should confirm the required notice form and include these texts and applicable copyright statements in the approved release artifact.

`frontend-build-sbom.cdx.json` covers the broader npm build/test dependency graph and is not a claim that those tools ship in the plugin page. The bundle also carries `frontend/package-lock.json` as a build identity record; it does not include `node_modules`, Python environments, or Kitsu/Zou host packages. Reconcile the exact shipped runtime inventory and complete notice texts again whenever the built frontend assets or lockfile change.

## Host-provided software

Kitsu and Zou are external host software and are not included in the plugin archive. The tested disposable pair is recorded in `environment/host-lock.yaml`; this is not a supported-version guarantee. Obtain and follow the applicable upstream notices for any host image distributed separately.

## Required owner review

- Confirm plugin source copyright and authority to license it.
- Confirm the offline archive's package manifest, SBOM file hashes, actual frontend assets and exact bundled dependency inventory.
- Add required third-party license texts and notices after review.
- Decide and add the plugin's license only after rights-holder approval.
