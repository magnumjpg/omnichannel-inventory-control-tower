"""Executive S&OP briefing (Markdown) generated from one pipeline run."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src import config


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _int(value: float) -> str:
    return f"{int(round(value)):,}"


def write_executive_briefing(
    result: dict[str, Any],
    output_path: Path | str | None = None,
) -> Path:
    """Write a one-page Monday-morning briefing and return its path."""

    summary = result["summary"]
    demand = result["demand"]
    fba_plan = result["fba_plan"]
    factory = result["factory_plan"]
    container = result["container_plan"]
    fee = result["fee_audit"]

    destination = (
        Path(output_path)
        if output_path is not None
        else config.OUTPUTS_DIR / "Weekly_Executive_Briefing.md"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    top_transfers = fba_plan[fba_plan["executable_transfer_units"] > 0].nlargest(
        8, "executable_transfer_units"
    )
    fee_band = fba_plan[
        (fba_plan["effective_hdos"] < config.LOW_INVENTORY_FEE_THRESHOLD_DAYS)
        & (fba_plan["low_inventory_fee_per_unit"] > 0)
    ].nlargest(8, "low_inventory_fee_exposure")
    removals = fee[fee["recommendation"] == "EXECUTE REMOVAL"]
    if not container.lines.empty:
        # Show the ten largest loads and always surface every top-up line, so
        # the detail table can never contradict the "base + top-up" headline.
        top_factory = pd.concat(
            [
                container.lines[container.lines["topup_cartons"] > 0],
                container.lines.nlargest(10, "total_units"),
            ]
        ).drop_duplicates("sku_id").sort_values("total_units", ascending=False)
    else:
        top_factory = factory.lines.nlargest(10, "base_units")
    top_demand = demand.nlargest(5, "mu_global")

    lines: list[str] = []
    lines.append("# Weekly Omnichannel Inventory Briefing")
    lines.append("")
    lines.append(
        f"**Snapshot:** {summary.get('snapshot_date', config.SNAPSHOT_DATE)}  "
    )
    lines.append(
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  "
    )
    lines.append(
        f"**Scope:** {summary.get('sku_count', 0)} child FNSKUs · 6 product families · "
        "Amazon FBA US + Shopify DTC + domestic 3PL + Shenzhen/Ningbo factory"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Executive KPI snapshot")
    lines.append("")
    lines.append("| KPI | Value | Policy / interpretation |")
    lines.append("| :--- | ---: | :--- |")
    lines.append(
        f"| FBA available | {_int(summary.get('fba_available_units', 0))} u | Physical sellable stock |"
    )
    lines.append(
        f"| 3PL on hand | {_int(summary.get('three_pl_units', 0))} u | Domestic upstream buffer |"
    )
    lines.append(
        f"| Open factory POs | {_int(summary.get('open_po_units', 0))} u | In production / on water |"
    )
    lines.append(
        f"| FNSKUs HDoS < 14d | {summary.get('stockout_skus_hdos_lt_14', 0)} | Critical stockout band; exempt long-tail included |"
    )
    lines.append(
        f"| Non-exempt fee-band FNSKUs | {summary.get('fee_band_skus_hdos_14_28', 0)} | 30-day window 14-28d; charge model uses max(30d, 90d) |"
    )
    lines.append(
        f"| 3PL → FBA transfer | {_int(summary.get('transfer_units', 0))} u / "
        f"{_int(summary.get('transfer_cartons', 0))} cartons | Age-safe master-carton multiples |"
    )
    lines.append(
        f"| Factory PO | {_int(summary.get('factory_units', 0))} u / "
        f"{_int(summary.get('factory_cartons', 0))} cartons | Aggregate MOQ "
        f"{'MET' if summary.get('aggregate_moq_met') else 'NOT MET'} |"
    )
    lines.append(
        f"| Container | {container.total_cbm:.3f} CBM | "
        f"{container.container_type} · {container.utilization:.1%} utilisation "
        f"(base {container.base_cbm:.3f} + top-up {container.topup_cbm:.3f}) |"
    )
    lines.append(
        f"| Worst-case 30-day fee exposure | {_money(summary.get('fee_exposure_usd', 0))} | "
        "Upper bound: every transferred unit ships while still inside the band |"
    )
    lines.append(
        f"| Projected monthly AIS | {_money(summary.get('projected_monthly_ais_usd', 0))} | "
        "2026 aged-inventory surcharge |"
    )
    lines.append(
        f"| Removal recommendations | {summary.get('removal_recommendations', 0)} | "
        "Payback ≤ 12 months |"
    )
    lines.append("")
    lines.append("## 2. Demand unconstraining")
    lines.append("")
    lines.append(
        "The estimator widens the exposure window until the SKU becomes visible: the "
        "active-day rate when τ > 0, the 90-day rate when the SKU was dark for the whole "
        "month, and the lifetime active-day rate when even 90 days were dark. The sister "
        "prior with its "
        f"{config.GAMMA_CANNIBALIZATION:.0%} substitution deflator is a last resort. The "
        "nine-cutoff walk-forward backtest behind that ladder, including the rejected "
        "κ-blend, is in `outputs/estimator_backtest.md`. The table shows the five "
        "largest global unconstrained velocities."
    )
    lines.append("")
    lines.append(
        "| SKU | Tier | Naive 30d | Active-day | Cross-sectional | Unconstrained FBA | DTC | Global | Uplift |"
    )
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in top_demand.iterrows():
        lines.append(
            f"| `{r['sku_id']}` | {r['abc_xyz_tier']} | {r['naive_30d']:.2f} | "
            f"{r['mu_active']:.2f} | {r['mu_cs']:.2f} | {r['mu_unconstrained']:.2f} | "
            f"{r['mu_dtc']:.2f} | {r['mu_global']:.2f} | {r['unconstraining_uplift']:.1%} |"
        )
    lines.append("")
    uplift = demand["unconstraining_uplift"]
    positive = uplift[uplift > 0]
    if positive.empty:
        lines.append("No FNSKU shows positive unconstraining uplift in this snapshot.")
    else:
        lines.append(
            f"{len(positive)} of {len(demand)} FNSKUs show positive uplift; the median "
            f"uplift among them is {positive.median():.1%} and the catalogue-wide mean "
            f"(zero-uplift SKUs included) is {uplift.mean():.1%}."
        )
    lines.append("")
    lines.append("## 3. Nivel 1 · 3PL → FBA transfer")
    lines.append("")
    lines.append(
        "Every transfer is an integer master-carton multiple and respects the "
        f"{config.FBA_AGE_CAP_DAYS:.0f}-day post-transfer FBA age cap. The policy buffer is "
        f"B_FBA = max(SS₁, {config.FBA_SHIELD_DAYS:.0f}·D_FBA) for non-exempt heroes."
    )
    lines.append("")
    lines.append("| SKU | Tier | HDoS | Fee/u | Transfer cartons | Transfer units | Post-DOS | Fee exposure |")
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in top_transfers.iterrows():
        lines.append(
            f"| `{r['sku_id']}` | {r['abc_xyz_tier']} | {r['effective_hdos']:.1f} | "
            f"{_money(r['low_inventory_fee_per_unit'])} | {int(r['executable_transfer_cartons'])} | "
            f"{_int(r['executable_transfer_units'])} | {r['post_transfer_dos']:.1f} | "
            f"{_money(r['low_inventory_fee_exposure'])} |"
        )
    if fee_band.empty:
        lines.append("| — | — | — | — | — | — | — | — |")
    lines.append("")
    lines.append("**Low-inventory fee watch list**")
    lines.append("")
    lines.append("| SKU | Tier | Effective HDoS | Weekly units | Fee/u | Exposure |")
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: |")
    for _, r in fee_band.iterrows():
        lines.append(
            f"| `{r['sku_id']}` | {r['abc_xyz_tier']} | {r['effective_hdos']:.1f} | "
            f"{int(r['weekly_units_sold_7d'])} | {_money(r['low_inventory_fee_per_unit'])} | "
            f"{_money(r['low_inventory_fee_exposure'])} |"
        )
    if fee_band.empty:
        lines.append("| — | — | — | — | — | — |")
    lines.append("")
    lines.append("## 4. Nivel 2 · Factory PO and container")
    lines.append("")
    lines.append(
        f"The weekly cadence books one **{container.container_type}** with a usable capacity of "
        f"{container.capacity_cbm:.1f} CBM. Baseline demand consumes {container.base_cbm:.3f} CBM; "
        f"the Tier-A profit-velocity knapsack adds {container.topup_cbm:.3f} CBM for a total of "
        f"**{container.total_cbm:.3f} CBM** ({container.utilization:.1%} fill)."
    )
    lines.append("")
    lines.append("| SKU | Tier | Base units | Top-up cartons | Total cartons | Total CBM | Status |")
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | :--- |")
    for _, r in top_factory.iterrows():
        status = "BASE + TOP-UP" if int(r.get("topup_cartons", 0)) > 0 else "BASE LOAD"
        lines.append(
            f"| `{r['sku_id']}` | {r['abc_xyz_tier']} | {_int(r['base_units'])} | "
            f"{int(r.get('topup_cartons', 0))} | {int(r['base_cartons'] + r.get('topup_cartons', 0))} | "
            f"{float(r['line_cbm'] + r.get('topup_cartons', 0) * r['carton_volume_cbm']):.3f} | {status} |"
        )
    lines.append("")
    lines.append(
        f"SKU MOQs and the {config.DEFAULT_PO_AGGREGATE_MOQ_UNITS:,}-unit aggregate PO floor are "
        "hard constraints. Lines whose MOQ would breach the 135-day system cap are surfaced "
        "as `MOQ OVERRIDE` in Tab 04 rather than silently over-ordering."
    )
    lines.append("")
    lines.append("## 5. Nivel 0 · Aged-inventory cliff audit")
    lines.append("")
    if removals.empty:
        lines.append("No SKU currently clears the 12-month removal payback hurdle.")
    else:
        lines.append(
            "The following FNSKUs should be removed or liquidated **before the 14th at 23:59 PT** "
            "so that the 15th-of-month Amazon aged snapshot does not bill the next tier."
        )
        lines.append("")
        lines.append("| SKU | Tier | 271d+ units | Monthly AIS | Removal cost | Payback (months) |")
        lines.append("| :--- | :--- | ---: | ---: | ---: | ---: |")
        for _, r in removals.iterrows():
            lines.append(
                f"| `{r['sku_id']}` | {r['abc_xyz_tier']} | "
                f"{int(r['inv_age_271_365'] + r['inv_age_365_plus'])} | "
                f"{_money(r['projected_monthly_ais'])} | {_money(r['removal_cost_usd'])} | "
                f"{r['payback_months']:.1f} |"
            )
    lines.append("")
    lines.append("## 6. Monday action list")
    lines.append("")
    lines.append(
        "1. **08:00** — Refresh the six feeds, run `python main.py --run-all`, and verify the "
        "ingestion integrity gate (zero orphan FNSKUs)."
    )
    lines.append(
        "2. **08:30** — Approve Tab 02 exception list; override the unconstrained velocity only "
        "where a documented promotion or influencer event explains the residual."
    )
    lines.append(
        "3. **09:00** — Release the 3PL transfer manifest from Tab 03 to the WMS; every line must "
        "show `CARTON OK` and `Post-transfer DOS ≤ 120`."
    )
    lines.append(
        "4. **10:00** — Transmit the factory PO from Tab 04; confirm the 20' GP allocation and "
        "the Tier-A top-up. Escalate any `MOQ OVERRIDE` line to procurement."
    )
    lines.append(
        "5. **Before the 14th** — Execute removal orders for Tab 05 `EXECUTE REMOVAL` lines and "
        "apply the 25% markdown to `DISCOUNT 25% NOW` lines."
    )
    lines.append(
        "6. **Sign-off** — Record the S&OP decision in Tab 00 and archive the workbook hash with "
        "the weekly operations log."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Generated deterministically by the open-source control tower. All figures are "
        "reproducible with `SEED = 42`; see `README.md` for the mathematical specification."
    )

    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def build_briefing(result: dict[str, Any], output_path: Path | str | None = None) -> Path:
    """Alias used by the CLI."""

    return write_executive_briefing(result, output_path)
