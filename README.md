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
- `HEALTH_OUT_DIR` - optional directory for health history files. If unset, the worker uses Railway's `RAILWAY_VOLUME_MOUNT_PATH` when a volume is attached, otherwise `reports`.
- `DISPLAY_TZ` - display timezone, default `Europe/Berlin`.
- `PORT` - HTTP port, Railway sets this automatically.
- `EMAIL_ENABLED` - set to `true` to enable email alerts.
- `EMAIL_PROVIDER` - `resend` for Resend HTTP API or `smtp` for SMTP, default `smtp`.
- `RESEND_API_KEY` - Resend API key when `EMAIL_PROVIDER=resend`.
- `SMTP_HOST` - SMTP host, for Microsoft 365 usually `smtp.office365.com`.
- `SMTP_PORT` - SMTP port, for Microsoft 365 usually `587`.
- `SMTP_FORCE_IPV4` - set to `true` by default to avoid IPv6 routing issues in hosted containers.
- `SMTP_USER` - SMTP username.
- `SMTP_PASSWORD` - SMTP password/app password/service mailbox secret.
- `EMAIL_FROM` - sender email address.
- `EMAIL_TO` - comma-separated recipient list.
- `HEALTH_REPORT_URL` - optional public Railway health dashboard URL included in emails.
- `EMAIL_TEST_ON_START` - set to `true` to send one test email when the worker starts.
- `HEALTH_FORCE_STATUS` - optional test-only forced status for the first URL, for example `502`.

The worker checks all URLs continuously and serves:

- `/` or `/health.html` - live health dashboard.
- `/health.json` - live status JSON.

For GitHub Pages links to point at Railway, set `RAILWAY_HEALTH_URL` in the
GitHub Actions environment used by the PageSpeed workflow.

Email alerts are sent only when state changes, for example `OK -> DEGRADED`,
`OK -> DOWN`, or recovery back to `OK`.

To keep health history across Railway restarts and deploys, attach a Railway
Volume to the worker service. Railway exposes its mount path as
`RAILWAY_VOLUME_MOUNT_PATH`, and the worker will store `health-history.jsonl`
and `health-email-state.json` there automatically. You can override this with
`HEALTH_OUT_DIR`.

To test email delivery without waiting for a real outage, temporarily set:

```bash
EMAIL_TEST_ON_START=true
HEALTH_FORCE_STATUS=502
```

After the test, remove `HEALTH_FORCE_STATUS` and set `EMAIL_TEST_ON_START=false`
so the worker stops simulating failures.
