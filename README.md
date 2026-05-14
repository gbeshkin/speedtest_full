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
- `DISPLAY_TZ` - display timezone, default `Europe/Berlin`.
- `PORT` - HTTP port, Railway sets this automatically.

The worker checks all URLs continuously and serves:

- `/` or `/health.html` - live health dashboard.
- `/health.json` - live status JSON.

For GitHub Pages links to point at Railway, set `RAILWAY_HEALTH_URL` in the
GitHub Actions environment used by the PageSpeed workflow.
