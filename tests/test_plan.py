"""Stage 9a: the meal plan data model, stable ids, status changes and persistence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chefscout.core.errors import ToolError
from chefscout.recipes.plan import (
    DishDraft,
    MealDraft,
    PlanStore,
    RequestParams,
    template_for,
    templates,
)

U, C = "me", "c1"


def dish(role="main", name="עוף בתנור", base="עוף", kosher="meat", method="oven", **kw) -> DishDraft:
    return DishDraft(role=role, name=name, base=base, kosher=kosher, method=method, **kw)


def two_meals() -> list[MealDraft]:
    return [
        MealDraft(
            title="ארוחה מהתנור",
            dishes=[
                dish(),
                dish("side", "אורז לבן", "אורז", "parve", "stovetop"),
                dish("salad", "סלט ירקות", "ירקות", "parve", "raw"),
            ],
        ),
        MealDraft(
            title="ארוחה מהסיר",
            dishes=[
                dish("main", "מג'דרה", "עדשים", "parve", "stovetop"),
                dish("salad", "סלט כרוב", "כרוב", "parve", "raw"),
            ],
        ),
    ]


@pytest.fixture
def store(tmp_path) -> PlanStore:
    return PlanStore(tmp_path)


@pytest.fixture
def plan(store):
    return store.new_plan(U, C, "meal", RequestParams(meal_time="lunch"), meals=two_meals())


# ---- templates ---------------------------------------------------------------------


def test_templates_load_and_unknown_time_falls_back_to_any():
    assert template_for("lunch").roles == ["main", "side", "salad"]
    assert "soup" in template_for("shabbat").optional
    assert template_for("brunch") == templates()["any"]


# ---- ids ---------------------------------------------------------------------------


def test_meals_and_dishes_get_stable_ids(plan):
    assert [m.id for m in plan.meals] == ["M1", "M2"]
    assert [d.id for d in plan.meals[0].dishes] == ["M1.D1", "M1.D2", "M1.D3"]
    assert plan.meal_of("M2.D2").id == "M2" and plan.dish("M1.D2").name == "אורז לבן"
    assert all(d.status == "proposed" and d.search_status == "none" for d in plan.all_dishes())


def test_single_ideas_get_d_ids(store):
    p = store.new_plan(U, C, "dish_ideas", RequestParams(), ideas=[dish(), dish(name="דג בתנור", base="דג")])
    assert [d.id for d in p.ideas] == ["D1", "D2"] and p.meals == []


def test_bad_drafts_are_rejected():
    with pytest.raises(ValidationError):
        dish(role="snack")
    with pytest.raises(ValidationError):
        MealDraft(title="ריקה", dishes=[])


# ---- persistence -------------------------------------------------------------------


def test_plan_survives_a_new_store_like_a_page_refresh(store, plan, tmp_path):
    store.select(U, C, meal_id="M1")
    again = PlanStore(tmp_path).load(U, C)
    assert again == store.load(U, C)
    assert again.dish("M1.D1").status == "selected"


def test_no_plan_yet(store):
    assert store.load(U, "other") is None
    with pytest.raises(ToolError, match="no plan"):
        store.select(U, "other", meal_id="M1")


def test_a_new_request_replaces_the_plan_and_bumps_the_version(store, plan):
    p2 = store.new_plan(U, C, "dish_ideas", RequestParams(), ideas=[dish()])
    assert p2.version == plan.version + 1 and p2.meals == []


# ---- selection ---------------------------------------------------------------------


def test_selecting_a_meal_selects_all_its_dishes_only(store, plan):
    picked = store.select(U, C, meal_id="M1")
    assert [d.id for d in picked] == ["M1.D1", "M1.D2", "M1.D3"]
    p = store.load(U, C)
    assert all(d.status == "selected" for d in p.meal("M1").dishes)
    assert all(d.status == "proposed" for d in p.meal("M2").dishes)


def test_selecting_one_dish_selects_only_it(store, plan):
    store.select(U, C, dish_ids=["M2.D1"])
    statuses = {d.id: d.status for d in store.load(U, C).all_dishes()}
    assert statuses["M2.D1"] == "selected"
    assert [k for k, v in statuses.items() if v == "selected"] == ["M2.D1"]


def test_selecting_twice_changes_nothing(store, plan):
    store.select(U, C, meal_id="M1")
    v = store.load(U, C).version
    store.select(U, C, meal_id="M1")
    store.select(U, C, dish_ids=["M1.D1"])
    assert store.load(U, C).version == v


def test_select_needs_a_target_and_known_ids(store, plan):
    with pytest.raises(ToolError):
        store.select(U, C)
    with pytest.raises(ToolError, match="unknown meal"):
        store.select(U, C, meal_id="M9")
    with pytest.raises(ToolError, match="unknown dish"):
        store.select(U, C, dish_ids=["M1.D9"])


# ---- replacing ---------------------------------------------------------------------


def test_replace_keeps_every_other_dish_and_id(store, plan):
    new = store.replace(U, C, "M1.D2", dish("side", "תפוחי אדמה בתנור", "תפוחי אדמה", "parve", "oven"))
    p = store.load(U, C)
    assert new.id == "M1.D4" and new.replaces == "M1.D2"
    assert p.dish("M1.D2").status == "replaced"  # not "rejected": no reason was given
    assert [d.id for d in p.meal("M1").active_dishes()] == ["M1.D1", "M1.D4", "M1.D3"]
    assert p.meal("M2") == plan.meal("M2")


def test_replacing_a_selected_dish_keeps_the_rest_and_their_recipes(store, plan, tmp_path):
    store.select(U, C, meal_id="M1")
    # Recipes were already found for the meal (stage 9e fills these; set here directly).
    p = store.load(U, C)
    for d in p.meal("M1").dishes:
        d.recipe_id, d.search_status = f"R-{d.id}", "found"
    store._save(U, C, p)

    new = store.replace(U, C, "M1.D3", dish("salad", "סלט כרוב", "כרוב", "parve", "raw"))
    p = store.load(U, C)
    assert new.status == "selected" and new.search_status == "none" and new.recipe_id is None
    kept = [d for d in p.meal("M1").active_dishes() if d.id != new.id]
    assert [(d.id, d.recipe_id, d.search_status) for d in kept] == [
        ("M1.D1", "R-M1.D1", "found"),
        ("M1.D2", "R-M1.D2", "found"),
    ]


def test_explicit_rejection_and_single_ideas(store):
    store.new_plan(U, C, "vague", RequestParams(), ideas=[dish(), dish(name="פשטידה", base="ביצים")])
    new = store.replace(U, C, "D1", dish(name="שניצל", base="עוף"), status="rejected")
    p = store.load(U, C)
    assert new.id == "D3" and p.dish("D1").status == "rejected"
    assert [d.id for d in p.ideas if d.active] == ["D3", "D2"]


def test_a_replaced_dish_cannot_be_replaced_or_selected_again(store, plan):
    store.replace(U, C, "M1.D2", dish("side", "פתיתים", "פתיתים", "parve", "stovetop"))
    with pytest.raises(ToolError, match="already replaced"):
        store.replace(U, C, "M1.D2", dish("side", "בורגול", "בורגול", "parve", "stovetop"))
    with pytest.raises(ToolError, match="choose an active dish"):
        store.select(U, C, dish_ids=["M1.D2"])


def test_selecting_a_meal_after_a_replacement_skips_the_old_dish(store, plan):
    store.replace(U, C, "M1.D2", dish("side", "פתיתים", "פתיתים", "parve", "stovetop"))
    picked = store.select(U, C, meal_id="M1")
    assert [d.id for d in picked] == ["M1.D1", "M1.D4", "M1.D3"]
    assert store.load(U, C).dish("M1.D2").status == "replaced"


# ---- transitions -------------------------------------------------------------------


@pytest.mark.parametrize(
    "first,then,ok",
    [
        ("selected", "rejected", True),
        ("rejected", "selected", False),
        ("replaced", "proposed", False),
        ("selected", "proposed", False),
    ],
)
def test_only_allowed_status_changes(store, plan, first, then, ok):
    store.transition(U, C, "M1.D1", first)
    if ok:
        assert store.transition(U, C, "M1.D1", then).status == then
    else:
        with pytest.raises(ToolError, match="cannot become"):
            store.transition(U, C, "M1.D1", then)


def test_the_same_status_twice_is_a_no_op(store, plan):
    store.transition(U, C, "M1.D1", "selected")
    v = store.load(U, C).version
    store.transition(U, C, "M1.D1", "selected")
    assert store.load(U, C).version == v


def test_plan_path_uses_safe_ids(store):
    with pytest.raises(Exception, match="conversation id"):
        store.path(U, "../evil")
