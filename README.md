# speedtest_full

PageSpeed and health monitoring for KN websites.

## Railway health worker

PageSpeed reports can stay on GitHub Actions. For more reliable frequent health
checks, deploy the worker process to Railway.

Railway start command:

```bash
python health_worker.py
```

Environment variables:

- `HEALTH_INTERVAL_SECONDS` - check interval, default `60`.
- `HEALTH_ALERT_WINDOW_CHECKS` - recent checks used for email alert state, default `3`.
- `DISPLAY_TZ` - display timezone, default `Europe/Berlin`.
- `PORT` - HTTP port, Railway sets this automatically.
- `EMAIL_ENABLED` - set to `true` to enable SMTP email alerts.
- `SMTP_HOST` - SMTP host, for Microsoft 365 usually `smtp.office365.com`.
- `SMTP_PORT` - SMTP port, for Microsoft 365 usually `587`.
- `SMTP_USER` - SMTP username.
- `SMTP_PASSWORD` - SMTP password/app password/service mailbox secret.
- `EMAIL_FROM` - sender email address.
- `EMAIL_TO` - comma-separated recipient list.
- `HEALTH_REPORT_URL` - optional public Railway health dashboard URL included in emails.

The worker checks all URLs continuously and serves:

- `/` or `/health.html` - live health dashboard.
- `/health.json` - live status JSON.

For GitHub Pages links to point at Railway, set `RAILWAY_HEALTH_URL` in the
GitHub Actions environment used by the PageSpeed workflow.

Email alerts are sent only when state changes, for example `OK -> DEGRADED`,
`OK -> DOWN`, or recovery back to `OK`.
