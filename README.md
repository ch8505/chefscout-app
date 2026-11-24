# ChefScout

A personal cooking agent in Hebrew. Ask for a dinner idea and get up to three **real** recipes from the web, with
links, filtered by your allergies and the foods you never eat, matched against what you have at home, with
substitutes (and amounts) for anything missing or against your kitchen rules.

> Status: MVP (stages 0-6 done). See [PLAN.md](PLAN.md) for the plan and progress.

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
Copy-Item .env.example .env      # then put your Anthropic API key in .env
```

Set a monthly spend limit in the Claude Console.

## Run

```powershell
python -m chefscout              # from the project folder, then open http://127.0.0.1:8000
```

## How it works

1. The model plans what dishes fit (request + profile + pantry) and searches the web in Hebrew.
2. `read_recipes` opens the pages and reads the recipe's structured data (schema.org JSON-LD).
3. The code, not the model, blocks recipes with an allergen or a never-eat food (Hebrew-aware matching),
   flags kitchen-rule ingredients, and lists what is missing compared with the pantry.
4. `show_recipes` picks up to 3 recipes; the page shows them as cards built from the data, never from the
   model's text. Links that did not come from a page that was read are removed from the answer.
5. `suggest_substitute` gives verified substitutes with scaled amounts, filtered by the profile.

Memory: the **profile** (allergies, never-eat, diet, kitchen rules, likes, cooked recipes with rating) is kept
for all conversations; the **pantry** belongs to one conversation. When it is unclear whether something is
lasting or only for now, the agent asks.

## Architecture

```
src/chefscout/
  core/          the agent harness (domain-free): agent, loop, tools registry, history,
                 compaction, caching, usage, events, llm client
  memory/        profile (lasting) and pantry (per conversation)
  recipes/       fetch (safe), jsonld, matching (filters), substitutions, store, link guard
  tools/         the tools the model can call
  server/        FastAPI: SSE chat, profile, pantry, cooked
  web/           the page (HTML/CSS/JS, RTL)
  data/          food terms and substitution tables
  bootstrap.py   wires everything together; new features plug in here
  prompts.py     the system prompt
  config.py      typed settings from .env
```

A feature is a `Tool` registered in `bootstrap.build_registry()` (plus, if needed, a context provider or an
output filter). The core does not change.

## Develop

```powershell
pytest                           # offline tests: fake model, fake web, no key, no cost
ruff check .                     # lint
ruff format .                    # format
```

## Configuration

All settings live in `.env` (see `.env.example`), prefix `CHEFSCOUT_`. Defaults: `claude-haiku-5-5` at low effort,
at most 3 web searches per request, server on 127.0.0.1 only.
