import os
import time
import random
import json
import datetime as dt
from typing import Dict, Any, List

import requests
from requests.exceptions import ReadTimeout, ConnectionError, Timeout

# =========================
# CONFIG
# =========================

URLS = [
    "https://public.websites-dev.eu-central-1.kncloud.aws.int.kn/",
    "https://public.websites-qa.eu-central-1.kncloud.aws.int.kn/",
    "https://www.kuehne-nagel.com",
]

API_KEY = os.environ.get("PSI_API_KEY", "")
API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

OUT_DIR = "reports"
HISTORY_FILE = os.path.join(OUT_DIR, "history.jsonl")

# 3 days history if job runs every 5 minutes
CHART_POINTS = 864
HISTORY_DAYS = 3

# Performance only
CATEGORIES = ["performance"]

# Dots every hour (12 x 5-minute points)
DOT_STEP = 12

# Chart settings
CHART_W = 920
CHART_H = 260
CHART_PAD_L = 44
CHART_PAD_R = 16
CHART_PAD_T = 18
CHART_PAD_B = 56

SESSION = requests.Session()


# =========================
# HELPERS
# =========================

def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def short_name(url: str) -> str:
    if "websites-dev" in url:
        return "DEV"
    if "websites-qa" in url:
        return "QA"
    if "websites-prod" in url:
        return "PROD"
    if "kuehne-nagel.com" in url:
        return "PROD"
    return url


# =========================
# PSI REQUEST
# =========================

def fetch(url: str, strategy: str, max_attempts: int = 10) -> Dict[str, Any]:
    params = {
        "url": url,
        "strategy": strategy,
        "category": CATEGORIES,
    }
    if API_KEY:
        params["key"] = API_KEY

    timeout = (10, 300)
    retry_http = {429, 500, 502, 503, 504}
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = SESSION.get(API, params=params, timeout=timeout)

            if response.status_code == 200:
                return response.json()

            if response.status_code in retry_http:
                wait = min(120, (2 ** (attempt - 1))) + random.uniform(0, 2.0)
                print(
                    "[{}][{}] HTTP {} -> retry {}/{} in {:.1f}s".format(
                        strategy, url, response.status_code, attempt, max_attempts, wait
                    )
                )
                time.sleep(wait)
                last_err = "HTTP {}".format(response.status_code)
                continue

            try:
                details = response.json()
            except Exception:
                details = (response.text or "")[:800]

            raise RuntimeError(
                "[{}][{}] PSI error {}: {}".format(
                    strategy, url, response.status_code, details
                )
            )

        except (ReadTimeout, Timeout, ConnectionError) as exc:
            wait = min(120, (2 ** (attempt - 1))) + random.uniform(0, 2.0)
            print(
                "[{}][{}] timeout/network {} -> retry {}/{} in {:.1f}s".format(
                    strategy, url, exc, attempt, max_attempts, wait
                )
            )
            time.sleep(wait)
            last_err = str(exc)

    raise RuntimeError(
        "[{}][{}] PSI failed after {} attempts. Last error: {}".format(
            strategy, url, max_attempts, last_err
        )
    )


def lh_score(data: Dict[str, Any], category: str) -> int:
    return int(round(data["lighthouseResult"]["categories"][category]["score"] * 100))


# =========================
# JSONL HISTORY
# =========================

def append_jsonl(path: str, obj: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(obj, ensure_ascii=False) + "\n")


def tail_jsonl(path: str, n: int) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []

    block_size = 64 * 1024
    data = b""
    lines: List[bytes] = []

    with open(path, "rb") as file:
        file.seek(0, os.SEEK_END)
        pos = file.tell()

        while pos > 0 and len(lines) <= n:
            read_size = block_size if pos >= block_size else pos
            pos -= read_size
            file.seek(pos)
            data = file.read(read_size) + data
            lines = data.splitlines()

    last_lines = lines[-n:] if len(lines) >= n else lines
    out: List[Dict[str, Any]] = []

    for line in last_lines:
        try:
            out.append(json.loads(line.decode("utf-8")))
        except Exception:
            pass

    return out


def rewrite_last_n_jsonl(path: str, n: int) -> None:
    items = tail_jsonl(path, n)
    tmp_path = path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    os.replace(tmp_path, path)


def parse_timestamp(value: str) -> dt.datetime:
    timestamp = dt.datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return timestamp


def filter_recent_history(
    history: List[Dict[str, Any]], now: dt.datetime, days: int
) -> List[Dict[str, Any]]:
    cutoff = now - dt.timedelta(days=days)
    recent = []

    for entry in history:
        try:
            if parse_timestamp(entry["timestamp"]) >= cutoff:
                recent.append(entry)
        except Exception:
            pass

    return recent


def rewrite_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp_path = path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    os.replace(tmp_path, path)


def average_scores(history: List[Dict[str, Any]], urls: List[str]) -> Dict[str, Dict[str, int]]:
    averages: Dict[str, Dict[str, int]] = {}

    for url in urls:
        mobile: List[int] = []
        desktop: List[int] = []

        for entry in history:
            result = next(
                (
                    item
                    for item in entry.get("results", [])
                    if item.get("url") == url and "error" not in item
                ),
                None,
            )
            if not result:
                continue

            mobile.append(int(result["mobile"]["performance"]))
            desktop.append(int(result["desktop"]["performance"]))

        if mobile and desktop:
            averages[url] = {
                "mobile": int(round(sum(mobile) / float(len(mobile)))),
                "desktop": int(round(sum(desktop) / float(len(desktop)))),
                "points": min(len(mobile), len(desktop)),
            }

    return averages


# =========================
# HTML BUILDERS
# =========================

def build_error_html(run_label: str, message: str) -> str:
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PageSpeed — error</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial; margin: 26px; }}
    pre {{ background:#f6f6f6; padding:12px; border-radius:12px; overflow:auto; }}
  </style>
</head>
<body>
  <h1>PageSpeed — temporary error</h1>
  <p><b>Run:</b> {run}</p>
  <pre>{message}</pre>
</body>
</html>
""".format(run=run_label, message=html_escape(message))


def build_chart(history: List[Dict[str, Any]], urls: List[str]) -> str:
    if len(history) < 2:
        return "<div class='meta'>Not enough history for chart yet. Current points: {}</div>".format(len(history))

    charts = []

    for url in urls:
        labels: List[str] = []
        mobile: List[int] = []
        desktop: List[int] = []

        for entry in history:
            timestamp = entry.get("timestamp", "")
            labels.append(timestamp[5:16].replace("T", " ") if timestamp else entry.get("time", ""))

            results = entry.get("results", [])
            result = next((r for r in results if r.get("url") == url), None)

            if result and "error" not in result:
                mobile.append(int(result["mobile"]["performance"]))
                desktop.append(int(result["desktop"]["performance"]))
            else:
                if mobile:
                    mobile.append(mobile[-1])
                    desktop.append(desktop[-1])
                else:
                    mobile.append(0)
                    desktop.append(0)

        n = len(labels)
        if n < 2:
            charts.append(
                "<h2>{}</h2><div class='meta'>Not enough history for this environment yet.</div>".format(
                    html_escape(short_name(url))
                )
            )
            continue

        minv = 0
        maxv = 100

        plot_w = CHART_W - CHART_PAD_L - CHART_PAD_R
        plot_h = CHART_H - CHART_PAD_T - CHART_PAD_B

        def x(i: int) -> float:
            return CHART_PAD_L + (plot_w * i / float(n - 1))

        def y(v: int) -> float:
            ratio = (v - minv) / float(maxv - minv) if maxv != minv else 0.5
            return CHART_PAD_T + (plot_h * (1.0 - ratio))

        def path(series: List[int]) -> str:
            pts = ["{:.2f},{:.2f}".format(x(i), y(v)) for i, v in enumerate(series)]
            return "M " + " L ".join(pts)

        def dots(series: List[int], cls: str) -> str:
            out = []
            last_i = len(series) - 1
            for i, v in enumerate(series):
                if (i % DOT_STEP != 0) and (i != last_i):
                    continue
                out.append(
                    "<circle cx='{:.2f}' cy='{:.2f}' r='2.6' class='{}'/>".format(
                        x(i), y(v), cls
                    )
                )
            return "".join(out)

        ticks = [minv, int((minv + maxv) / 2), maxv]
        ygrid = []
        for tick in ticks:
            yy = y(tick)
            ygrid.append(
                "<line x1='{l}' y1='{y:.2f}' x2='{r}' y2='{y:.2f}' class='svg-grid'/>".format(
                    l=CHART_PAD_L, r=CHART_PAD_L + plot_w, y=yy
                )
            )
            ygrid.append(
                "<text x='{x}' y='{y:.2f}' text-anchor='end' class='svg-y'>{tick}</text>".format(
                    x=CHART_PAD_L - 8, y=yy + 4, tick=tick
                )
            )

        label_step = 24 if n > 200 else 12
        xlabels = []
        for i, label in enumerate(labels):
            if (i % label_step != 0) and (i != n - 1):
                continue
            xlabels.append(
                "<text x='{:.2f}' y='{}' text-anchor='middle' class='svg-x'>{}</text>".format(
                    x(i), CHART_PAD_T + plot_h + 32, html_escape(label)
                )
            )

        chart = """
        <div class="env-chart">
          <h2>{env}</h2>
          <div class="legend">
            <span class="leg"><span class="sw sw-m"></span> Mobile: <b>{m_last}</b></span>
            <span class="leg"><span class="sw sw-d"></span> Desktop: <b>{d_last}</b></span>
          </div>

          <svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">
            <rect x="0" y="0" width="{w}" height="{h}" rx="16" class="svg-bg"/>
            {ygrid}
            <line x1="{l}" y1="{t}" x2="{l}" y2="{b}" class="svg-axis"/>
            <line x1="{l}" y1="{b}" x2="{r}" y2="{b}" class="svg-axis"/>

            <path d="{mp}" class="svg-line-m"/>
            <path d="{dp}" class="svg-line-d"/>

            {mdots}
            {ddots}

            {xlabels}
          </svg>
        </div>
        """.format(
            env=html_escape(short_name(url)),
            m_last=mobile[-1],
            d_last=desktop[-1],
            w=CHART_W,
            h=CHART_H,
            ygrid="".join(ygrid),
            l=CHART_PAD_L,
            r=CHART_PAD_L + plot_w,
            t=CHART_PAD_T,
            b=CHART_PAD_T + plot_h,
            mp=path(mobile),
            dp=path(desktop),
            mdots=dots(mobile, "svg-dot-m"),
            ddots=dots(desktop, "svg-dot-d"),
            xlabels="".join(xlabels),
        )

        charts.append(chart)

    return "".join(charts)


def build_html(run_label: str, results: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> str:
    cards = []
    averages = average_scores(history, URLS)

    for item in results:
        avg = averages.get(item["url"])
        if "error" in item:
            cards.append(
                """
                <div class="card">
                  <div class="k">{name}</div>
                  <div class="err">Error</div>
                  <div class="small">{err}</div>
                </div>
                """.format(
                    name=html_escape(short_name(item["url"])),
                    err=html_escape(item["error"]),
                )
            )
        else:
            mobile_score = avg["mobile"] if avg else item["mobile"]["performance"]
            desktop_score = avg["desktop"] if avg else item["desktop"]["performance"]
            points = avg["points"] if avg else 1
            cards.append(
                """
                <div class="card">
                  <div class="k">{name}</div>
                  <div class="small">{url}</div>
                  <div class="v">M {m} / D {d}</div>
                  <div class="small">3-day average · {points} points</div>
                </div>
                """.format(
                    name=html_escape(short_name(item["url"])),
                    url=html_escape(item["url"]),
                    m=mobile_score,
                    d=desktop_score,
                    points=points,
                )
            )

    chart = build_chart(history, URLS)

    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PageSpeed — Performance</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial; margin: 26px; }}
    .meta {{ color:#555; margin-top:6px; }}
    .row {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:14px; }}
    .card {{ border:1px solid #eee; border-radius:16px; padding:14px; min-width:280px; flex:1; }}
    .k {{ color:#666; font-size:12px; font-weight:700; }}
    .v {{ font-size:28px; font-weight:800; margin-top:8px; }}
    .small {{ color:#666; font-size:13px; margin-top:8px; word-break:break-all; }}
    .err {{ color:#b00020; font-weight:700; margin-top:8px; }}

    .chart {{ margin-top:26px; }}
    .env-chart {{ margin-top:24px; }}
    .legend {{ display:flex; gap:14px; flex-wrap:wrap; margin:10px 0 8px; color:#444; }}
    .leg {{ display:flex; align-items:center; gap:8px; }}
    .sw {{ display:inline-block; width:14px; height:4px; border-radius:999px; }}

    .svg-bg {{ fill:#fafafa; stroke:#e8e8e8; }}
    .svg-grid {{ stroke:#e9e9e9; stroke-width:1; }}
    .svg-axis {{ stroke:#d7d7d7; stroke-width:1.2; }}
    .svg-line-m {{ fill:none; stroke:#111; stroke-width:2.4; }}
    .svg-line-d {{ fill:none; stroke:#2563eb; stroke-width:2.4; stroke-dasharray:6 5; }}
    .svg-dot-m {{ fill:#111; }}
    .svg-dot-d {{ fill:#2563eb; }}
    .svg-x {{ font-size:11px; fill:#666; }}
    .svg-y {{ font-size:11px; fill:#666; }}

    .sw-m {{ background:#111; }}
    .sw-d {{ background:#2563eb; }}
  </style>
</head>
<body>
  <h1 style="margin:0;">PageSpeed — Performance (5 min)</h1>
  <div class="meta"><b>Run:</b> {run} · <b>URLs:</b> {count} · <b>History points:</b> {history_len}</div>

  <div class="row">
    {cards}
  </div>

  <div class="chart">
    <h2>3-day trend (linear 0–100 scale)</h2>
    {chart}
  </div>

  <p class="meta">Full daily report: <a href="full.html">full.html</a></p>
</body>
</html>
""".format(
        run=run_label,
        count=len(results),
        history_len=len(history),
        cards="".join(cards),
        chart=chart,
    )


def score_stats(values: List[int]) -> str:
    if not values:
        return "—"

    return "{avg} / {minv} / {maxv}".format(
        avg=int(round(sum(values) / float(len(values)))),
        minv=min(values),
        maxv=max(values),
    )


def build_full_html(run_label: str, history: List[Dict[str, Any]], urls: List[str]) -> str:
    by_day: Dict[str, Dict[str, Dict[str, List[int]]]] = {}

    for entry in history:
        try:
            day = parse_timestamp(entry["timestamp"]).date().isoformat()
        except Exception:
            day = entry.get("timestamp", "unknown")[:10] or "unknown"

        day_data = by_day.setdefault(day, {})

        for result in entry.get("results", []):
            if "error" in result:
                continue

            url = result.get("url", "")
            url_data = day_data.setdefault(url, {"mobile": [], "desktop": []})
            url_data["mobile"].append(int(result["mobile"]["performance"]))
            url_data["desktop"].append(int(result["desktop"]["performance"]))

    sections = []

    for day in sorted(by_day.keys(), reverse=True):
        rows = []
        for url in urls:
            values = by_day[day].get(url, {"mobile": [], "desktop": []})
            rows.append(
                """
                <tr>
                  <td>{name}</td>
                  <td>{runs}</td>
                  <td>{mobile}</td>
                  <td>{desktop}</td>
                </tr>
                """.format(
                    name=html_escape(short_name(url)),
                    runs=max(len(values["mobile"]), len(values["desktop"])),
                    mobile=score_stats(values["mobile"]),
                    desktop=score_stats(values["desktop"]),
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
                    <th>Runs</th>
                    <th>Mobile avg / min / max</th>
                    <th>Desktop avg / min / max</th>
                  </tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </section>
            """.format(day=html_escape(day), rows="".join(rows))
        )

    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>PageSpeed — Full Daily Report</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Roboto, Arial; margin: 26px; color:#111; }}
    .meta {{ color:#555; margin-top:6px; }}
    section {{ margin-top:26px; }}
    table {{ border-collapse:collapse; width:100%; margin-top:10px; }}
    th, td {{ border-bottom:1px solid #e8e8e8; padding:10px 8px; text-align:left; }}
    th {{ color:#555; font-size:12px; text-transform:uppercase; }}
    td:first-child {{ font-weight:700; }}
    a {{ color:#2563eb; }}
  </style>
</head>
<body>
  <h1 style="margin:0;">PageSpeed — Full Daily Report</h1>
  <div class="meta"><b>Run:</b> {run} · <b>History window:</b> last {days} days · <b>Points:</b> {points}</div>
  <p class="meta"><a href="index.html">Back to latest report</a></p>
  {sections}
</body>
</html>
""".format(
        run=run_label,
        days=HISTORY_DAYS,
        points=len(history),
        sections="".join(sections),
    )


# =========================
# MAIN
# =========================

def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    now = dt.datetime.now().astimezone()
    run_label = now.strftime("%Y-%m-%d %H:%M %z")

    all_results: List[Dict[str, Any]] = []

    for url in URLS:
        print("Fetching PageSpeed for:", url)

        try:
            mobile_raw = fetch(url, "mobile")
            time.sleep(2)
            desktop_raw = fetch(url, "desktop")

            all_results.append(
                {
                    "timestamp": now.isoformat(timespec="minutes"),
                    "time": now.strftime("%H:%M"),
                    "url": url,
                    "mobile": {"performance": lh_score(mobile_raw, "performance")},
                    "desktop": {"performance": lh_score(desktop_raw, "performance")},
                }
            )
        except Exception as exc:
            all_results.append(
                {
                    "timestamp": now.isoformat(timespec="minutes"),
                    "time": now.strftime("%H:%M"),
                    "url": url,
                    "error": str(exc),
                }
            )

    history_entry = {
        "timestamp": now.isoformat(timespec="minutes"),
        "time": now.strftime("%H:%M"),
        "results": all_results,
    }

    append_jsonl(HISTORY_FILE, history_entry)
    history = filter_recent_history(
        tail_jsonl(HISTORY_FILE, CHART_POINTS * 4),
        now,
        HISTORY_DAYS,
    )
    rewrite_history(HISTORY_FILE, history)

    if all("error" in item for item in all_results):
        html = build_error_html(
            run_label,
            "All monitored URLs failed during this run. Check GitHub Actions logs.",
        )
    else:
        html = build_html(run_label, all_results, history)

    with open(os.path.join(OUT_DIR, "latest.html"), "w", encoding="utf-8") as file:
        file.write(html)

    with open(os.path.join(OUT_DIR, "full.html"), "w", encoding="utf-8") as file:
        file.write(build_full_html(run_label, history, URLS))

    print("✅ Done. History points:", len(history))


if __name__ == "__main__":
    main()
