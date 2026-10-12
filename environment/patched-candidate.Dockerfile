FROM cgwire/cgwire:1.0.70-1.0.94-01@sha256:e6d9021241b12f88616762b4c7600a5e84d646119cba4a153ce7908fbd4f300a

COPY patched-candidate-requirements.txt /tmp/patched-candidate-requirements.txt

# Zou application code is unchanged. Adjust only its installed package metadata
# to the separately tested compatible fido2 and pysaml2 releases.
RUN /opt/zou/env/bin/python - <<'PY'
from pathlib import Path

metadata = Path("/opt/zou/env/lib/python3.10/site-packages/zou-1.0.94.dist-info/METADATA")
content = metadata.read_text(encoding="utf-8")
replacements = {
    "Requires-Dist: fido2==2.2.0": "Requires-Dist: fido2==2.2.1",
    "Requires-Dist: pysaml2==7.5.4": "Requires-Dist: pysaml2==7.5.5",
}
for old, new in replacements.items():
    if content.count(old) != 1:
        raise SystemExit(f"Expected exactly one pinned metadata entry: {old}")
    content = content.replace(old, new)
metadata.write_text(content, encoding="utf-8")
PY

RUN /opt/zou/env/bin/python -m pip install --no-deps --require-hashes \
    --disable-pip-version-check \
    --requirement /tmp/patched-candidate-requirements.txt \
    && /opt/zou/env/bin/python -m pip check
