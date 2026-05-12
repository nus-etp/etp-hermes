# Installing Packages Into the Hermes Venv

## Problem

The Hermes-installed venv at `~/.hermes/hermes-agent/venv/` ships **without pip** — it's stripped for end-user install size. This means:

- `pip install python-telegram-bot` in the system Python does nothing for the gateway
- `uvx --python ~/.hermes/hermes-agent/venv/bin/python pip install <pkg>` installs into a **temporary uvx ephemeral environment**, not the target venv
- The gateway logs show: `WARNING gateway.run: Telegram: python-telegram-bot not installed` followed by `WARNING gateway.run: No adapter available for telegram`

## Fix

```bash
uv pip install <package-name> --python ~/.hermes/hermes-agent/venv/bin/python
```

Then restart the gateway:
```bash
hermes gateway restart
```

Verify:
```bash
~/.hermes/hermes-agent/venv/bin/python -c "import <module>; print('<module>', <module>.__version__)"
tail -5 ~/.hermes/logs/gateway.log   # Should show "✓ telegram connected" etc.
```

## Why `uvx --python ... pip install` Doesn't Work

`uvx` creates an ephemeral isolated environment for each run, installs the tool there, executes it, then discards it. When you run `uvx --python /path/to/venv pip install foo`, uvx:

1. Creates a temp environment
2. Installs `pip` into that temp environment  
3. Passes `--python /path/to/venv` to pip's argument list, which pip ignores for package installation location

The result: the package goes into uvx's temp env, not the target venv. The venv remains unchanged.

## Affected Platforms

All platforms where the Hermes venv exists (Linux, macOS, Windows). Gateway adapter dependencies affected include:

| Platform | Package | Env var |
|----------|---------|---------|
| Telegram | `python-telegram-bot` | `TELEGRAM_BOT_TOKEN` |
| Discord | `discord.py` | `DISCORD_BOT_TOKEN` |
| Slack | `slack-sdk` | `SLACK_BOT_TOKEN` |
| WhatsApp | `twilio` | `TWILIO_*` |
| Signal | `signal-cli` | n/a (binary) |
| Matrix | `matrix-nio` | `MATRIX_*` |

## Diagnostic Flow

```
1. User reports "telegram bot has no access to messages"
2. Check gateway logs:
   grep -i "telegram" ~/.hermes/logs/gateway.log
3. If "python-telegram-bot not installed" → install into venv
4. If "No adapter available for telegram" → same cause
5. If "No user allowlists configured" → check TELEGRAM_ALLOWED_USERS in .env
6. After install + restart, verify "✓ telegram connected" in logs
```
