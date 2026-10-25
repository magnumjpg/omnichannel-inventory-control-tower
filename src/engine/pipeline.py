"""Weekly control-tower orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src import config
from src.data.ingestion import load_raw_feeds
from src.engine.container_knapsack import (
    ContainerPlan,
    FactoryPlan,
    build_factory_plan,
    optimize_container,
)
from src.engine.fee_audit import build_fee_risk_audit
from src.engine.multiechelon import build_replenishment_plan
from src.engine.unconstraining import estimate_unconstrained_demand


def run_pipeline(
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    persist: bool = True,
) -> dict[str, object]:
    """Run demand unconstraining, buffers, factory MOQs and knapsack."""

    feeds = load_raw_feeds(data_dir)
    catalog = feeds["catalog_master"]
    fba = feeds["amazon_fba_inventory"]
    tpl = feeds["warehouse_3pl_stock"]
    daily = feeds["daily_sales_history"]

    demand = estimate_unconstrained_demand(daily, catalog)
    fba_plan = build_replenishment_plan(catalog, demand, fba, tpl)
    factory = build_factory_plan(catalog, demand, fba_plan, tpl)
    container = optimize_container(factory, catalog, demand, fba_plan)
    fee = build_fee_risk_audit(catalog, fba)

    summary = {
        "snapshot_date": str(pd.to_datetime(fba["snapshot_date"]).max().date()),
        "sku_count": int(len(catalog)),
        "fba_available_units": int(fba["fba_available"].sum()),
        "three_pl_units": int(tpl["on_hand_units"].sum()),
        "open_po_units": int(feeds["open_purchase_orders"]["open_units"].sum()),
        "stockout_skus_hdos_lt_14": int((fba["short_term_hdos_30d"] < 14).sum()),
        "fee_band_skus_hdos_14_28": int(
            (
                (fba["short_term_hdos_30d"] >= 14)
                & (fba["long_term_hdos_90d"] < 28)
                & (fba["short_term_hdos_30d"] < 28)
                & (~fba["low_inv_fee_exempt_flag"])
            ).sum()
        ),
        "aged_cliff_skus": int(
            ((fba["inv_age_271_365"] + fba["inv_age_365_plus"]) > 0).sum()
        ),
        "transfer_cartons": int(fba_plan["executable_transfer_cartons"].sum()),
        "transfer_units": int(fba_plan["executable_transfer_units"].sum()),
        "factory_units": int(factory.total_units),
        "factory_cartons": int(factory.total_cartons),
        "factory_cbm": round(float(factory.total_cbm), 4),
        "container": container.to_dict(),
        "aggregate_moq_met": bool(factory.aggregate_moq_met),
        "projected_monthly_ais_usd": round(float(fee["projected_monthly_ais"].sum()), 2),
        "fee_exposure_usd": round(float(fba_plan["low_inventory_fee_exposure"].sum()), 2),
        "removal_recommendations": int((fee["recommendation"] == "EXECUTE REMOVAL").sum()),
    }

    if persist:
        out = Path(output_dir) if output_dir else config.DATA_PROCESSED_DIR
        out.mkdir(parents=True, exist_ok=True)
        demand.to_csv(out / "demand_unconstrained.csv", index=False)
        fba_plan.to_csv(out / "fba_replenishment_plan.csv", index=False)
        factory.lines.to_csv(out / "factory_po_plan.csv", index=False)
        container.lines.to_csv(out / "container_manifest.csv", index=False)
        fee.to_csv(out / "fee_risk_audit.csv", index=False)
        with (out / "container_plan.json").open("w", encoding="utf-8") as fh:
            json.dump(container.to_dict(), fh, indent=2)
        with (out / "exec_summary.json").open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)

    return {
        "feeds": feeds,
        "demand": demand,
        "fba_plan": fba_plan,
        "factory_plan": factory,
        "container_plan": container,
        "fee_audit": fee,
        "summary": summary,
    }
