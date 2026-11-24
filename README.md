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

**Ideas first, recipes after you choose.**

1. The model decides what kind of request this is:
   - a whole meal: 2 different meals;
   - dish ideas, or what to make from the pantry: 3 dishes;
   - something vague: 3 directions;
   - one specific dish: search right away.
2. `propose_ideas` saves the ideas with stable ids (M1, M1.D2). It runs no web search and adds no links. The code checks every idea:
   - the profile;
   - kashrut (a meal never mixes meat and dairy);
   - the time asked for;
   - variety (a different main ingredient in each meal);
   - recent explicit rejections.
3. The user chooses a meal or a dish (`select_dishes`), swaps one dish (`replace_dish`), or asks for more options for one dish (`more_options`). Everything else in the plan keeps its ids and recipes.
4. For each chosen dish the model searches in Hebrew. `read_recipes` opens only pages that this conversation's web search returned, and reads the recipe's structured data (schema.org JSON-LD).
5. The code, not the model, does all the checks:
   - blocks recipes with an allergen, a never-eat food, meat with dairy, or a dish that clashes with the rest of the meal;
   - flags kitchen-rule ingredients;
   - lists what is missing compared with the pantry;
   - marks a recipe that takes much longer than asked. Such a recipe is shown only as an alternative.
6. `show_recipes` shows the cards. They are built from the page data, never from the model's text. Any link that did not come from a page that was read is removed from the answer.
7. `suggest_substitute` gives verified substitutes with scaled amounts, filtered by the profile.

Memory:
- The **profile** is kept for all conversations: allergies, never-eat foods, diet, kitchen rules, likes, and cooked recipes with a rating.
- The **pantry** belongs to one conversation.
- When it is unclear whether something is lasting or only for now, the agent asks.
- The **suggestion history** separates "not chosen" from "rejected":
  - suggested, shown or swapped: only a variety hint;
  - saved: never blocked;
  - explicitly rejected: not suggested again for 60 days.

## Architecture

```
src/chefscout/
  core/          the agent harness (domain-free): agent, loop, tools registry, history,
                 compaction, caching, usage, events, llm client
  memory/        profile (lasting), pantry (per conversation), suggestion history (lasting)
  recipes/       plan (meals, dishes, ids), validate (the gates), kosher, timing,
                 fetch (safe), jsonld, matching (filters), substitutions, store, link guard
  tools/         the tools the model can call
  server/        FastAPI: SSE chat (ideas + recipe cards), plan, feedback, profile, pantry, cooked;
                 a request id makes each button press run once
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
