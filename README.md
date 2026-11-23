# ChefScout

A personal cooking agent. Ask for a dinner idea and get three real Hebrew recipes from the web, with links,
filtered by your allergies and the foods you never eat, matched against what you have at home, with
substitutes for anything missing.

> Status: early development (stage 1: agent core, no recipe tools yet).

## Architecture

```
src/chefscout/
  core/          the agent harness (domain-free)
    agent.py       one user turn: history -> compaction -> context -> loop -> save
    loop.py        model calls until it stops asking for tools (tool_use / pause_turn)
    tools.py       tool registry: schemas, dispatch, errors returned as is_error
    history.py     append-only JSONL conversations per user
    compaction.py  summarizes long conversations
    caching.py     prompt-cache breakpoints
    usage.py       tokens and cost per call and per turn
    events.py      progress events (streamed to the browser later)
    llm.py         the Anthropic client (the only place that reads the key)
  bootstrap.py   wires everything together; new features plug in here
  prompts.py     the system prompt
  config.py      typed settings from .env
  server/        FastAPI app
```

A feature is a `Tool` registered in `bootstrap.build_registry()` and, if it needs context every turn,
a context provider. The core does not change.

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
