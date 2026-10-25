"""Contract for the walk-forward estimator backtest.

The claim in the README is not "the estimator is good"; it is "the shipped
ladder beat three named alternatives on nine monthly cutoffs".  That claim is
regression-tested here so that a future change to the estimator cannot silently
invalidate the published table.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import config

ARTIFACT = config.DATA_PROCESSED_DIR / "estimator_backtest.csv"
METHODS = {"shipped", "kappa_blend", "naive", "sister"}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    assert ARTIFACT.exists(), (
        "run `python -m src.diagnostics.estimator_backtest` before the suite"
    )
    return pd.read_csv(ARTIFACT)


def test_artifact_covers_nine_cutoffs_and_four_methods(table):
    assert table["cutoff"].nunique() == 9
    assert set(table["method"]) == METHODS
    assert len(table) == 36
    assert table["n_skus"].eq(160).all()


def test_shipped_ladder_wins_on_every_summary_metric(table):
    summary = table.groupby("method").agg(
        abs_bias=("bias_pct", lambda s: s.abs().mean()),
        wape=("wape_pct", "mean"),
        worst=("wape_pct", "max"),
        censored=("censored_wape_pct", "mean"),
    )
    shipped = summary.loc["shipped"]
    for rival in ("kappa_blend", "naive", "sister"):
        assert shipped["wape"] < summary.loc[rival, "wape"]
        assert shipped["abs_bias"] < summary.loc[rival, "abs_bias"]
        assert shipped["censored"] < summary.loc[rival, "censored"]
    # Guards the absolute claim in the README without pinning the exact figure.
    assert shipped["wape"] < 3.0
    assert shipped["worst"] < 5.0
    assert shipped["abs_bias"] < 2.0


def test_shipped_ladder_wins_on_each_individual_cutoff(table):
    pivot = table.pivot(index="cutoff", columns="method", values="wape_pct")
    for cutoff, row in pivot.iterrows():
        assert row["shipped"] <= row["kappa_blend"], cutoff
        assert row["shipped"] <= row["sister"], cutoff
