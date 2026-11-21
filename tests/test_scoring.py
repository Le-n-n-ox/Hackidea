"""Unit tests: weighted scoring math, weight normalization, table rendering."""
from hackidea.config import DEFAULT_WEIGHTS
from hackidea.stages.scoring import (DIMENSIONS, normalize_weights, weighted_total,
                                     render_table, map_judging_weights)


def test_default_weights_sum_to_one():
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    set(DEFAULT_WEIGHTS) == set(DIMENSIONS)


def test_weighted_total_all_tens():
    scores = {d: 10 for d in DIMENSIONS}
    assert weighted_total(scores, DEFAULT_WEIGHTS) == 10.0


def test_weighted_total_mixed():
    scores = {"theme_fit": 8, "ai_depth": 9, "feasibility": 7,
              "novelty": 6, "demo_impact": 8, "real_world_impact": 9}
    expected = 8 * .20 + 9 * .20 + 7 * .20 + 6 * .15 + 8 * .15 + 9 * .10
    assert abs(weighted_total(scores, DEFAULT_WEIGHTS) - round(expected, 3)) < 1e-9


def test_normalize_weights_renormalizes():
    w = normalize_weights({"theme_fit": 2, "ai_depth": 2})
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert w["theme_fit"] == 0.5 and w["ai_depth"] == 0.5
    assert w["feasibility"] == 0.0


def test_normalize_weights_clamps_and_falls_back():
    w = normalize_weights({"theme_fit": -3, "ai_depth": 1})
    assert w["theme_fit"] == 0.0 and w["ai_depth"] == 1.0
    assert normalize_weights({}) == DEFAULT_WEIGHTS
    assert normalize_weights({"theme_fit": 0}) == DEFAULT_WEIGHTS


def test_render_table():
    rows = [{"rank": 1, "id": "c01", "name": "MilkFlow",
             "scores": {d: 8 for d in DIMENSIONS}, "weighted_total": 8.0}]
    md = render_table(rows, DEFAULT_WEIGHTS)
    assert "MilkFlow" in md and "weighted" in md and "c01" in md
    assert md.count("\n") >= 3


def test_judging_weights_map_to_supplied_criteria_deterministically():
    weights, note = map_judging_weights({"impact": .30, "innovation": .30,
                                         "technical execution": .25, "presentation": .15})
    assert weights == {"theme_fit": .1, "ai_depth": .2, "feasibility": .2,
                       "novelty": .15, "demo_impact": .15, "real_world_impact": .2}
    assert abs(sum(weights.values()) - 1) < 1e-9
    assert "technical execution" in note


def test_unknown_judging_weights_use_documented_default():
    weights, note = map_judging_weights({"community spirit": 1})
    assert weights == DEFAULT_WEIGHTS
    assert "community spirit" in note
