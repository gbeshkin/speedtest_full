import os
import time
import datetime as dt

import healthcheck
import pagespeed


INTERVAL_SECONDS = int(os.environ.get("HEALTH_INTERVAL_SECONDS", "60"))
RETENTION_EVERY_CHECKS = int(os.environ.get("HEALTH_RETENTION_EVERY_CHECKS", "60"))


def trim_history(now: dt.datetime) -> None:
    retained = pagespeed.filter_recent_history(
        pagespeed.tail_jsonl(healthcheck.HEALTH_HISTORY_FILE, healthcheck.RETENTION_POINTS * 2),
        now,
        healthcheck.RETENTION_DAYS,
    )
    pagespeed.rewrite_history(healthcheck.HEALTH_HISTORY_FILE, retained)


def write_report(now: dt.datetime) -> None:
    retained = pagespeed.tail_jsonl(healthcheck.HEALTH_HISTORY_FILE, healthcheck.RETENTION_POINTS * 2)
    recent = pagespeed.filter_recent_history(retained, now, healthcheck.HISTORY_DAYS)
    run_label = now.astimezone(pagespeed.DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M %Z")

    with open(healthcheck.HEALTH_REPORT_FILE, "w", encoding="utf-8") as file:
        file.write(healthcheck.build_health_html(run_label, recent))


def main() -> None:
    os.makedirs(healthcheck.OUT_DIR, exist_ok=True)
    checks = 0

    print(
        "Starting Railway health worker:",
        "interval_seconds=",
        INTERVAL_SECONDS,
    )

    while True:
        started = time.monotonic()
        now = dt.datetime.now().astimezone()
        entry = healthcheck.health_entry(now)

        pagespeed.append_jsonl(healthcheck.HEALTH_HISTORY_FILE, entry)
        checks += 1

        write_report(now)

        if checks % max(1, RETENTION_EVERY_CHECKS) == 0:
            trim_history(now)

        elapsed = time.monotonic() - started
        sleep_for = max(0, INTERVAL_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
