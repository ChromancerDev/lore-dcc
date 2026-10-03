# lore-dcc

Lore version control inside DCC applications, in the spirit of SideFX Labs `P4Houdini`. Lock, stage, commit and push from inside the app, with Lore's own words and rules.

**Status:** in development. No release yet. v1 targets Houdini 22 on Windows. The code is portable, but Linux is untested.

## Requirements

- Houdini 22
- Lore 0.9.0 or newer, with `lore` on `PATH`
- A Lore working copy. The plugin finds it by walking up from the open file to `.lore/`. It reads the remote and identity from `.lore/config.toml` and needs no other setup.

Sign in with `lore login` in a terminal. The plugin never handles credentials.

## Install

Install steps arrive with the first release. Each release ships one zip per DCC, starting with `lore-dcc-houdini-<version>.zip`.

## Per-project settings

An optional `lore-dcc.json` at the root of a Lore working copy changes which file types the plugin locks. Commit it like any project file.

```json
{
  "version": 1,
  "lockable": { "add": [".sim"], "remove": [".obj"] }
}
```

The default lockable types are in [`python/lore_dcc/settings.py`](python/lore_dcc/settings.py).

## Design

[`docs/design/v1-houdini.md`](docs/design/v1-houdini.md) holds the v1 feature map, the lock rules and the open checks.

## Development

```
python -m pip install pytest ruff
python -m pytest -q
ruff check .
```

The core under `python/lore_dcc/` is pure Python 3.13 with no third-party imports. Tests run without Houdini and without a Lore server.

## License

MIT. Lore is developed by Epic Games. Houdini is developed by SideFX. This project is not affiliated with either.
