# gh-ops

A reusable, deterministic GitHub operations and intelligence platform.

> "A personal GitHub operations layer running on GitHub Actions."

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python scripts/run_local.py daily
```

## Project Structure

```
gh-ops/
├── src/
│   ├── core/           # Config, state, events, errors, rate limiting
│   ├── github/         # API client, auth, collectors
│   ├── intelligence/   # OSS hunter, release/security radars
│   ├── developer/      # Personal activity, statistics, reports
│   ├── monitors/       # Repository, CI, release monitoring
│   ├── notifications/  # Telegram adapter
│   └── utils/          # Logging, text, time helpers
├── config/             # YAML configuration files
├── data/               # Runtime state (gitignored)
├── tests/              # Test suite
└── scripts/            # Local development scripts
```

## Runtime Dependencies

- Python 3.10+
- `requests` — GitHub API HTTP client
- `pyyaml` — Configuration file parsing

## License

MIT
