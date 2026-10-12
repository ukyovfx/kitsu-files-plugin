ARG CANDIDATE_IMAGE=vfx-files-host-candidate:unset
FROM ${CANDIDATE_IMAGE}

ARG SOURCE_HEAD
LABEL org.opencontainers.image.revision=${SOURCE_HEAD} \
      org.opencontainers.image.source="https://github.com/ukyovfx/vfx-kitsu-files-plugin" \
      org.vfx-files.uat-only="true"

# Container logs must not depend on writable host-style /var/log files.
RUN sed -i \
      -e 's|access_log /var/log/nginx/access.log;|access_log /dev/stdout;|' \
      -e 's|error_log /var/log/nginx/error.log;|error_log /dev/stderr;|' \
      /etc/nginx/nginx.conf

COPY plugin/ /opt/vfx-files-uat/plugin-source/
COPY plugin/ /opt/zou/plugins/vfx_files/
COPY deploy/uat/supervisord-api.conf /opt/vfx-files-uat/supervisord-api.conf
COPY tests/e2e/seed_installed_page_flow.py tests/e2e/seeder_safety.py tests/e2e/restore_state_report.py /opt/vfx-files-uat/

ENV PYTHONPATH=/opt/zou/plugins

# The API and Worker use this same image. Database and Redis are separate
# services in compose.yaml; the upstream application image remains unchanged.
CMD ["/usr/bin/supervisord", "-n", "-c", "/opt/vfx-files-uat/supervisord-api.conf"]
