"""Fake recipe pages (real JSON-LD shapes) and a fake fetcher. No network in tests."""

from __future__ import annotations

import json

from chefscout.core.errors import ToolError


def page(recipe: dict, wrap: str = "plain") -> str:
    node = {"@context": "https://schema.org", "@type": "Recipe", **recipe}
    if wrap == "graph":
        data = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, node]}
    elif wrap == "list":
        data = [{"@type": "Organization"}, {**node, "@type": ["Recipe", "NewsArticle"]}]
    else:
        data = node
    script = json.dumps(data, ensure_ascii=False)
    return f'<html><head><script type="application/ld+json">{script}</script></head><body>...</body></html>'


SHAKSHUKA = page(
    {
        "name": "שקשוקה ביתית קלה",
        "image": ["https://img.example.co.il/shakshuka.jpg"],
        "totalTime": "PT25M",
        "recipeYield": "4 מנות",
        "recipeIngredient": ["4 ביצים", "6 עגבניות בשלות", "1 פלפל אדום", "2 כפות שמן זית", "מלח"],
    }
)
SALAD = page(
    {
        "name": "סלט ישראלי קצוץ",
        "image": {"url": "https://img.example.co.il/salad.jpg"},
        "prepTime": "PT10M",
        "recipeIngredient": ["3 עגבניות", "2 מלפפונים", "לימון", "שמן זית", "מלח"],
    },
    wrap="graph",
)
TUNA_PASTA = page(
    {
        "name": "פסטה עם טונה",
        "totalTime": "PT20M",
        "recipeIngredient": ["250 גרם פסטה", "קופסת טונה בשמן", "עגבניות שרי"],
    },
    wrap="list",
)
CAKE = page(
    {
        "name": "עוגת שוקולד",
        "totalTime": "PT1H10M",
        "recipeIngredient": ["200 גרם מרגרינה", "1 כוס סוכר", "3 ביצים", "1 כוס קמח"],
    }
)
NO_DATA = "<html><body><h1>מתכון</h1><p>טקסט חופשי בלי נתונים מובנים</p></body></html>"

PAGES = {
    "https://site-a.co.il/shakshuka": SHAKSHUKA,
    "https://site-b.co.il/salad": SALAD,
    "https://site-c.co.il/tuna-pasta": TUNA_PASTA,
    "https://site-a.co.il/cake": CAKE,
    "https://site-d.co.il/blog": NO_DATA,
}


class FakeFetcher:
    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = pages if pages is not None else PAGES
        self.requested: list[str] = []

    def get(self, url: str) -> str:
        self.requested.append(url)
        if url not in self.pages:
            raise ToolError("site answered 404")
        return self.pages[url]
