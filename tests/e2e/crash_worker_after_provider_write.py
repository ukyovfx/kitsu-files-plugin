"""Test-only process that exits after FakeStorage succeeds but before DB completion."""

import os
from dataclasses import replace

from zou.app import create_app

from vfx_files.worker_runtime import build_runtime
from vfx_files.worker.main import process_one


def main():
    app = create_app()
    with app.app_context():
        runtime = build_runtime(app)
        transfer = runtime.handlers["publish_source"]

        def terminate_after_external_write(job):
            transfer(job)
            os._exit(86)

        runtime = replace(runtime, handlers={"publish_source": terminate_after_external_write})
        process_one(runtime)


if __name__ == "__main__":
    main()
