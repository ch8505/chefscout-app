"""Stage 9b: the gates every idea passes: kashrut, profile, meal template, variety, time."""

from __future__ import annotations

import pytest

from chefscout.memory.profile import Profile
from chefscout.recipes.kosher import classify, fits, meal_category
from chefscout.recipes.matching import same_food, similar_names, strip_term
from chefscout.recipes.plan import DishDraft, MealDraft, PlanStore, RequestParams
from chefscout.recipes.timing import time_fit
from chefscout.recipes.validate import validate_ideas, validate_replacement


def dish(role, name, base, kosher="parve", method="stovetop", keys=(), minutes=None) -> DishDraft:
    return DishDraft(
        role=role, name=name, base=base, kosher=kosher, method=method,
        key_ingredients=list(keys), est_minutes=minutes,
    )  # fmt: skip


def chicken_meal(title="ארוחה מהתנור") -> MealDraft:
    return MealDraft(
        title=title,
        style="ביתי",
        dishes=[
            dish("main", "עוף בתנור עם בצל", "עוף", "meat", "oven", ["עוף", "בצל"], 60),
            dish("side", "אורז לבן", "אורז", keys=["אורז"], minutes=25),
            dish("salad", "סלט ירקות", "ירקות", method="raw", keys=["מלפפון", "עגבנייה"], minutes=10),
        ],
    )


def lentil_meal() -> MealDraft:
    return MealDraft(
        title="ארוחה מהסיר",
        style="ים תיכוני",
        dishes=[
            dish("main", "מג'דרה", "עדשים", keys=["עדשים", "אורז", "בצל"], minutes=45),
            dish("side", "יוגורט עם נענע", "יוגורט", "dairy", "no_cook", ["יוגורט"]),
            dish("salad", "סלט כרוב", "כרוב", method="raw", keys=["כרוב"]),
        ],
    )


LUNCH = RequestParams(meal_time="lunch")
NOBODY = Profile()


def meals_report(meals, request=LUNCH, profile=NOBODY, rejected=()):
    return validate_ideas("meal", request, profile, meals=meals, rejected=rejected)


# ---- kashrut ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lines,category",
    [
        (['1 ק"ג חזה עוף', "2 בצלים"], "meat"),
        (["200 גרם גבינה בולגרית", "3 ביצים"], "dairy"),
        (["מלפפון", "עגבנייה", "שמן זית"], "parve"),
        (['400 מ"ל חלב קוקוס', "עוף"], "meat"),  # coconut milk is parve
        (["1 מיכל שמנת צמחית", "שוקולד מריר"], "parve"),
        (["4 חלבונים", "סוכר"], "parve"),  # egg whites, not milk
        (["2 כפות חמאת בוטנים"], "parve"),
        (["נקניקיות טבעוניות"], "parve"),
        (["פילה סלמון", "לימון"], "parve"),  # fish is parve
        (["100 גרם חמאה או מרגרינה", "קמח"], "dairy"),  # in doubt, never parve
        (["1/2 כוס חמאה"], "dairy"),  # "/" is an amount, not "or"
        (["עוף", "100 גרם חמאה"], "mixed"),
    ],
)
def test_classify(lines, category):
    assert classify(lines).category == category


def test_optional_butter_in_a_meat_dish_is_flagged_not_mixed():
    k = classify(["שוקי עוף", "2 כפות חמאה או מרגרינה"])
    assert k.category == "meat" and k.optional_dairy == ["2 כפות חמאה או מרגרינה"]


def test_kosher_fits_and_meal_category():
    assert fits("meat", "parve") and fits("any", "dairy") and not fits("meat", "dairy")
    assert not fits("any", "mixed")
    assert meal_category(["meat", "parve"]) == "meat"
    assert meal_category(["meat", "dairy"]) == "mixed"


def test_helpers():
    assert strip_term("1 כוס חלב קוקוס", "חלב קוקוס") == "1 כוס"
    assert same_food("חזה עוף", "עוף") and same_food("פרגית", "שוקיים") and same_food("סלמון", "דג")
    assert not same_food("עוף", "בקר") and not same_food("עדשים", "אורז")
    assert similar_names("סלט ירקות", "סלט ירקות קצוץ") and not similar_names("סלט", "סלט כרוב")


# ---- time ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes,limit,fit",
    [(30, 30, "ok"), (38, 30, "close"), (40, 30, "close"), (60, 30, "over"), (None, 30, "unknown"),
     (90, None, "any"), (115, 100, "close"), (125, 100, "over")],
)  # fmt: skip
def test_time_fit(minutes, limit, fit):
    assert time_fit(minutes, limit) == fit


# ---- meals -----------------------------------------------------------------------------


def test_two_good_different_meals_pass():
    r = meals_report([chicken_meal(), lentil_meal()])
    assert r.ok, r.errors


def test_meal_without_a_main_is_rejected():
    meal = chicken_meal()
    meal.dishes = meal.dishes[1:]
    assert "needs: main" in meals_report([meal, lentil_meal()]).text()


def test_full_meal_covers_main_and_side():
    one_pan = MealDraft(
        title="תבנית אחת",
        dishes=[
            dish("full_meal", "עוף עם תפוחי אדמה בתבנית", "עוף", "meat", "oven", ["עוף", "תפוחי אדמה"]),
            dish("salad", "סלט חסה", "חסה", method="raw"),
        ],
    )
    assert meals_report([one_pan, lentil_meal()]).ok


def test_meat_meal_with_a_dairy_dish_is_rejected():
    meal = chicken_meal()
    meal.dishes[1] = dish("side", "פירה עם חמאה", "תפוחי אדמה", "dairy", keys=["תפוחי אדמה", "חמאה"])
    assert "mixes meat and dairy" in meals_report([meal, lentil_meal()]).text()


def test_dish_marked_parve_but_has_cheese_is_rejected():
    meal = lentil_meal()
    meal.dishes[1] = dish("side", "פסטה ברוטב", "פסטה", "parve", keys=["פסטה", "גבינה צהובה"])
    assert "marked parve but has" in meals_report([chicken_meal(), meal]).text()


def test_request_for_a_meat_meal():
    req = RequestParams(meal_time="lunch", kosher="meat")
    r = meals_report([chicken_meal(), lentil_meal()], request=req)
    assert "the request is meat" in r.text()  # yogurt in a meat request
    assert "has no meat dish" in r.text()


def test_two_meals_on_chicken_are_rejected_but_two_ovens_are_fine():
    other = chicken_meal(title="עוד עוף")
    other.dishes[0] = dish("main", "שניצל בתנור", "חזה עוף", "meat", "oven", ["חזה עוף"])
    assert "both meals are based on" in meals_report([chicken_meal(), other]).text()

    fish = lentil_meal()
    fish.dishes[0] = dish("main", "סלמון בתנור", "סלמון", "parve", "oven", ["סלמון", "לימון"])
    fish.dishes[1] = dish("side", "תפוחי אדמה בתנור", "תפוחי אדמה", method="oven")
    r = meals_report([chicken_meal(), fish])
    assert r.ok and r.variety in ("medium", "low") and "both oven" in r.text()


def test_same_salad_in_both_meals_is_rejected():
    other = lentil_meal()
    other.dishes[2] = dish("salad", "סלט ירקות קצוץ", "ירקות", method="raw")
    assert "are the same dish" in meals_report([chicken_meal(), other]).text()


def test_more_than_two_meals_or_single_ideas_for_a_meal():
    assert "at most 2" in meals_report([chicken_meal(), lentil_meal(), lentil_meal()]).text()
    r = validate_ideas("meal", LUNCH, NOBODY, ideas=[dish("main", "מג'דרה", "עדשים")])
    assert "needs meals" in r.text()


# ---- profile, history, time (every gate) ------------------------------------------------


def test_profile_hard_rules_block_an_idea():
    p = Profile(allergies=["אגוזים"], avoid=["טונה"], kitchen_rules=["בלי מרגרינה"])
    ideas = [
        dish("main", "פסטה עם טונה", "פסטה", keys=["פסטה", "טונה"]),
        dish("dessert", "עוגת שקדים", "שקדים", keys=["שקדים", "מרגרינה"]),
        dish("main", "שקשוקה", "ביצים", keys=["ביצים", "עגבניות"]),
    ]
    r = validate_ideas("dish_ideas", RequestParams(), p, ideas=ideas)
    assert "breaks the profile: טונה" in r.text() and "breaks the profile: אגוזים" in r.text()
    assert "needs a substitute for מרגרינה" in r.text()


def test_a_rejected_dish_is_blocked_but_others_are_not():
    r = validate_ideas(
        "dish_ideas", RequestParams(), NOBODY,
        ideas=[dish("main", "מג'דרה", "עדשים"), dish("main", "שקשוקה", "ביצים"),
               dish("main", "פסטה ברוטב עגבניות", "פסטה")],
        rejected=["מג'דרה"],
    )  # fmt: skip
    assert r.errors == ["'מג'דרה' was rejected by the user recently. suggest something else"]


def test_time_over_the_limit_is_rejected_and_close_is_a_note():
    req = RequestParams(max_minutes=30)
    ideas = [
        dish("main", "צלי בקר", "בקר", "meat", "slow", minutes=180),
        dish("main", "חביתת ירק", "ביצים", minutes=38),
        dish("main", "פסטה ברוטב עגבניות", "פסטה", minutes=20),
    ]
    r = validate_ideas("dish_ideas", req, NOBODY, ideas=ideas)
    assert r.errors == ["'צלי בקר' takes about 180 min; the user asked for up to 30 (at most 40)"]
    assert "a bit over the time" in r.text()


def test_three_ideas_need_different_bases():
    ideas = [
        dish("main", "שניצל", "עוף", "meat"),
        dish("main", "עוף בגריל", "פרגית", "meat", "grill"),
        dish("dessert", "עוגת גבינה", "גבינה", "dairy", "oven", ["גבינה"]),
    ]
    r = validate_ideas("vague", RequestParams(), NOBODY, ideas=ideas)
    assert "are both based on" in r.text() and len(r.errors) == 1


# ---- replacing one dish ------------------------------------------------------------------


@pytest.fixture
def plan(tmp_path):
    store = PlanStore(tmp_path)
    return store.new_plan("me", "c1", "meal", LUNCH, meals=[chicken_meal(), lentil_meal()])


def test_replacement_must_keep_the_role_and_pass_every_gate(plan):
    p = Profile(avoid=["טונה"])
    r = validate_replacement(plan, "M1.D2", dish("salad", "סלט טונה", "טונה", keys=["טונה"]), p)
    assert "same kind of dish (side)" in r.text() and "breaks the profile: טונה" in r.text()


def test_replacement_cannot_bring_dairy_into_a_meat_meal(plan):
    r = validate_replacement(plan, "M1.D2", dish("side", "פירה עם חמאה", "תפוחי אדמה", "dairy"), NOBODY)
    assert "would mix meat and dairy" in r.text()


def test_replacement_main_must_differ_from_the_other_meal(plan):
    r = validate_replacement(plan, "M2.D1", dish("main", "שוקיים בתנור", "שוקיים", "meat", "oven"), NOBODY)
    # M2 has a dairy side: chicken there also mixes meat and dairy.
    assert "M1 is already based on" in r.text() and "would mix" in r.text()


def test_good_replacement_and_same_dish_again(plan):
    good = dish("side", "תפוחי אדמה בתנור", "תפוחי אדמה", method="oven")
    assert validate_replacement(plan, "M1.D2", good, NOBODY).ok
    same = validate_replacement(plan, "M1.D2", dish("side", "אורז לבן", "אורז"), NOBODY)
    assert "is the dish being replaced" in same.text()
    dup = validate_replacement(plan, "M1.D2", dish("side", "סלט כרוב", "כרוב"), NOBODY)
    assert "already in the plan (M2.D3)" in dup.text()


def test_meal_kind_is_derived_from_its_dishes(plan):
    assert plan.meal("M1").kosher == "meat" and plan.meal("M2").kosher == "dairy"
