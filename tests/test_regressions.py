"""Regression tests for audit findings that must never come back.

Each test pins a specific inconsistency that was found between the
documentation and the executable behaviour:

* the executive briefing must show the top-up lines it describes;
* the DuckDB audit view must apply the same exemption rules as Python.
"""

from __future__ import annotations

import pandas as pd

from src import config
from src.data.database import build_database, query
from src.deliverables.briefing_report import write_executive_briefing


def test_briefing_table_surfaces_topup_lines(project):
    """The detail table may not contradict the base + top-up headline."""

    container = project.container_plan
    topup = container.lines[container.lines["topup_cartons"] > 0]
    assert not topup.empty, "fixture should contain at least one top-up line"

    path = write_executive_briefing(project.result, project.output_dir / "briefing.md")
    text = path.read_text(encoding="utf-8")

    assert "BASE + TOP-UP" in text
    for _, row in topup.iterrows():
        assert row["sku_id"] in text
        assert f"| {int(row['topup_cartons'])} |" in text, row["sku_id"]


def test_duckdb_fee_view_matches_python_exemptions(project):
    """vw_fba_fee_risk must honour the exemption and the 20-unit weekly floor."""

    db_path = build_database(
        db_path=project.base / "control_tower.duckdb",
        data_dir=project.raw_dir,
    )
    view = query(db_path, "SELECT * FROM vw_fba_fee_risk")

    raw = pd.read_csv(project.raw_dir / "amazon_fba_inventory.csv")
    raw["effective_hdos"] = raw[["short_term_hdos_30d", "long_term_hdos_90d"]].max(axis=1)
    raw["expected_fee"] = [
        config.low_inventory_fee_per_unit(h, w, bool(e))
        for h, w, e in zip(
            raw["effective_hdos"],
            raw["weekly_units_sold_7d"],
            raw["low_inv_fee_exempt_flag"],
        )
    ]

    merged = view.merge(raw[["sku_id", "expected_fee"]], on="sku_id", how="inner")
    assert len(merged) == len(raw)

    no_fee = merged["expected_fee"] == 0
    assert (
        merged.loc[no_fee, "fee_band"] == "EXEMPT_OR_HEALTHY"
    ).all(), "exempt or below-threshold SKUs must not sit in a surcharge band"

    banded = merged.loc[~no_fee]
    assert (~banded["fee_band"].eq("EXEMPT_OR_HEALTHY")).all()
    assert banded["effective_hdos"].lt(config.LOW_INVENTORY_FEE_THRESHOLD_DAYS).all()
    assert banded["weekly_units_sold_7d"].ge(
        config.LOW_INVENTORY_FEE_MIN_WEEKLY_UNITS
    ).all()
    assert (~banded["low_inv_fee_exempt_flag"].astype(bool)).all()
