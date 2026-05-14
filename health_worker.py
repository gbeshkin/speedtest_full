import os
import time
import json
import datetime as dt
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from typing import Deque, Dict, Any

import healthcheck
import pagespeed


INTERVAL_SECONDS = int(os.environ.get("HEALTH_INTERVAL_SECONDS", "60"))
ALERT_WINDOW_CHECKS = int(os.environ.get("HEALTH_ALERT_WINDOW_CHECKS", "3"))
RETENTION_EVERY_CHECKS = int(os.environ.get("HEALTH_RETENTION_EVERY_CHECKS", "60"))
PORT = int(os.environ.get("PORT", "8080"))


def recent_health() -> list:
    retained = pagespeed.tail_jsonl(healthcheck.HEALTH_HISTORY_FILE, healthcheck.RETENTION_POINTS * 2)
    now = dt.datetime.now().astimezone()
    return pagespeed.filter_recent_history(retained, now, healthcheck.HISTORY_DAYS)


def trim_history(now: dt.datetime) -> None:
    retained = pagespeed.filter_recent_history(
        pagespeed.tail_jsonl(healthcheck.HEALTH_HISTORY_FILE, healthcheck.RETENTION_POINTS * 2),
        now,
        healthcheck.RETENTION_DAYS,
    )
    pagespeed.rewrite_history(healthcheck.HEALTH_HISTORY_FILE, retained)


def write_report(now: dt.datetime) -> None:
    recent = recent_health()
    run_label = now.astimezone(pagespeed.DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M %Z")

    with open(healthcheck.HEALTH_REPORT_FILE, "w", encoding="utf-8") as file:
        file.write(healthcheck.build_health_html(run_label, recent))


def current_status() -> dict:
    recent = recent_health()
    urls = {}

    for url in pagespeed.URLS:
        stats = healthcheck.health_stats(recent, url)
        urls[pagespeed.short_name(url)] = {
            "url": url,
            "availability": stats["availability"],
            "checks": stats["total"],
            "http_502": stats["http_502"],
            "http_504": stats["http_504"],
            "network_errors": stats["errors"],
            "last_status": stats["last_status"],
            "last_latency": stats["last_latency"],
            "last_502": stats["last_502"],
            "last_504": stats["last_504"],
            "last_checked": stats["last_checked"],
        }

    return {
        "generated_at": dt.datetime.now().astimezone(pagespeed.DISPLAY_ZONE).isoformat(timespec="seconds"),
        "window_days": healthcheck.HISTORY_DAYS,
        "urls": urls,
    }


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in ("/", "/health.html"):
            self.serve_health_html()
            return

        if path == "/health.json":
            self.serve_health_json()
            return

        self.send_error(404)

    def serve_health_html(self) -> None:
        write_report(dt.datetime.now().astimezone())

        with open(healthcheck.HEALTH_REPORT_FILE, "rb") as file:
            data = file.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_health_json(self) -> None:
        data = json.dumps(current_status(), ensure_ascii=False, indent=2).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print("HTTP:", self.address_string(), format % args)


def start_http_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    print("Serving health dashboard on port", PORT)
    server.serve_forever()


def main() -> None:
    os.makedirs(healthcheck.OUT_DIR, exist_ok=True)
    recent_batch: Deque[Dict[str, Any]] = deque(maxlen=max(1, ALERT_WINDOW_CHECKS))
    checks = 0
    threading.Thread(target=start_http_server, daemon=True).start()

    print(
        "Starting Railway health worker:",
        "interval_seconds=",
        INTERVAL_SECONDS,
        "alert_window_checks=",
        ALERT_WINDOW_CHECKS,
    )

    if healthcheck.EMAIL_TEST_ON_START:
        healthcheck.send_startup_test_email(dt.datetime.now().astimezone())

    while True:
        started = time.monotonic()
        now = dt.datetime.now().astimezone()
        entry = healthcheck.health_entry(now)

        pagespeed.append_jsonl(healthcheck.HEALTH_HISTORY_FILE, entry)
        recent_batch.append(entry)
        checks += 1

        healthcheck.process_email_alerts(list(recent_batch), now)
        write_report(now)

        if checks % max(1, RETENTION_EVERY_CHECKS) == 0:
            trim_history(now)

        elapsed = time.monotonic() - started
        sleep_for = max(0, INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
