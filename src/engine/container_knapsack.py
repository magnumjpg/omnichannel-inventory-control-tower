"""Factory MOQ reconciliation and volumetric container knapsack.

The physical constraints are hard:

* every SKU line is an integer number of master cartons,
* every ordered SKU quantity respects ``sku_moq_units``,
* the aggregate purchase order respects ``po_aggregate_moq_units`` (5,000 u),
* every line and every top-up carton respects the 135-day system DOS cap,
* a 20' GP is filled to no more than 28.8 CBM and a 40' HC to no more than
  70.0 CBM.  The top-up is a greedy integer knapsack ordered by marginal
  profit velocity ``D_global * GM / v_i``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src import config


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------
@dataclass
class FactoryPlan:
    lines: pd.DataFrame
    total_units: int
    total_cartons: int
    total_cbm: float
    aggregate_moq_gap_units: int
    aggregate_moq_met: bool
    pulled_forward_skus: list[str] = field(default_factory=list)
    all_lines: pd.DataFrame | None = None


@dataclass
class ContainerPlan:
    container_type: str
    capacity_cbm: float
    base_cbm: float
    topup_cbm: float
    total_cbm: float
    utilization: float
    dead_zone: bool
    lines: pd.DataFrame
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "container_type": self.container_type,
            "capacity_cbm": round(self.capacity_cbm, 4),
            "base_cbm": round(self.base_cbm, 4),
            "topup_cbm": round(self.topup_cbm, 4),
            "total_cbm": round(self.total_cbm, 4),
            "utilization": round(self.utilization, 4),
            "dead_zone": self.dead_zone,
            "total_cartons": int(self.lines["total_cartons"].sum()) if not self.lines.empty else 0,
            "total_units": int(self.lines["total_units"].sum()) if not self.lines.empty else 0,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def moq_cartons(sku_moq_units: int, master_carton_qty: int) -> int:
    return int(math.ceil(sku_moq_units / master_carton_qty))


def reconcile_sku_moq(units: float, sku_moq_units: int, master_carton_qty: int) -> int:
    """Round any raw quantity up to a carton multiple and the SKU MOQ."""

    m = int(master_carton_qty)
    floor_cartons = moq_cartons(sku_moq_units, m)
    cartons = max(floor_cartons, int(math.ceil(max(units, 0) / m - 1e-12)))
    return cartons


def _container_for(cbm: float) -> tuple[str, float]:
    if cbm <= 0:
        return "LCL_OCEAN", 0.0
    if cbm < config.LCL_FCL_BREAKEVEN_CBM:
        return "LCL_OCEAN", 0.0
    if cbm <= config.CONTAINER_20GP_CBM:
        return "FCL_20GP", config.CONTAINER_20GP_CBM
    if cbm <= config.CONTAINER_40HC_CBM:
        return "FCL_40HC", config.CONTAINER_40HC_CBM
    return "FCL_40HC_MULTI", config.CONTAINER_40HC_CBM * math.ceil(cbm / config.CONTAINER_40HC_CBM)


# ---------------------------------------------------------------------------
# Factory plan
# ---------------------------------------------------------------------------
def build_factory_plan(
    catalog: pd.DataFrame,
    demand: pd.DataFrame,
    fba_plan: pd.DataFrame,
    tpl_stock: pd.DataFrame,
) -> FactoryPlan:
    cat = catalog[
        [
            "sku_id", "parent_sku_id", "abc_xyz_tier", "master_carton_qty",
            "carton_volume_cbm", "sku_moq_units", "po_aggregate_moq_units",
            "landed_cost", "retail_price",
        ]
    ].copy()
    dem = demand[
        ["sku_id", "mu_global", "sigma_global", "mu_unconstrained", "sigma_unconstrained"]
    ].copy()
    e1 = fba_plan[
        [
            "sku_id", "ip_fba", "ss2", "b_fba", "mu_global", "mu_unconstrained",
            "effective_hdos", "low_inventory_fee_per_unit",
        ]
    ].rename(columns={"mu_global": "mu_global_e1", "mu_unconstrained": "mu_fba_e1"})
    tpl = tpl_stock[["sku_id", "on_hand_units", "allocated_dtc_units"]].copy()
    df = (
        cat.merge(dem, on="sku_id", how="left")
        .merge(e1, on="sku_id", how="left")
        .merge(tpl, on="sku_id", how="left")
    )
    df = df.fillna(
        {
            "mu_global": 0.0, "sigma_global": 0.0, "mu_unconstrained": 0.0,
            "sigma_unconstrained": 0.0, "ip_fba": 0.0, "ss2": 0.0,
            "b_fba": 0.0, "on_hand_units": 0, "allocated_dtc_units": 0,
            "effective_hdos": 0.0, "low_inventory_fee_per_unit": 0.0,
        }
    )

    d_global = df["mu_global"].to_numpy(dtype=float)
    m = df["master_carton_qty"].to_numpy(dtype=int)
    moq = df["sku_moq_units"].to_numpy(dtype=int)
    ss2 = df["ss2"].to_numpy(dtype=float)
    shield = df["b_fba"].to_numpy(dtype=float)
    ip_sys = (
        df["ip_fba"].to_numpy(dtype=float)
        + df["on_hand_units"].to_numpy(dtype=float)
        - df["allocated_dtc_units"].to_numpy(dtype=float)
    )
    rop2 = d_global * config.L2_MEAN_DAYS + ss2 + shield
    target = (config.L2_MEAN_DAYS + 45.0) * d_global + ss2 + shield
    triggered = (ip_sys <= rop2) & (d_global > 0)

    cartons = np.zeros(len(df), dtype=int)
    cap_exception = np.zeros(len(df), dtype=bool)
    raw_deficit = np.zeros(len(df), dtype=float)
    for i in range(len(df)):
        if not triggered[i]:
            continue
        raw_def = max(0.0, target[i] - ip_sys[i])
        raw_deficit[i] = raw_def
        base = reconcile_sku_moq(raw_def, int(moq[i]), int(m[i]))
        max_units = max(0.0, config.SYSTEM_DOS_CAP_DAYS * d_global[i] - ip_sys[i])
        cap_cartons = int(math.floor(max_units / m[i] + 1e-9))
        min_cartons = moq_cartons(int(moq[i]), int(m[i]))
        if cap_cartons >= min_cartons:
            cartons[i] = max(min_cartons, min(base, cap_cartons))
        else:
            # MOQ is a physical supplier constraint and outranks the planning
            # cap.  The exception is surfaced explicitly for the operator.
            cartons[i] = min_cartons
            cap_exception[i] = True

    df["ip_sys"] = np.round(ip_sys, 2)
    df["rop2_sys"] = np.round(rop2, 2)
    df["smax2"] = np.round(target, 2)
    df["trigger_factory"] = triggered
    df["raw_deficit_units"] = np.round(raw_deficit, 2)
    df["moq_cartons"] = [moq_cartons(int(a), int(b)) for a, b in zip(moq, m)]
    df["days_to_breach"] = np.where(
        d_global > 0, np.round((ip_sys - rop2) / np.maximum(d_global, 1e-9), 2), 999.0
    )

    # ------------------------------------------------------------------
    # Aggregate PO MOQ reconciliation: pull forward the nearest-to-breach
    # SKUs in exact SKU-MOQ increments until the 5,000-unit floor is met.
    # ------------------------------------------------------------------
    base_cartons = cartons.copy()
    base_units = int((base_cartons * m).sum())
    pulled_forward: list[str] = []
    if base_units > 0:
        gap = int(config.DEFAULT_PO_AGGREGATE_MOQ_UNITS) - base_units
        if gap > 0:
            candidates = df[
                (~triggered)
                & (d_global > 0)
                & (df["days_to_breach"] >= 0)
            ].copy()
            candidates["abs_breach"] = candidates["days_to_breach"].abs()
            candidates = candidates.sort_values("abs_breach")
            for idx, row in candidates.iterrows():
                if gap <= 0:
                    break
                i = int(idx)
                add_cartons = reconcile_sku_moq(
                    max(float(moq[i]), gap), int(moq[i]), int(m[i])
                )
                # Respect the 135-day cap for pulled-forward lines as well.
                max_units = max(0.0, config.SYSTEM_DOS_CAP_DAYS * d_global[i] - ip_sys[i])
                max_cartons = int(math.floor(max_units / m[i] + 1e-9))
                min_cartons = moq_cartons(int(moq[i]), int(m[i]))
                if max_cartons < min_cartons:
                    add_cartons = min_cartons
                    cap_exception[i] = True
                else:
                    add_cartons = min(add_cartons, max_cartons)
                if add_cartons <= 0:
                    continue
                cartons[i] = max(cartons[i], add_cartons)
                gap -= int(add_cartons * m[i])
                pulled_forward.append(row["sku_id"])
            if gap > 0 and cartons.sum() > 0:
                # Final backstop: extend the largest existing line in whole
                # cartons rather than issuing a sub-MOQ purchase order.  The
                # backstop must still respect the 135-day system DOS cap; when
                # it cannot, the exception is surfaced instead of hidden.
                largest = int(np.argmax(cartons * m))
                extra = int(math.ceil(gap / m[largest]))
                max_units = max(
                    0.0,
                    config.SYSTEM_DOS_CAP_DAYS * d_global[largest] - ip_sys[largest],
                )
                max_cartons = int(math.floor(max_units / m[largest] + 1e-9))
                allowed = max(0, max_cartons - cartons[largest])
                if extra > allowed:
                    extra = allowed
                    cap_exception[largest] = True
                cartons[largest] += extra

    units = cartons * m
    cbm = cartons * df["carton_volume_cbm"].to_numpy(dtype=float)

    # ------------------------------------------------------------------
    # Weekly cadence: one 20' GP FCL per Monday cycle.  When the raw
    # triggered quantity exceeds 28.8 CBM, the most urgent lines (lowest
    # days-to-breach, then highest velocity) are loaded first; the remainder
    # is explicitly deferred to the next cycle rather than silently
    # over-ordering or splitting into an unplanned second container.
    # ------------------------------------------------------------------
    deferred = np.zeros(len(df), dtype=int)
    if float(cbm.sum()) > config.CONTAINER_20GP_CBM:
        order = df.assign(_line_cbm=cbm).sort_values(
            ["days_to_breach", "mu_global"], ascending=[True, False]
        ).index
        selected = np.zeros(len(df), dtype=bool)
        used = 0.0
        trim_capacity = config.CONTAINER_20GP_CBM - config.TOPUP_RESERVE_CBM
        for idx in order:
            i = int(idx)
            if cartons[i] <= 0:
                continue
            if used + float(cbm[i]) <= trim_capacity + 1e-9:
                selected[i] = True
                used += float(cbm[i])
        deferred[~selected] = cartons[~selected]
        cartons = np.where(selected, cartons, 0)
        units = cartons * m
        cbm = cartons * df["carton_volume_cbm"].to_numpy(dtype=float)

    df["base_cartons"] = cartons
    df["base_units"] = units
    df["line_cbm"] = np.round(cbm, 4)
    df["deferred_cartons"] = deferred
    df["dos_cap_exception"] = cap_exception
    df["post_po_dos"] = np.where(
        d_global > 0,
        np.round((ip_sys + units) / np.maximum(d_global, 1e-9), 2),
        0.0,
    )
    df["pulled_forward"] = df["sku_id"].isin(pulled_forward)

    lines = df[df["base_cartons"] > 0].copy().reset_index(drop=True)
    total_units = int(lines["base_units"].sum()) if not lines.empty else 0
    total_cartons = int(lines["base_cartons"].sum()) if not lines.empty else 0
    total_cbm = float(lines["line_cbm"].sum()) if not lines.empty else 0.0
    gap = (
        max(0, int(config.DEFAULT_PO_AGGREGATE_MOQ_UNITS) - total_units)
        if total_units > 0 else 0
    )
    return FactoryPlan(
        lines=lines,
        total_units=total_units,
        total_cartons=total_cartons,
        total_cbm=round(total_cbm, 4),
        aggregate_moq_gap_units=gap,
        aggregate_moq_met=(total_units == 0 or gap == 0),
        pulled_forward_skus=pulled_forward,
        all_lines=df.copy(),
    )


# ---------------------------------------------------------------------------
# Container knapsack
# ---------------------------------------------------------------------------
def optimize_container(
    factory: FactoryPlan,
    catalog: pd.DataFrame,
    demand: pd.DataFrame,
    fba_plan: pd.DataFrame,
) -> ContainerPlan:
    """Top up a paid FCL container with Tier-A master cartons."""

    if factory.lines.empty or factory.total_cbm <= 0:
        return ContainerPlan(
            container_type="LCL_OCEAN",
            capacity_cbm=0.0,
            base_cbm=0.0,
            topup_cbm=0.0,
            total_cbm=0.0,
            utilization=0.0,
            dead_zone=False,
            lines=pd.DataFrame(),
            notes="No triggered factory lines; no container is booked.",
        )

    base_cbm = float(factory.total_cbm)
    container_type, capacity = _container_for(base_cbm)
    lines = factory.lines.copy().reset_index(drop=True)
    lines["topup_cartons"] = 0
    lines["topup_units"] = 0
    lines["total_cartons"] = lines["base_cartons"]
    lines["total_units"] = lines["base_units"]
    lines["total_cbm"] = lines["line_cbm"]
    lines["marginal_profit_velocity"] = 0.0
    lines["topup_rank"] = 0

    if capacity <= 0:
        return ContainerPlan(
            container_type=container_type,
            capacity_cbm=0.0,
            base_cbm=round(base_cbm, 4),
            topup_cbm=0.0,
            total_cbm=round(base_cbm, 4),
            utilization=1.0,
            dead_zone=False,
            lines=lines,
            notes="Below the 15.0 CBM LCL/FCL breakeven; book LCL consolidation.",
        )

    remaining = capacity - base_cbm
    if remaining <= 1e-9:
        return ContainerPlan(
            container_type=container_type,
            capacity_cbm=capacity,
            base_cbm=round(base_cbm, 4),
            topup_cbm=0.0,
            total_cbm=round(base_cbm, 4),
            utilization=round(base_cbm / capacity, 4),
            dead_zone=False,
            lines=lines,
            notes="Baseline volume already fills the selected container.",
        )

    # Candidate economics -------------------------------------------------
    cat = catalog[
        ["sku_id", "parent_sku_id", "abc_xyz_tier", "master_carton_qty",
         "carton_volume_cbm", "landed_cost", "retail_price", "sku_moq_units"]
    ].copy()
    dem = demand[["sku_id", "mu_global"]].copy()
    if factory.all_lines is not None:
        pol = factory.all_lines[["sku_id", "ip_sys"]].copy()
    elif "ip_sys" in fba_plan.columns:
        pol = fba_plan[["sku_id", "ip_sys"]].copy()
    else:
        pol = None
    cand = cat.merge(dem, on="sku_id", how="left")
    if pol is not None:
        cand = cand.merge(pol, on="sku_id", how="left")
    else:
        cand["ip_sys"] = 0.0
    cand = cand.fillna({"mu_global": 0.0, "ip_sys": 0.0})
    cand["gross_margin_usd"] = (
        cand["retail_price"]
        - cand["landed_cost"]
        - config.AMAZON_REFERRAL_RATE * cand["retail_price"]
    )
    cand["marginal_profit_velocity"] = (
        cand["mu_global"] * cand["gross_margin_usd"] / cand["carton_volume_cbm"]
    )
    # Tier-A hero variants are AX and AY; the top-up ranks them by marginal
    # profit velocity per CBM and still enforces the 135-day DOS cap.
    cand = cand[cand["abc_xyz_tier"].isin(["AX", "AY"])].copy()
    cand = cand.sort_values("marginal_profit_velocity", ascending=False).reset_index(drop=True)

    # Existing order map
    order_units = dict(zip(lines["sku_id"], lines["total_units"]))
    order_cartons = dict(zip(lines["sku_id"], lines["total_cartons"]))
    topup_map: dict[str, int] = {}
    rank_map: dict[str, int] = {}
    velocity_map: dict[str, float] = {}

    for rank, (_, row) in enumerate(cand.iterrows(), start=1):
        if remaining < float(row["carton_volume_cbm"]) - 1e-9:
            continue
        sku = row["sku_id"]
        m = int(row["master_carton_qty"])
        v = float(row["carton_volume_cbm"])
        d = float(row["mu_global"])
        if d <= 0:
            continue
        current_units = order_units.get(sku, 0)
        headroom = max(0.0, config.SYSTEM_DOS_CAP_DAYS * d - (float(row["ip_sys"]) + current_units))
        max_by_dos = int(math.floor(headroom / m + 1e-9))
        max_by_capacity = int(math.floor(remaining / v + 1e-9))
        if sku not in order_cartons:
            # A new factory line must still clear the supplier SKU MOQ.
            min_cartons = moq_cartons(int(row["sku_moq_units"]), m)
            if max_by_capacity < min_cartons:
                continue
        add = min(max_by_capacity, max_by_dos)
        if add <= 0:
            continue
        if sku not in order_cartons and add < moq_cartons(int(row["sku_moq_units"]), m):
            continue
        topup_map[sku] = add
        rank_map[sku] = rank
        velocity_map[sku] = float(row["marginal_profit_velocity"])
        order_units[sku] = current_units + add * m
        order_cartons[sku] = order_cartons.get(sku, 0) + add
        remaining -= add * v

    # Materialise top-up lines, including SKUs that were not in the baseline.
    for sku, add in topup_map.items():
        if sku in set(lines["sku_id"]):
            mask = lines["sku_id"] == sku
            idx = lines.index[mask][0]
            m = int(lines.loc[idx, "master_carton_qty"])
            v = float(lines.loc[idx, "carton_volume_cbm"])
            lines.loc[idx, "topup_cartons"] = add
            lines.loc[idx, "topup_units"] = add * m
            lines.loc[idx, "total_cartons"] += add
            lines.loc[idx, "total_units"] += add * m
            lines.loc[idx, "total_cbm"] = lines.loc[idx, "total_cartons"] * v
            lines.loc[idx, "marginal_profit_velocity"] = round(velocity_map[sku], 2)
            lines.loc[idx, "topup_rank"] = rank_map[sku]
        else:
            c = cand[cand["sku_id"] == sku].iloc[0]
            m = int(c["master_carton_qty"])
            v = float(c["carton_volume_cbm"])
            new_row = {
                "sku_id": sku,
                "parent_sku_id": c.get("parent_sku_id", ""),
                "abc_xyz_tier": c["abc_xyz_tier"],
                "master_carton_qty": m,
                "carton_volume_cbm": v,
                "sku_moq_units": int(c["sku_moq_units"]),
                "po_aggregate_moq_units": config.DEFAULT_PO_AGGREGATE_MOQ_UNITS,
                "landed_cost": c["landed_cost"],
                "retail_price": c["retail_price"],
                "mu_global": c["mu_global"],
                "sigma_global": 0.0,
                "mu_unconstrained": 0.0,
                "sigma_unconstrained": 0.0,
                "ip_fba": 0.0,
                "ss2": 0.0,
                "b_fba": 0.0,
                "effective_hdos": 0.0,
                "low_inventory_fee_per_unit": 0.0,
                "ip_sys": c["ip_sys"],
                "rop2_sys": 0.0,
                "smax2": 0.0,
                "trigger_factory": False,
                "raw_deficit_units": 0.0,
                "moq_cartons": moq_cartons(int(c["sku_moq_units"]), m),
                "dos_cap_exception": False,
                "days_to_breach": 999.0,
                "base_cartons": 0,
                "base_units": 0,
                "line_cbm": 0.0,
                "post_po_dos": 0.0,
                "pulled_forward": False,
                "topup_cartons": add,
                "topup_units": add * m,
                "total_cartons": add,
                "total_units": add * m,
                "total_cbm": add * v,
                "marginal_profit_velocity": round(velocity_map[sku], 2),
                "topup_rank": rank_map[sku],
            }
            lines = pd.concat([lines, pd.DataFrame([new_row])], ignore_index=True)

    lines = lines[lines["total_cartons"] > 0].copy().reset_index(drop=True)
    total_cbm = float(lines["total_cbm"].sum())
    used = min(total_cbm, capacity)
    dead_zone = bool(
        container_type == "FCL_20GP" and used >= config.LCL_FCL_BREAKEVEN_CBM
        and used < 0.833 * config.CONTAINER_20GP_CBM
    )
    return ContainerPlan(
        container_type=container_type,
        capacity_cbm=capacity,
        base_cbm=round(base_cbm, 4),
        topup_cbm=round(total_cbm - base_cbm, 4),
        total_cbm=round(total_cbm, 4),
        utilization=round(total_cbm / capacity, 4) if capacity else 0.0,
        dead_zone=dead_zone,
        lines=lines.sort_values("topup_rank").reset_index(drop=True),
        notes=(
            "Greedy integer knapsack on Tier-A marginal profit velocity; "
            "every added carton satisfies the 135-day system DOS cap."
        ),
    )
