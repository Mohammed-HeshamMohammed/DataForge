"""Parent side of the Scrapy engine: pre-checks site signals, starts the child process, relays progress,
honours pause and cancel through the control file, then validates records like the httpx engine."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..errors import PolicyViolation
from ..extraction import ScrapeResult, strategy_rationale, validate_candidates, validate_url
from ..fetch import make_client, user_agent
from ..signals import SignalChecker

SCRAPY_PAGE_THRESHOLD = 200


def choose_engine(preset: dict, max_pages: int, requested: str | None = None) -> str:
    """`auto` uses Scrapy only for sitemap or crawl discovery above the page threshold."""
    requested = requested or preset.get("engine", "auto")
    if requested in ("httpx", "scrapy"):
        return requested
    mode = (preset.get("discovery") or {}).get("mode", "none")
    return "scrapy" if mode in ("sitemap", "crawl") and max_pages > SCRAPY_PAGE_THRESHOLD else "httpx"


def engine_command(job_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):  # packaged service: same executable, second entry point
        return [sys.executable, "--engine", "scrapy", "--job", str(job_path)]
    return [sys.executable, "-m", "dataforge_scraping.engines.scrapy_engine", str(job_path)]


def collect_with_scrapy(
    start_url: str,
    preset: dict,
    max_records: int,
    max_pages: int,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[dict], None] = lambda event: None,
    is_paused: Callable[[], bool] = lambda: False,
    contact: dict | None = None,
    cache_dir: Path | None = None,
    purpose: str | None = None,
    incremental: bool = False,
    work_dir: Path | None = None,
    include_local_signals: bool = False,
    credential: str | None = None,
) -> ScrapeResult:
    if credential or ((preset.get("strategy") or {}).get("api_integration") or {}).get("auth"):
        raise PolicyViolation("The Scrapy engine does not handle credentials; run API presets with the httpx engine")
    if (preset.get("strategy") or {}).get("preferred") != "http":
        raise PolicyViolation("The Scrapy engine runs HTTP presets only")
    validate_url(start_url, preset)
    purpose = purpose or preset["policy"].get("purpose") or "internal_analysis"
    # Signals are resolved here with the OS-trusted httpx client, then applied identically by the child.
    with make_client(preset, contact) as client:
        checker = SignalChecker(client, purpose, preset["policy"].get("robots_policy", "respect"), include_local_signals)
        checker.check_url(start_url)
        for host in preset["url_scope"].get("allowed_hosts", []):
            if host != urlparse(start_url).hostname:
                checker.for_url(f"{urlparse(start_url).scheme}://{host}/")

    owns_dir = work_dir is None
    work = Path(work_dir or tempfile.mkdtemp(prefix="dataforge-scrapy-"))
    work.mkdir(parents=True, exist_ok=True)
    control = work / "control.txt"
    control.write_text("run", encoding="utf-8")
    job = {
        "start_url": start_url, "preset": preset, "max_records": max_records, "max_pages": max_pages, "purpose": purpose,
        "signals": checker.export(), "user_agent": user_agent(preset, contact), "cache_dir": str(cache_dir) if cache_dir else None,
        "control_file": str(control), "work_dir": str(work), "incremental": incremental,
    }
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "SCRAPY_SETTINGS_MODULE": ""}
    process = subprocess.Popen(engine_command(job_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               env=env, creationflags=creationflags, text=True, encoding="utf-8", errors="replace")
    lines: queue.Queue = queue.Queue()
    stderr_tail: list[str] = []

    def pump_stdout() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)  # end of stream

    def pump_stderr() -> None:
        for line in process.stderr:
            stderr_tail.append(line)
            del stderr_tail[:-200]

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()

    records: list[dict] = []
    warnings: list[str] = []
    done: dict = {}
    stop_message: str | None = None
    state = "run"

    def set_control(command: str) -> None:
        temporary = control.with_suffix(".tmp")
        temporary.write_text(command, encoding="utf-8")
        os.replace(temporary, control)

    try:
        while True:
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "item":
                    records.append(event["record"])
                elif kind == "page":
                    on_page({k: v for k, v in event.items() if k != "type"} | {"engine": "scrapy", "total_records": len(records)})
                elif kind == "warning":
                    warnings.append(event["message"])
                elif kind == "stop":
                    stop_message = event["reason"]
                elif kind == "done":
                    done = event
                elif kind == "error":
                    stop_message = stop_message or event["message"]
            if is_paused() and state == "run":
                set_control("pause")
                state = "pause"
            if should_stop():  # blocks while paused; returns True on cancel
                set_control("stop")
                state = "stop"
            elif state == "pause":
                set_control("run")
                state = "run"
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if owns_dir:
            import shutil

            shutil.rmtree(work, ignore_errors=True)

    if not done:
        tail = "".join(stderr_tail[-20:]).strip()
        raise RuntimeError(f"Scrapy engine exited without finishing (code {process.returncode}): {stop_message or tail[-600:]}")
    if stop_message and done.get("reason") not in ("cancelled",):
        raise PolicyViolation(stop_message)
    valid, rejected, more = validate_candidates(records, preset)
    valid = valid[:max_records]
    duplicates = len(records) - len(valid) - len(rejected)
    return ScrapeResult(
        tuple(valid), start_url, datetime.now(timezone.utc).isoformat(), "http", int(done.get("pages", 0)), len(rejected), max(0, duplicates),
        tuple(dict.fromkeys(warnings + done.get("warnings", []) + more + [w for s in checker.summary() for w in s["warnings"]])),
        str(done.get("reason", "completed")), strategy_rationale(preset) + " Ran on the Scrapy engine for a large discovery job.",
        engine="scrapy", signals=tuple(checker.summary()), cached_responses=int(done.get("cached", 0)), discovered_urls=int(done.get("discovered", 0)),
    )
