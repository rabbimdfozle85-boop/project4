# project4

FHONE — a lightweight personal AI agent that runs on a low-RAM Android phone.

Pure Python standard library. No third-party packages. No build step.

## Quick start

```sh
git clone https://github.com/rabbimdfozle85-boop/project4.git
cd project4/fhone
chmod +x fhone-launcher.sh
./fhone-launcher.sh install
./fhone-launcher.sh
```

See [fhone/README.md](fhone/README.md) for the full install guide, commands, and
configuration.

## What is here

| Path | Contents |
|---|---|
| `fhone/fhone/` | The agent — 18 core modules, 3 surfaces (CLI, web portal, Telegram) |
| `fhone/skills/` | 37 bundled skills |
| `fhone/tests/` | 191 assertions, all offline |
| `fhone/fhone-launcher.sh` | Standalone launcher — works on Termux, Linux, macOS |

## Measured

- 29 MB resident (supervisor with portal and scheduler running)
- 27.9 MB peak on a full streamed turn
- 191 offline test assertions passing
