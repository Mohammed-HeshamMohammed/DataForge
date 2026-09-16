"""Command-line entry point over the same versioned command API the desktop app uses.

    python -m dataforge_application.cli --project C:\\data\\my-project dataset.list
    python -m dataforge_application.cli --project C:\\data\\my-project dataset.import "{\\"path\\": \\"C:\\\\data\\\\leads.csv\\"}" --wait
    python -m dataforge_application.cli --create --project C:\\data\\new-project --name "New project" health.check
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .api import Service

TERMINAL = {"completed", "failed", "cancelled"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dataforge", description="Run DataForge commands against a local project.")
    parser.add_argument("--project", required=True, help="absolute path to the project folder")
    parser.add_argument("--create", action="store_true", help="create the project if it does not exist")
    parser.add_argument("--name", help="project name when creating")
    parser.add_argument("--wait", action="store_true", help="for commands that start a job, wait for it to finish")
    parser.add_argument("command", help="command name, for example dataset.list or match.create_job")
    parser.add_argument("payload", nargs="?", default="{}", help="JSON object payload")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as error:
        parser.error(f"payload is not valid JSON: {error}")
    if not isinstance(payload, dict):
        parser.error("payload must be a JSON object")

    service = Service()
    opened = service.handle({"schema_version": 1, "command": "project.create" if args.create else "project.open", "payload": {"path": args.project, "name": args.name}})
    if not opened["ok"] and args.create and "already exists" in opened["error"]["message"]:
        opened = service.handle({"schema_version": 1, "command": "project.open", "payload": {"path": args.project}})
    if not opened["ok"]:
        print(json.dumps(opened, indent=2), file=sys.stderr)
        return 2

    response = service.handle({"schema_version": 1, "command": args.command, "payload": payload})
    job_id = response.get("result", {}).get("job_id") if response["ok"] and isinstance(response.get("result"), dict) else None
    if args.wait and job_id:
        while True:
            job = service.handle({"schema_version": 1, "command": "job.get", "payload": {"job_id": job_id}})
            if not job["ok"] or job["result"]["state"] in TERMINAL:
                response = job
                break
            time.sleep(0.25)
    print(json.dumps(response, indent=2, ensure_ascii=False, default=str))
    if not response["ok"]:
        return 1
    return 1 if isinstance(response["result"], dict) and response["result"].get("state") in ("failed", "cancelled") else 0


if __name__ == "__main__":
    raise SystemExit(main())
