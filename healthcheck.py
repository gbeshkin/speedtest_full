import os
import time
import json
import smtplib
import datetime as dt
from email.message import EmailMessage
from typing import Any, Dict, List

import pagespeed


OUT_DIR = "reports"
HEALTH_HISTORY_FILE = os.path.join(OUT_DIR, "health-history.jsonl")
HEALTH_REPORT_FILE = os.path.join(OUT_DIR, "health.html")
EMAIL_STATE_FILE = os.path.join(OUT_DIR, "health-email-state.json")
PAGESPEED_REPORT_URL = "https://gbeshkin.github.io/speedtest_full/"
FULL_PAGESPEED_REPORT_URL = "https://gbeshkin.github.io/speedtest_full/full.html"

HISTORY_DAYS = 3
RETENTION_DAYS = 30
RETENTION_POINTS = 43200

CHECKS_PER_RUN = int(os.environ.get("HEALTH_CHECKS_PER_RUN", "5"))
CHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", "60"))
EMAIL_ENABLED = os.environ.get("EMAIL_ENABLED", "false").lower() == "true"
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [item.strip() for item in os.environ.get("EMAIL_TO", "").split(",") if item.strip()]
HEALTH_REPORT_URL = os.environ.get("HEALTH_REPORT_URL", "")


def health_entry(now: dt.datetime) -> Dict[str, Any]:
    results = []

    for url in pagespeed.URLS:
        health = pagespeed.check_url_health(url)
        print(
            "Health check:",
            pagespeed.short_name(url),
            "status=",
            health.get("status"),
            "latency_ms=",
            health.get("latency_ms"),
            "error=",
            health.get("error"),
        )
        results.append(
            {
                "timestamp": now.isoformat(timespec="seconds"),
                "time": now.strftime("%H:%M:%S"),
                "url": url,
                "health": health,
            }
        )

    return {
        "timestamp": now.isoformat(timespec="seconds"),
        "time": now.strftime("%H:%M:%S"),
        "results": results,
    }


def format_percent(ok_count: int, total: int) -> str:
    if total == 0:
        return "-"
    return "{}%".format(int(round((ok_count / float(total)) * 100)))


def read_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def write_json(path: str, data: Dict[str, Any]) -> None:
    tmp_path = path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")

    os.replace(tmp_path, path)


def health_stats(history: List[Dict[str, Any]], url: str) -> Dict[str, Any]:
    total = 0
    ok = 0
    http_504 = 0
    errors = 0
    last_status = "-"
    last_latency = "-"
    last_504 = "-"
    last_checked = "-"

    for entry in history:
        result = next(
            (item for item in entry.get("results", []) if item.get("url") == url),
            None,
        )
        if not result:
            continue

        health = result.get("health", {})
        total += 1
        last_checked = entry.get("timestamp", "-")
        status = health.get("status")
        last_status = str(status) if status is not None else "network error"
        last_latency = "{} ms".format(health.get("latency_ms")) if health.get("latency_ms") is not None else "-"

        if health.get("ok"):
            ok += 1
        if status == 504:
            http_504 += 1
            last_504 = entry.get("timestamp", "-")
        if health.get("error"):
            errors += 1

    return {
        "total": total,
        "ok": ok,
        "availability": format_percent(ok, total),
        "http_504": http_504,
        "errors": errors,
        "last_status": last_status,
        "last_latency": last_latency,
        "last_504": pagespeed.display_time(last_504, with_seconds=True) if last_504 != "-" else "-",
        "last_checked": pagespeed.display_time(last_checked, with_seconds=True) if last_checked != "-" else "-",
    }


def day_stats(history: List[Dict[str, Any]], urls: List[str]) -> Dict[str, Dict[str, Dict[str, int]]]:
    days: Dict[str, Dict[str, Dict[str, int]]] = {}

    for entry in history:
        try:
            day = pagespeed.parse_timestamp(entry["timestamp"]).date().isoformat()
        except Exception:
            day = entry.get("timestamp", "unknown")[:10] or "unknown"

        day_data = days.setdefault(day, {})

        for result in entry.get("results", []):
            url = result.get("url", "")
            data = day_data.setdefault(url, {"total": 0, "ok": 0, "http_504": 0, "errors": 0})
            health = result.get("health", {})
            data["total"] += 1
            if health.get("ok"):
                data["ok"] += 1
            if health.get("status") == 504:
                data["http_504"] += 1
            if health.get("error"):
                data["errors"] += 1

    return days


def batch_stats(batch: List[Dict[str, Any]], urls: List[str]) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}

    for url in urls:
        total = 0
        ok = 0
        http_504 = 0
        errors = 0
        last_status = "-"
        last_latency = "-"
        last_checked = "-"

        for entry in batch:
            result = next(
                (item for item in entry.get("results", []) if item.get("url") == url),
                None,
            )
            if not result:
                continue

            health = result.get("health", {})
            total += 1
            last_checked = entry.get("timestamp", "-")
            status = health.get("status")
            last_status = str(status) if status is not None else "network error"
            last_latency = "{} ms".format(health.get("latency_ms")) if health.get("latency_ms") is not None else "-"

            if health.get("ok"):
                ok += 1
            if status == 504:
                http_504 += 1
            if health.get("error"):
                errors += 1

        if total == 0:
            state = "UNKNOWN"
        elif ok == total:
            state = "OK"
        elif ok == 0:
            state = "DOWN"
        else:
            state = "DEGRADED"

        if state == "OK" and (http_504 > 0 or errors > 0):
            state = "DEGRADED"

        stats[url] = {
            "state": state,
            "total": total,
            "ok": ok,
            "availability": format_percent(ok, total),
            "http_504": http_504,
            "errors": errors,
            "last_status": last_status,
            "last_latency": last_latency,
            "last_checked": pagespeed.display_time(last_checked, with_seconds=True) if last_checked != "-" else "-",
        }

    return stats


def email_configured() -> bool:
    return EMAIL_ENABLED and SMTP_HOST and EMAIL_FROM and EMAIL_TO


def send_email(subject: str, body: str) -> bool:
    if not email_configured():
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = EMAIL_FROM
    message["To"] = ", ".join(EMAIL_TO)
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        if SMTP_USER or SMTP_PASSWORD:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(message)

    return True


def email_body(name: str, url: str, previous: str, current: str, stats: Dict[str, Any]) -> str:
    report_line = "\nDashboard: {}\n".format(HEALTH_REPORT_URL) if HEALTH_REPORT_URL else ""

    return (
        "{name} health status changed.\n\n"
        "URL: {url}\n"
        "Previous state: {previous}\n"
        "Current state: {current}\n"
        "Availability: {availability} over last {total} checks\n"
        "HTTP 504: {http_504}/{total}\n"
        "Network errors: {errors}\n"
        "Last status: {last_status}\n"
        "Latency: {last_latency}\n"
        "Checked: {last_checked}\n"
        "{report_line}"
    ).format(
        name=name,
        url=url,
        previous=previous or "UNKNOWN",
        current=current,
        availability=stats["availability"],
        total=stats["total"],
        http_504=stats["http_504"],
        errors=stats["errors"],
        last_status=stats["last_status"],
        last_latency=stats["last_latency"],
        last_checked=stats["last_checked"],
        report_line=report_line,
    )


def process_email_alerts(batch: List[Dict[str, Any]], now: dt.datetime) -> None:
    stats_by_url = batch_stats(batch, pagespeed.URLS)
    state = read_json(EMAIL_STATE_FILE, {"urls": {}})
    urls_state = state.setdefault("urls", {})

    for url in pagespeed.URLS:
        stats = stats_by_url[url]
        current = stats["state"]
        previous = urls_state.get(url, {}).get("state", "UNKNOWN")

        should_alert = current in ("DEGRADED", "DOWN") and previous != current
        should_recover = current == "OK" and previous in ("DEGRADED", "DOWN")

        if should_alert or should_recover:
            name = pagespeed.short_name(url)
            subject = "[Health] {} {}".format(
                name,
                "recovered" if should_recover else current.lower(),
            )
            body = email_body(name, url, previous, current, stats)

            try:
                sent = send_email(subject, body)
                if sent:
                    print("Email alert sent:", name, previous, "->", current)
                else:
                    print("Email alert skipped; SMTP is not configured:", name, previous, "->", current)
            except Exception as exc:
                print("Email alert failed:", name, exc)

        urls_state[url] = {
            "state": current,
            "updated_at": now.isoformat(timespec="seconds"),
            "availability": stats["availability"],
            "http_504": stats["http_504"],
            "errors": stats["errors"],
            "last_status": stats["last_status"],
        }

    state["updated_at"] = now.isoformat(timespec="seconds")
    write_json(EMAIL_STATE_FILE, state)


def build_health_html(run_label: str, history: List[Dict[str, Any]]) -> str:
    cards = []

    for url in pagespeed.URLS:
        stats = health_stats(history, url)
        cards.append(
            """
            <div class="card">
              <div class="k">{name}</div>
              <div class="small">{url}</div>
              <div class="v">{availability}</div>
              <div class="small">HTTP 504: {http_504}/{total} · Network errors: {errors}</div>
              <div class="small">Last status: {last_status} · {last_latency}</div>
              <div class="small">Last 504: {last_504}</div>
            </div>
            """.format(
                name=pagespeed.html_escape(pagespeed.short_name(url)),
                url=pagespeed.html_escape(url),
                availability=stats["availability"],
                http_504=stats["http_504"],
                total=stats["total"],
                errors=stats["errors"],
                last_status=pagespeed.html_escape(stats["last_status"]),
                last_latency=pagespeed.html_escape(stats["last_latency"]),
                last_504=pagespeed.html_escape(stats["last_504"]),
            )
        )

    sections = []
    daily = day_stats(history, pagespeed.URLS)

    for day in sorted(daily.keys(), reverse=True):
        rows = []
        for url in pagespeed.URLS:
            data = daily[day].get(url, {"total": 0, "ok": 0, "http_504": 0, "errors": 0})
            rows.append(
                """
                <tr>
                  <td>{name}</td>
                  <td>{availability}</td>
                  <td>{http_504}/{total}</td>
                  <td>{errors}</td>
                  <td>{total}</td>
                </tr>
                """.format(
                    name=pagespeed.html_escape(pagespeed.short_name(url)),
                    availability=format_percent(data["ok"], data["total"]),
                    http_504=data["http_504"],
                    total=data["total"],
                    errors=data["errors"],
                )
            )

        sections.append(
            """
            <section>
              <h2>{day}</h2>
              <table>
                <thead>
                  <tr>
                    <th>URL</th>
                    <th>Availability</th>
                    <th>HTTP 504</th>
                    <th>Network errors</th>
                    <th>Checks</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """.format(day=pagespeed.html_escape(day), rows="".join(rows))
        )

    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Health Monitor</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial; margin: 26px; color:#111; }}
    .meta {{ color:#555; margin-top:6px; }}
    .row {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:14px; }}
    .card {{ border:1px solid #eee; border-radius:16px; padding:14px; min-width:280px; flex:1; }}
    .k {{ color:#666; font-size:12px; font-weight:700; }}
    .v {{ font-size:28px; font-weight:800; margin-top:8px; }}
    .small {{ color:#666; font-size:13px; margin-top:8px; word-break:break-all; }}
    section {{ margin-top:26px; }}
    table {{ border-collapse:collapse; width:100%; margin-top:10px; }}
    th, td {{ border-bottom:1px solid #e8e8e8; padding:10px 8px; text-align:left; }}
    th {{ color:#555; font-size:12px; text-transform:uppercase; }}
    td:first-child {{ font-weight:700; }}
    a {{ color:#2563eb; }}
  </style>
</head>
<body>
  <h1 style="margin:0;">Health Monitor</h1>
  <div class="meta"><b>Run:</b> {run} · <b>Window:</b> last {days} days · <b>Checks:</b> {points}</div>
  <p class="meta"><a href="{pagespeed_url}">PageSpeed report</a> · <a href="{full_pagespeed_url}">Full PageSpeed report</a></p>

  <div class="row">
    {cards}
  </div>

  {sections}
</body>
</html>
""".format(
        run=pagespeed.html_escape(run_label),
        days=HISTORY_DAYS,
        points=len(history),
        cards="".join(cards),
        sections="".join(sections),
        pagespeed_url=pagespeed.html_escape(PAGESPEED_REPORT_URL),
        full_pagespeed_url=pagespeed.html_escape(FULL_PAGESPEED_REPORT_URL),
    )


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    batch = []

    for index in range(CHECKS_PER_RUN):
        now = dt.datetime.now().astimezone()
        entry = health_entry(now)
        pagespeed.append_jsonl(HEALTH_HISTORY_FILE, entry)
        batch.append(entry)

        if index < CHECKS_PER_RUN - 1:
            time.sleep(CHECK_INTERVAL_SECONDS)

    now = dt.datetime.now().astimezone()
    retained = pagespeed.filter_recent_history(
        pagespeed.tail_jsonl(HEALTH_HISTORY_FILE, RETENTION_POINTS * 2),
        now,
        RETENTION_DAYS,
    )
    pagespeed.rewrite_history(HEALTH_HISTORY_FILE, retained)

    recent = pagespeed.filter_recent_history(retained, now, HISTORY_DAYS)
    run_label = now.astimezone(pagespeed.DISPLAY_ZONE).strftime("%Y-%m-%d %H:%M %Z")

    with open(HEALTH_REPORT_FILE, "w", encoding="utf-8") as file:
        file.write(build_health_html(run_label, recent))

    print("Done. Health checks in report:", len(recent))


if __name__ == "__main__":
    main()
