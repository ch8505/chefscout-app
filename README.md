# ChefScout

A personal cooking agent. Ask for a dinner idea and get three real Hebrew recipes from the web, with links,
filtered by your allergies and the foods you never eat, matched against what you have at home, with
substitutes for anything missing.

> Status: early development (stage 0, project skeleton).

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
Copy-Item .env.example .env      # then put your Anthropic API key in .env
```

## Run

```powershell
python -m chefscout              # http://127.0.0.1:8000
```

## Develop

```powershell
pytest                           # offline tests, no API key needed
ruff check .                     # lint
ruff format .                    # format
```

## Configuration

All settings live in `.env` (see `.env.example`), prefix `CHEFSCOUT_`. The default model is
`claude-haiku-5-5` at low effort, the cheapest setup. Set a monthly spend limit in the Claude Console.
