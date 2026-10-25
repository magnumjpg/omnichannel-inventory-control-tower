"""Live-formula executive workbook (six tabs, Excel 365).

The workbook is deliberately *not* a static report.  Every derived cell on
tabs 02-05 is an Excel formula (``LET``, ``XLOOKUP``, ``SUMIFS``,
``CEILING.MATH`` ...), so an S&OP analyst can audit, override and re-calculate
the plan without re-running Python.  Python writes only the raw snapshots and
the final container allocation, exactly as a PowerQuery refresh would.

Design notes
------------
* Rows 5-164 on every operational tab map 1:1 to the 160 child SKUs in
  ``tbl_Catalog`` (rows 16-175).  This row alignment keeps the sister-SKU
  ``SUMPRODUCT`` and cross-tab links auditable.
* Future functions are stored with the OOXML ``_xlfn.`` prefix.  Excel 365
  displays them normally; openpyxl therefore keeps the file schema-valid.
  ``LET`` parameters additionally carry the ``_xlpm.`` marker: Excel writes
  ``_xlfn.LET(_xlpm.x,1,_xlpm.x+1)``.  Omitting it makes Excel repair the file
  and delete the formula (verified on Excel 16.0 build 20527).
* ``fullCalcOnLoad`` is enabled so the workbook recalculates on open.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from src import config
from src.engine.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Palette / typography
# ---------------------------------------------------------------------------
NAVY = "1F3864"
BLUE = "2F5597"
MID_BLUE = "8EA9DB"
PALE_BLUE = "D9E2F3"
LIGHT_GREY = "F2F2F2"
MID_GREY = "BFBFBF"
DARK_GREY = "404040"
WHITE = "FFFFFF"
GREEN_FILL = "E6F4EA"
GREEN_TEXT = "137333"
AMBER_FILL = "FEF7E0"
AMBER_TEXT = "B06000"
RED_FILL = "FCE8E6"
RED_TEXT = "C5221F"
PURPLE_FILL = "F3E8FD"
PURPLE_TEXT = "681DA8"

TITLE_FONT = Font(name="Calibri", size=16, bold=True, color=WHITE)
SUBTITLE_FONT = Font(name="Calibri", size=10, italic=True, color=WHITE)
SECTION_FONT = Font(name="Calibri", size=11, bold=True, color=WHITE)
HEADER_FONT = Font(name="Calibri", size=9, bold=True, color=WHITE)
BODY_FONT = Font(name="Calibri", size=9, color=DARK_GREY)
BOLD_FONT = Font(name="Calibri", size=9, bold=True, color=DARK_GREY)
SMALL_FONT = Font(name="Calibri", size=8, color="7F7F7F")

THIN_GREY = Side(style="thin", color=MID_GREY)
MEDIUM_NAVY = Side(style="medium", color=NAVY)
GRID_BORDER = Border(left=THIN_GREY, right=THIN_GREY, top=THIN_GREY, bottom=THIN_GREY)

CURRENCY_FMT = '"$"#,##0.00'
CURRENCY_0_FMT = '"$"#,##0'
INT_FMT = "#,##0"
DEC2_FMT = "#,##0.00"
DEC3_FMT = "0.000"
DEC4_FMT = "0.0000"
PCT1_FMT = "0.0%"
PCT2_FMT = "0.00%"
DATE_FMT = "yyyy-mm-dd"

SHEETS = {
    "exec": "00_Exec_Control_Tower",
    "catalog": "01_Catalog_&_Parameters",
    "demand": "02_Demand_Unconstraining",
    "fba": "03_FBA_Nivel1_Transfer",
    "factory": "04_Factory_Nivel2_PO_&_CBM",
    "fee": "05_Fee_Risk_Audit",
}

_QL = {k: f"'{v}'" for k, v in SHEETS.items()}
_OPER_ROWS = (5, 164)
_CAT_ROWS = (16, 175)
_CAT_HEADER_ROW = 15


# ---------------------------------------------------------------------------
# Excel 365 future-function prefixing
# ---------------------------------------------------------------------------
_FUTURE_FUNCTIONS = ("CEILING.MATH", "FLOOR.MATH", "XLOOKUP", "LET", "IFS")


def _fx(formula: str) -> str:
    """Prefix post-2007 functions with the OOXML ``_xlfn.`` marker."""

    out = formula
    for fn in _FUTURE_FUNCTIONS:
        pattern = rf"(?<![A-Za-z0-9_.]){re.escape(fn)}\("
        out = re.sub(pattern, f"_xlfn.{fn}(", out)
    return out


def _set(ws, coord: str, value: Any, *, font=BODY_FONT, fill=None,
         fmt: str | None = None, align: str | None = None,
         border=GRID_BORDER, wrap: bool = False) -> None:
    cell = ws[coord]
    if isinstance(value, str) and value.startswith("="):
        cell.value = _fx(value)
    else:
        cell.value = _py(value)
    cell.font = font
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    if fmt:
        cell.number_format = fmt
    if align or wrap:
        cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    else:
        cell.alignment = Alignment(vertical="center")
    if border:
        cell.border = border


def _py(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    if value is None:
        return None
    if pd.isna(value):
        return None
    return value


def _merge_banner(ws, row: int, ncols: int, text: str, *, height: int = 28) -> None:
    last = get_column_letter(ncols)
    ws.merge_cells(f"A{row}:{last}{row}")
    c = ws[f"A{row}"]
    c.value = text
    c.font = TITLE_FONT
    c.fill = PatternFill("solid", fgColor=NAVY)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = height
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=NAVY)


def _merge_subtitle(ws, row: int, ncols: int, text: str) -> None:
    last = get_column_letter(ncols)
    ws.merge_cells(f"A{row}:{last}{row}")
    c = ws[f"A{row}"]
    c.value = text
    c.font = SUBTITLE_FONT
    c.fill = PatternFill("solid", fgColor=BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = 20
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=BLUE)


def _section(ws, row: int, ncols: int, text: str) -> None:
    last = get_column_letter(ncols)
    ws.merge_cells(f"A{row}:{last}{row}")
    c = ws[f"A{row}"]
    c.value = text
    c.font = SECTION_FONT
    c.fill = PatternFill("solid", fgColor=BLUE)
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[row].height = 22
    for col in range(1, ncols + 1):
        ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor=BLUE)


def _header_row(ws, row: int, headers: Iterable[str]) -> None:
    for idx, label in enumerate(headers, start=1):
        c = ws.cell(row=row, column=idx, value=label)
        c.font = HEADER_FONT
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(left=THIN_GREY, right=THIN_GREY, bottom=MEDIUM_NAVY)
    ws.row_dimensions[row].height = 38


def _style_band(ws, first_row: int, last_row: int, ncols: int) -> None:
    """Thin borders and zebra banding for non-table regions."""

    for r in range(first_row, last_row + 1):
        for c in range(1, ncols + 1):
            cell = ws.cell(row=r, column=c)
            cell.border = GRID_BORDER
            if cell.font is None or cell.font.name != "Calibri":
                cell.font = BODY_FONT
            if (r - first_row) % 2 == 1:
                cell.fill = PatternFill("solid", fgColor=LIGHT_GREY)


def _widths(ws, mapping: dict[str, float]) -> None:
    for col, width in mapping.items():
        ws.column_dimensions[col].width = width


def _add_table(ws, name: str, ref: str, style: str = "TableStyleMedium2") -> None:
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(
        name=style,
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def _page_setup(ws) -> None:
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True


# ---------------------------------------------------------------------------
# Data preparation (raw inputs aligned to catalogue order)
# ---------------------------------------------------------------------------
def _prepare(result: dict[str, Any]) -> dict[str, pd.DataFrame]:
    feeds = result["feeds"]
    catalog = feeds["catalog_master"].copy().reset_index(drop=True)
    catalog["_order"] = np.arange(len(catalog))

    demand = result["demand"].copy()
    fba_plan = result["fba_plan"].copy()
    fee = result["fee_audit"].copy()
    factory = result["factory_plan"]
    container = result["container_plan"]

    daily = feeds["daily_sales_history"].copy()
    daily["activity_date"] = pd.to_datetime(daily["activity_date"])
    all_dates = np.sort(daily["activity_date"].unique())
    last30 = set(all_dates[-30:])
    last30_rows = daily[daily["activity_date"].isin(last30)]
    sales = (
        last30_rows.groupby(["sku_id", "channel"])["observed_sales"]
        .sum()
        .unstack(fill_value=0)
        .reset_index()
        .rename(
            columns={
                "AMZ_US": "sales_30d_fba",
                "SHOPIFY_DTC": "sales_30d_dtc",
            }
        )
    )
    for col in ("sales_30d_fba", "sales_30d_dtc"):
        if col not in sales.columns:
            sales[col] = 0.0

    demand = demand.merge(
        sales[["sku_id", "sales_30d_fba", "sales_30d_dtc"]], on="sku_id", how="left"
    ).fillna({"sales_30d_fba": 0.0, "sales_30d_dtc": 0.0})

    fba = feeds["amazon_fba_inventory"].copy()
    fba = fba_plan.merge(
        fba[
            [
                "sku_id", "inv_age_0_90", "inv_age_91_180",
                "inv_age_181_270", "inv_age_271_365", "inv_age_365_plus",
            ]
        ],
        on="sku_id",
        how="left",
    ).fillna(0)

    all_lines = (
        factory.all_lines.copy()
        if factory.all_lines is not None
        else factory.lines.copy()
    )
    factory_sku = all_lines[
        [
            "sku_id", "base_units", "deferred_cartons", "pulled_forward",
            "dos_cap_exception",
        ]
    ].copy()

    cont = container.lines.copy()
    if cont.empty:
        cont = pd.DataFrame(
            columns=[
                "sku_id", "base_units", "topup_cartons", "topup_units",
                "total_cartons", "total_units",
            ]
        )
    cont_sku = cont[
        ["sku_id", "topup_cartons", "topup_units", "total_units"]
    ].copy()

    open_pos = (
        feeds["open_purchase_orders"]
        .groupby("sku_id", as_index=False)["open_units"]
        .sum()
    )

    def aligned(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        keep = ["sku_id", "_order"] + [c for c in columns if c in frame.columns]
        return catalog[keep].merge(frame[columns + ["sku_id"]], on="sku_id", how="left")

    demand = demand.merge(catalog[["sku_id", "_order"]], on="sku_id", how="right")
    demand = demand.sort_values("_order").reset_index(drop=True)

    fba = fba.merge(catalog[["sku_id", "_order"]], on="sku_id", how="right")
    fba = fba.sort_values("_order").reset_index(drop=True)

    fee = fee.merge(catalog[["sku_id", "_order"]], on="sku_id", how="right")
    fee = fee.sort_values("_order").reset_index(drop=True)

    factory_frame = catalog[
        ["sku_id", "_order", "parent_sku_id", "abc_xyz_tier", "master_carton_qty",
         "carton_volume_cbm", "sku_moq_units", "z_score"]
    ].merge(
        all_lines[
            ["sku_id", "ip_sys", "days_to_breach"]
        ],
        on="sku_id",
        how="left",
    )
    factory_frame = factory_frame.merge(
        feeds["warehouse_3pl_stock"][
            ["sku_id", "on_hand_units", "allocated_dtc_units"]
        ],
        on="sku_id",
        how="left",
    )
    factory_frame = factory_frame.merge(factory_sku, on="sku_id", how="left")
    factory_frame = factory_frame.merge(cont_sku, on="sku_id", how="left")
    factory_frame = factory_frame.merge(open_pos, on="sku_id", how="left")
    factory_frame = factory_frame.sort_values("_order").reset_index(drop=True)
    for col in (
        "ip_sys", "days_to_breach", "on_hand_units", "allocated_dtc_units",
        "base_units", "deferred_cartons", "topup_cartons", "topup_units",
        "total_units", "open_units",
    ):
        if col not in factory_frame.columns:
            factory_frame[col] = 0
        factory_frame[col] = factory_frame[col].fillna(0)
    factory_frame["pulled_forward"] = factory_frame["pulled_forward"].fillna(False)
    factory_frame["dos_cap_exception"] = factory_frame["dos_cap_exception"].fillna(False)

    return {
        "catalog": catalog,
        "demand": demand.fillna(0),
        "fba": fba.fillna(0),
        "factory": factory_frame,
        "fee": fee.fillna(0),
        "summary": result["summary"],
    }


# ---------------------------------------------------------------------------
# Sheet 01 - catalogue + policy parameters
# ---------------------------------------------------------------------------
def _build_catalog_sheet(wb: Workbook, catalog: pd.DataFrame, summary: dict) -> None:
    ws = wb.create_sheet(SHEETS["catalog"])
    ws.sheet_properties.tabColor = BLUE
    _page_setup(ws)
    _merge_banner(ws, 1, 24, "01 · CATALOGUE MASTER & GLOBAL POLICY PARAMETERS")
    _merge_subtitle(
        ws, 2, 24,
        f"Snapshot {summary.get('snapshot_date', config.SNAPSHOT_DATE)} · "
        f"{len(catalog)} child FNSKUs · 6 product families · "
        "all downstream formulas reference these control cells",
    )

    params = [
        ("A3", "L1_Mean", "Nivel 1 mean lead time (3PL → FBA), days", config.L1_MEAN_DAYS, INT_FMT),
        ("A4", "L1_Std", "Nivel 1 lead-time std deviation, days", config.L1_STD_DAYS, INT_FMT),
        ("A5", "L2_Mean", "Nivel 2 mean lead time (Factory → 3PL), days", config.L2_MEAN_DAYS, INT_FMT),
        ("A6", "L2_Std", "Nivel 2 lead-time std deviation, days", config.L2_STD_DAYS, INT_FMT),
        ("A7", "Shield_Days", "Low-inventory fee shield, days", config.FBA_SHIELD_DAYS, INT_FMT),
        ("A8", "Review_Days", "FBA transfer review cycle, days", config.FBA_REVIEW_CYCLE_DAYS, INT_FMT),
        ("A9", "Age_Cap", "FBA age-safe cap after transfer, days", config.FBA_AGE_CAP_DAYS, INT_FMT),
        ("A10", "CBM_20GP", "20' GP usable capacity, CBM", config.CONTAINER_20GP_CBM, DEC2_FMT),
        ("A11", "CBM_40HC", "40' HC usable capacity, CBM", config.CONTAINER_40HC_CBM, DEC2_FMT),
        ("A12", "DOS_Cap", "System DOS cap for base + top-up, days", config.SYSTEM_DOS_CAP_DAYS, INT_FMT),
    ]
    for coord, _name, label, value, fmt in params:
        row = int(coord[1:])
        _set(ws, f"A{row}", label, font=BOLD_FONT, fill=LIGHT_GREY)
        ws.merge_cells(f"A{row}:A{row}")
        _set(ws, f"B{row}", value, font=BOLD_FONT, fill=PALE_BLUE, fmt=fmt, align="center")

    params2 = [
        ("D3", "Kappa_Cred", "Credibility kappa (James-Stein)", config.KAPPA_CREDIBILITY, DEC2_FMT),
        ("D4", "Gamma_Cannib", "Substitution spillover gamma", config.GAMMA_CANNIBALIZATION, PCT1_FMT),
        ("D5", "Fee_Threshold", "Low-inventory HDoS threshold, days", config.LOW_INVENTORY_FEE_THRESHOLD_DAYS, INT_FMT),
        ("D6", "Fee_Min_Weekly", "Fee exemption weekly unit floor", config.LOW_INVENTORY_FEE_MIN_WEEKLY_UNITS, INT_FMT),
        ("D7", "Removal_Fee", "FBA removal fee per unit, USD", config.REMOVAL_FEE_PER_UNIT_USD, CURRENCY_FMT),
        ("D8", "AIS_Payback", "AIS removal payback hurdle, months", config.AIS_PAYBACK_MONTHS, INT_FMT),
        ("D9", "Aggregate_MOQ", "Aggregate PO MOQ, units", config.DEFAULT_PO_AGGREGATE_MOQ_UNITS, INT_FMT),
        ("D10", "LCL_Breakeven", "LCL / FCL breakeven, CBM", config.LCL_FCL_BREAKEVEN_CBM, DEC2_FMT),
        ("D11", "TopUp_Reserve", "Tier-A top-up reserve, CBM", config.TOPUP_RESERVE_CBM, DEC2_FMT),
        ("D12", "Hero_Weekly", "Hero weekly velocity floor, units", config.HERO_WEEKLY_UNIT_THRESHOLD, INT_FMT),
    ]
    for coord, _name, label, value, fmt in params2:
        row = int(coord[1:])
        ws.merge_cells(f"D{row}:E{row}")
        _set(ws, f"D{row}", label, font=BOLD_FONT, fill=LIGHT_GREY)
        _set(ws, f"F{row}", value, font=BOLD_FONT, fill=PALE_BLUE, fmt=fmt, align="center")
        for col in (4, 5):
            cell = ws.cell(row=row, column=col)
            cell.fill = PatternFill("solid", fgColor=LIGHT_GREY)
            cell.border = GRID_BORDER

    _set(ws, "A13", "Snapshot date", font=BOLD_FONT, fill=LIGHT_GREY)
    _set(ws, "B13", summary.get("snapshot_date", str(config.SNAPSHOT_DATE)), font=BOLD_FONT,
         fill=PALE_BLUE, align="center")
    ws.merge_cells("D13:X13")
    _set(
        ws, "D13",
        "Blue cells are policy controls. Change a value here and every tab recalculates. "
        "The catalogue table below is the single source of truth for carton physics, MOQs and unit volume.",
        font=SMALL_FONT, fill=None, border=None, wrap=True,
    )
    ws.row_dimensions[13].height = 30

    headers = [
        "sku_id", "parent_sku_id", "asin", "fnsku", "shopify_variant_id", "material",
        "size_width", "color", "hardware", "abc_xyz_tier", "z_score",
        "master_carton_qty", "carton_volume_cbm", "carton_weight_kg",
        "sku_moq_units", "po_aggregate_moq_units", "fob_unit_cost", "landed_cost",
        "retail_price", "parent_stationary_share", "unit_volume_cuft",
        "fba_channel_share", "fba_size_tier", "supplier_id",
    ]
    _header_row(ws, _CAT_HEADER_ROW, headers)

    formats = {
        "z_score": DEC3_FMT,
        "carton_volume_cbm": DEC4_FMT,
        "carton_weight_kg": DEC2_FMT,
        "fob_unit_cost": CURRENCY_FMT,
        "landed_cost": CURRENCY_FMT,
        "retail_price": CURRENCY_FMT,
        "parent_stationary_share": "0.000000",
        "unit_volume_cuft": "0.00000",
        "fba_channel_share": PCT1_FMT,
        "shopify_variant_id": "0",
    }
    for r, (_, row) in enumerate(catalog.iterrows(), start=_CAT_ROWS[0]):
        for c, col in enumerate(headers, start=1):
            value = row[col]
            if col in formats:
                value = _py(value)
            elif isinstance(value, (np.integer, np.floating)):
                value = _py(value)
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = BODY_FONT
            cell.border = GRID_BORDER
            cell.alignment = Alignment(vertical="center")
            if col in formats:
                cell.number_format = formats[col]

    _add_table(ws, "tbl_Catalog", f"A{_CAT_HEADER_ROW}:X{_CAT_ROWS[1]}", "TableStyleMedium2")

    _widths(
        ws,
        {
            "A": 30, "B": 22, "C": 14, "D": 14, "E": 17, "F": 30, "G": 10,
            "H": 10, "I": 12, "J": 9, "K": 8, "L": 11, "M": 11, "N": 11,
            "O": 10, "P": 12, "Q": 10, "R": 10, "S": 10, "T": 12, "U": 11,
            "V": 11, "W": 14, "X": 14,
        },
    )
    ws.freeze_panes = f"A{_CAT_ROWS[0]}"

    # Named controls -----------------------------------------------------
    names = {
        "L1_Mean": "$B$3", "L1_Std": "$B$4", "L2_Mean": "$B$5", "L2_Std": "$B$6",
        "Shield_Days": "$B$7", "Review_Days": "$B$8", "Age_Cap": "$B$9",
        "CBM_20GP": "$B$10", "CBM_40HC": "$B$11", "DOS_Cap": "$B$12",
        "Snapshot_Date": "$B$13",
        "Kappa_Cred": "$F$3", "Gamma_Cannib": "$F$4", "Fee_Threshold": "$F$5",
        "Fee_Min_Weekly": "$F$6", "Removal_Fee": "$F$7", "AIS_Payback": "$F$8",
        "Aggregate_MOQ": "$F$9", "LCL_Breakeven": "$F$10",
        "TopUp_Reserve": "$F$11", "Hero_Weekly": "$F$12",
    }
    for name, ref in names.items():
        wb.defined_names.add(
            DefinedName(name, attr_text=f"'{SHEETS['catalog']}'!{ref}")
        )


# ---------------------------------------------------------------------------
# Sheet 02 - demand unconstraining
# ---------------------------------------------------------------------------
def _build_demand_sheet(wb: Workbook, demand: pd.DataFrame, summary: dict) -> None:
    ws = wb.create_sheet(SHEETS["demand"])
    ws.sheet_properties.tabColor = "4472C4"
    _page_setup(ws)
    _merge_banner(ws, 1, 25, "02 · DEMAND UNCONSTRAINING · CENSORED → LATENT VELOCITY")
    _merge_subtitle(
        ws, 2, 25,
        "Recovery ladder: active-day exposure τ · sister-SKU imputation with γ=10% · "
        "90-day and lifetime fallbacks.  K takes H when τ>0, else Y, else J.  "
        "The κ=7 blend is a rejected diagnostic, not the shipped rule.  "
        "Red rows have <20 active days in the 30-day window.",
    )
    headers = [
        "sku_id", "parent_sku_id", "tier", "FBA share", "FBA sales 30d",
        "Active days 30d", "Naive 30d u/d", "Active-day u/d", "90d baseline u/d",
        "Cross-sectional u/d", "D_FBA unconstrained", "σ_FBA",
        "DTC sales 30d", "DTC active days", "DTC naive u/d", "DTC active u/d",
        "D_DTC unconstrained", "D_Global unconstrained", "σ_Global",
        "Stockout ratio", "Unconstraining uplift", "Exposure ratio",
        "Stockout days 30d", "Alert", "Fallback pool u/d",
    ]
    _header_row(ws, 4, headers)

    for r in range(_OPER_ROWS[0], _OPER_ROWS[1] + 1):
        d = demand.iloc[r - _OPER_ROWS[0]]
        _set(ws, f"A{r}", d["sku_id"], font=BOLD_FONT)
        _set(ws, f"B{r}", d["parent_sku_id"])
        _set(ws, f"C{r}", d["abc_xyz_tier"], align="center")
        _set(ws, f"D{r}", float(d["fba_channel_share"]), fmt=PCT1_FMT, align="center")
        _set(ws, f"E{r}", float(d["sales_30d_fba"]), fmt=DEC2_FMT)
        _set(ws, f"F{r}", float(d["active_days_30d"]), fmt=DEC2_FMT)
        _set(ws, f"G{r}", f"=E{r}/30", fmt=DEC4_FMT)
        # H, J and K are engine outputs, not Excel re-derivations: the
        # exposure-weighted active-day rate and the daily sister imputation
        # cannot be reproduced in a cell without a 30-day x 160-SKU block.
        _set(ws, f"H{r}", float(d["mu_active"]), fmt=DEC4_FMT)
        _set(ws, f"I{r}", float(d["baseline_90d"]), fmt=DEC4_FMT)
        _set(ws, f"J{r}", float(d["mu_cs"]), fmt=DEC4_FMT)
        _set(
            ws, f"K{r}",
            float(d["mu_unconstrained"]),
            font=BOLD_FONT, fill=GREEN_FILL, fmt=DEC4_FMT,
        )
        _set(ws, f"L{r}", float(d["sigma_unconstrained"]), fmt=DEC4_FMT)
        _set(ws, f"M{r}", float(d["sales_30d_dtc"]), fmt=DEC2_FMT)
        _set(ws, f"N{r}", float(d["active_days_30d_dtc"]), fmt=DEC2_FMT)
        _set(ws, f"O{r}", f"=M{r}/30", fmt=DEC4_FMT)
        _set(ws, f"P{r}", float(d["mu_active_dtc"]), fmt=DEC4_FMT)
        _set(ws, f"Q{r}", float(d["mu_dtc"]), fmt=DEC4_FMT)
        _set(ws, f"R{r}", float(d["mu_global"]), font=BOLD_FONT, fmt=DEC4_FMT)
        _set(ws, f"S{r}", float(d["sigma_global"]), fmt=DEC4_FMT)
        _set(ws, f"T{r}", f"=MAX(0,(30-F{r})/30)", fmt=PCT1_FMT)
        _set(ws, f"U{r}", f"=IF(G{r}>0,K{r}/G{r}-1,0)", fmt=PCT1_FMT)
        _set(ws, f"V{r}", f"=F{r}/30", fmt=PCT1_FMT)
        _set(ws, f"W{r}", f"=MAX(0,30-F{r})", fmt=DEC2_FMT, align="center")
        _set(
            ws, f"X{r}",
            f"=IF(F{r}<20,\"CENSORED - REVIEW ACTIVE DAY\",\"OK\")",
            align="center",
        )
        fallback = float(d["baseline_90d"])
        if fallback <= 0:
            fallback = float(d["baseline_365d"])
        _set(ws, f"Y{r}", fallback, fmt=DEC4_FMT)

    _add_table(ws, "tbl_Demand_Unconstraining", "A4:Y164", "TableStyleMedium2")
    _widths(
        ws,
        {
            "A": 28, "B": 20, "C": 8, "D": 9, "E": 13, "F": 11, "G": 11, "H": 12,
            "I": 13, "J": 14, "K": 16, "L": 10, "M": 13, "N": 11, "O": 11, "P": 12,
            "Q": 14, "R": 16, "S": 10, "T": 11, "U": 12, "V": 10, "W": 11, "X": 28,
            "Y": 15,
        },
    )
    ws.freeze_panes = "C5"

    ws.conditional_formatting.add(
        "F5:F164",
        FormulaRule(formula=["$F5<20"], fill=PatternFill("solid", fgColor=RED_FILL),
                    font=Font(color=RED_TEXT, bold=True), stopIfTrue=False),
    )
    ws.conditional_formatting.add(
        "U5:U164",
        CellIsRule(operator="greaterThan", formula=["0"],
                   fill=PatternFill("solid", fgColor=AMBER_FILL),
                   font=Font(color=AMBER_TEXT)),
    )
    ws.conditional_formatting.add(
        "K5:K164",
        DataBarRule(start_type="num", start_value=0, end_type="num", end_value=60,
                    color="70AD47", showValue=True),
    )


# ---------------------------------------------------------------------------
# Sheet 03 - FBA echelon
# ---------------------------------------------------------------------------
def _build_fba_sheet(wb: Workbook, fba: pd.DataFrame) -> None:
    ws = wb.create_sheet(SHEETS["fba"])
    ws.sheet_properties.tabColor = "548235"
    _page_setup(ws)
    _merge_banner(ws, 1, 28, "03 · FBA NIVEL 1 · 3PL → FBA TRANSFER & FEE SHIELD")
    _merge_subtitle(
        ws, 2, 28,
        "Effective HDoS = MAX(30d, 90d). B_FBA = MAX(SS1, 32·D_FBA) for non-exempt heroes. "
        "Transfers are age-safe master-carton multiples (120-day cap).",
    )
    headers = [
        "sku_id", "parent_sku_id", "tier", "FBA available", "IP_FBA",
        "HDoS 30d", "HDoS 90d", "Effective HDoS", "Fee / unit", "Fee exempt",
        "D_FBA u/d", "σ_FBA", "SS1", "B_FBA shield", "ROP1", "S_max1",
        "3PL cartons avail", "Age-safe cartons", "Transfer units", "Transfer cartons",
        "FC transfer", "Inbound shipped", "Inbound receiving", "Transfer CBM",
        "Post-transfer DOS", "Fee exposure", "Alert", "Carton check",
    ]
    _header_row(ws, 4, headers)

    for r in range(_OPER_ROWS[0], _OPER_ROWS[1] + 1):
        d = fba.iloc[r - _OPER_ROWS[0]]
        _set(ws, f"A{r}", d["sku_id"], font=BOLD_FONT)
        _set(ws, f"B{r}", d["parent_sku_id"])
        _set(ws, f"C{r}", d["abc_xyz_tier"], align="center")
        _set(ws, f"D{r}", int(d["fba_available"]), fmt=INT_FMT)
        _set(ws, f"E{r}", f"=D{r}+U{r}+V{r}+W{r}", font=BOLD_FONT, fmt=DEC2_FMT)
        _set(ws, f"F{r}", float(d["short_term_hdos_30d"]), fmt=DEC2_FMT)
        _set(ws, f"G{r}", float(d["long_term_hdos_90d"]), fmt=DEC2_FMT)
        _set(ws, f"H{r}", f"=MAX(F{r},G{r})", font=BOLD_FONT, fill=AMBER_FILL, fmt=DEC2_FMT)
        _set(
            ws, f"I{r}",
            float(d["low_inventory_fee_per_unit"]),
            fmt=CURRENCY_FMT,
        )
        _set(ws, f"J{r}", bool(d["low_inv_fee_exempt_flag"]), align="center")
        # Policy outputs (D_FBA, SS1, shield, ROP, S_max, target cartons) are
        # engine decisions.  The workbook keeps the arithmetic live: units,
        # CBM, exposure, alerts and all aggregates are formulas.
        _set(ws, f"K{r}", float(d["mu_unconstrained"]), font=BOLD_FONT, fmt=DEC4_FMT)
        _set(ws, f"L{r}", float(d["sigma_unconstrained"]), fmt=DEC4_FMT)
        _set(ws, f"M{r}", float(d["ss1"]), fmt=INT_FMT)
        _set(
            ws, f"N{r}",
            float(d["b_fba"]),
            font=BOLD_FONT, fill=GREEN_FILL, fmt=INT_FMT,
        )
        _set(ws, f"O{r}", float(d["rop1_shield"]), fmt=INT_FMT)
        _set(ws, f"P{r}", float(d["smax1"]), fmt=INT_FMT)
        _set(ws, f"Q{r}", int(d["tpl_cartons_available"]), fmt=INT_FMT)
        _set(
            ws, f"R{r}",
            int(d["target_cartons"]),
            fill=PALE_BLUE, fmt=INT_FMT,
        )
        _set(
            ws, f"S{r}",
            f"=MIN(R{r},Q{r})*XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty])",
            font=BOLD_FONT, fmt=INT_FMT,
        )
        _set(
            ws, f"T{r}",
            f"=IF(XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty])>0,"
            f"S{r}/XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty]),0)",
            fmt=INT_FMT,
        )
        _set(ws, f"U{r}", int(d["fba_fc_transfer"]), fmt=INT_FMT)
        _set(ws, f"V{r}", int(d["fba_inbound_shipped"]), fmt=INT_FMT)
        _set(ws, f"W{r}", int(d["fba_inbound_receiving"]), fmt=INT_FMT)
        _set(
            ws, f"X{r}",
            f"=T{r}*XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[carton_volume_cbm])",
            fmt=DEC4_FMT,
        )
        _set(ws, f"Y{r}", f"=IF(K{r}>0,(E{r}+S{r})/K{r},0)", fmt=DEC2_FMT)
        _set(ws, f"Z{r}", f"=S{r}*I{r}", font=BOLD_FONT, fill=RED_FILL, fmt=CURRENCY_FMT)
        _set(
            ws, f"AA{r}",
            f"=IF(AND(H{r}<Fee_Threshold,I{r}>0),\"LOW-INVENTORY FEE ACTIVE\","
            f"IF(H{r}<Fee_Threshold,\"EXEMPT / AUDIT\",\"SHIELDED\"))",
            align="center",
        )
        _set(
            ws, f"AB{r}",
            f"=IF(MOD(S{r},XLOOKUP($A{r},tbl_Catalog[sku_id],"
            f"tbl_Catalog[master_carton_qty]))=0,\"CARTON OK\",\"CHECK\")",
            align="center",
        )

    _add_table(ws, "tbl_FBA_Transfers", "A4:AB164", "TableStyleMedium4")
    _widths(
        ws,
        {
            "A": 28, "B": 20, "C": 8, "D": 11, "E": 10, "F": 9, "G": 9, "H": 11,
            "I": 9, "J": 9, "K": 10, "L": 8, "M": 8, "N": 10, "O": 8, "P": 9,
            "Q": 10, "R": 11, "S": 11, "T": 10, "U": 9, "V": 10, "W": 11,
            "X": 10, "Y": 11, "Z": 12, "AA": 25, "AB": 12,
        },
    )
    ws.freeze_panes = "C5"

    ws.conditional_formatting.add(
        "H5:I164",
        FormulaRule(formula=["AND($H5<28,$I5>0)"],
                    fill=PatternFill("solid", fgColor=AMBER_FILL),
                    font=Font(color=AMBER_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "AB5:AB164",
        FormulaRule(formula=["$AB5=\"CHECK\""],
                    fill=PatternFill("solid", fgColor=RED_FILL),
                    font=Font(color=RED_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "Y5:Y164",
        CellIsRule(operator="greaterThan", formula=["120"],
                   fill=PatternFill("solid", fgColor=RED_FILL),
                   font=Font(color=RED_TEXT, bold=True)),
    )


# ---------------------------------------------------------------------------
# Sheet 04 - factory echelon and container
# ---------------------------------------------------------------------------
def _build_factory_sheet(wb: Workbook, factory: pd.DataFrame, container: dict) -> None:
    ws = wb.create_sheet(SHEETS["factory"])
    ws.sheet_properties.tabColor = "BF8F00"
    _page_setup(ws)
    _merge_banner(ws, 1, 29, "04 · FACTORY NIVEL 2 · MOQ RECONCILIATION & CONTAINER KNAPSACK")
    _merge_subtitle(
        ws, 2, 29,
        f"Plan: {container.get('container_type', 'LCL_OCEAN')} · "
        f"base {container.get('base_cbm', 0):.3f} CBM + top-up {container.get('topup_cbm', 0):.3f} CBM "
        f"= {container.get('total_cbm', 0):.3f} / {container.get('capacity_cbm', 0):.1f} CBM. "
        "Base units are container-trimmed; tier-A top-up cartons are the greedy marginal-profit allocation.",
    )
    headers = [
        "sku_id", "parent_sku_id", "tier", "3PL on hand", "Allocated DTC",
        "IP_System", "D_Global u/d", "σ_Global", "Z", "SS2", "B_FBA",
        "ROP2 system", "S_max2", "MOQ cartons", "Days to breach",
        "Executable PO units", "Policy line CBM", "Base units", "Top-up cartons",
        "Top-up units", "Total units", "Total cartons", "Total CBM",
        "Status", "MOQ check", "DOS check", "Open PO units", "Deferred cartons",
        "Pulled forward",
    ]
    _header_row(ws, 4, headers)

    for r in range(_OPER_ROWS[0], _OPER_ROWS[1] + 1):
        d = factory.iloc[r - _OPER_ROWS[0]]
        _set(ws, f"A{r}", d["sku_id"], font=BOLD_FONT)
        _set(ws, f"B{r}", d["parent_sku_id"])
        _set(ws, f"C{r}", d["abc_xyz_tier"], align="center")
        _set(ws, f"D{r}", int(d["on_hand_units"]), fmt=INT_FMT)
        _set(ws, f"E{r}", int(d["allocated_dtc_units"]), fmt=INT_FMT)
        _set(ws, f"F{r}", f"='{SHEETS['fba']}'!E{r}+D{r}-E{r}", font=BOLD_FONT, fmt=DEC2_FMT)
        _set(ws, f"G{r}", f"='{SHEETS['demand']}'!R{r}", font=BOLD_FONT, fmt=DEC4_FMT)
        _set(ws, f"H{r}", f"='{SHEETS['demand']}'!S{r}", fmt=DEC4_FMT)
        _set(ws, f"I{r}", f"=XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[z_score])", fmt=DEC3_FMT)
        _set(
            ws, f"J{r}",
            f"=ROUNDUP(I{r}*SQRT(L2_Mean*(H{r}^2)+(G{r}^2)*(L2_Std^2)),0)",
            fmt=INT_FMT,
        )
        _set(ws, f"K{r}", f"='{SHEETS['fba']}'!N{r}", fmt=INT_FMT)
        _set(ws, f"L{r}", f"=CEILING.MATH(G{r}*L2_Mean+J{r}+K{r},1)", fmt=INT_FMT)
        _set(
            ws, f"M{r}",
            f"=CEILING.MATH(G{r}*(L2_Mean+45)+J{r}+K{r},1)",
            font=BOLD_FONT, fill=GREEN_FILL, fmt=INT_FMT,
        )
        _set(
            ws, f"N{r}",
            f"=CEILING.MATH(XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[sku_moq_units])"
            f"/XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty]),1)",
            fmt=INT_FMT,
        )
        _set(ws, f"O{r}", f"=IF(G{r}>0,(F{r}-L{r})/G{r},999)", fmt=DEC2_FMT)
        _set(
            ws, f"P{r}",
            (
                f"=LET(_xlpm.ip_sys,$F{r},_xlpm.rop_sys,$L{r},_xlpm.s_max,$M{r},"
                f"_xlpm.m_qty,XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty]),"
                f"_xlpm.moq,XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[sku_moq_units]),"
                f"IF(_xlpm.ip_sys<=_xlpm.rop_sys,"
                f"CEILING.MATH(MAX(_xlpm.s_max-_xlpm.ip_sys,_xlpm.moq),_xlpm.m_qty),0))"
            ),
            font=BOLD_FONT, fill=PALE_BLUE, fmt=INT_FMT,
        )
        _set(
            ws, f"Q{r}",
            f"=IF(P{r}>0,(P{r}/XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty]))"
            f"*XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[carton_volume_cbm]),0)",
            fmt=DEC4_FMT,
        )
        _set(ws, f"R{r}", int(d["base_units"]), fmt=INT_FMT)
        _set(ws, f"S{r}", int(d["topup_cartons"]), fmt=INT_FMT)
        _set(
            ws, f"T{r}",
            f"=S{r}*XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty])",
            fmt=INT_FMT,
        )
        _set(ws, f"U{r}", f"=R{r}+T{r}", font=BOLD_FONT, fmt=INT_FMT)
        _set(
            ws, f"V{r}",
            f"=IF(XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty])>0,"
            f"U{r}/XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[master_carton_qty]),0)",
            fmt=INT_FMT,
        )
        _set(
            ws, f"W{r}",
            f"=V{r}*XLOOKUP($A{r},tbl_Catalog[sku_id],tbl_Catalog[carton_volume_cbm])",
            font=BOLD_FONT, fill=GREEN_FILL, fmt=DEC4_FMT,
        )
        _set(
            ws, f"X{r}",
            f"=IF(U{r}=0,\"DEFERRED / NO PO\",IF(S{r}>0,\"BASE + TIER-A TOP-UP\",\"BASE LOAD\"))",
            align="center",
        )
        _set(
            ws, f"Y{r}",
            f"=IF(U{r}=0,\"N/A\",IF(AND(MOD(U{r},XLOOKUP($A{r},tbl_Catalog[sku_id],"
            f"tbl_Catalog[master_carton_qty]))=0,U{r}>=XLOOKUP($A{r},tbl_Catalog[sku_id],"
            f"tbl_Catalog[sku_moq_units])),\"MOQ OK\",\"CHECK\"))",
            align="center",
        )
        _set(
            ws, f"Z{r}",
            f"=IF(U{r}=0,\"N/A\",IF((F{r}+U{r})/MAX(0.1,G{r})<=DOS_Cap,\"DOS OK\",\"MOQ OVERRIDE\"))",
            align="center",
        )
        _set(ws, f"AA{r}", int(d["open_units"]), fmt=INT_FMT)
        _set(ws, f"AB{r}", int(d["deferred_cartons"]), fmt=INT_FMT)
        _set(ws, f"AC{r}", bool(d["pulled_forward"]), align="center")

    _add_table(ws, "tbl_Factory_PO", "A4:AC164", "TableStyleMedium6")
    _widths(
        ws,
        {
            "A": 28, "B": 20, "C": 8, "D": 11, "E": 10, "F": 11, "G": 11, "H": 9,
            "I": 8, "J": 7, "K": 8, "L": 11, "M": 10, "N": 9, "O": 10, "P": 13,
            "Q": 11, "R": 10, "S": 10, "T": 10, "U": 10, "V": 10, "W": 10,
            "X": 22, "Y": 10, "Z": 12, "AA": 10, "AB": 10, "AC": 10,
        },
    )
    ws.freeze_panes = "C5"

    ws.conditional_formatting.add(
        "Z5:Z164",
        FormulaRule(formula=["$Z5=\"MOQ OVERRIDE\""],
                    fill=PatternFill("solid", fgColor=AMBER_FILL),
                    font=Font(color=AMBER_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "X5:X164",
        FormulaRule(formula=["$X5=\"DEFERRED / NO PO\""],
                    font=Font(color="7F7F7F")),
    )
    ws.conditional_formatting.add(
        "Y5:Y164",
        FormulaRule(formula=["$Y5=\"CHECK\""],
                    fill=PatternFill("solid", fgColor=RED_FILL),
                    font=Font(color=RED_TEXT, bold=True)),
    )


# ---------------------------------------------------------------------------
# Sheet 05 - aged inventory / fee audit
# ---------------------------------------------------------------------------
def _build_fee_sheet(wb: Workbook, fee: pd.DataFrame) -> None:
    ws = wb.create_sheet(SHEETS["fee"])
    ws.sheet_properties.tabColor = "C00000"
    _page_setup(ws)
    _merge_banner(ws, 1, 24, "05 · FEE RISK AUDIT · PRE-15th AGED-CLIFF REMOVAL")
    _merge_subtitle(
        ws, 2, 24,
        "2026 Aged Inventory Surcharge by bucket, including the 271-day $5.45/ft³ cliff and "
        "$0.30/$0.35 unit floors. Removal is recommended when the $0.84/unit removal cost "
        "pays back within 12 months.",
    )
    headers = [
        "sku_id", "parent_sku_id", "tier", "Unit ft³", "0-90d", "91-180d",
        "181-270d", "271-365d", "366d+", "Effective HDoS", "Fee / unit",
        "AIS 181-270", "AIS 271-365", "AIS 366+", "Monthly AIS",
        "Removal units", "Removal cost", "Payback months", "Recommendation",
        "Annualised AIS", "Net annual saving", "Action window", "Units >180d", "Note",
    ]
    _header_row(ws, 4, headers)

    for r in range(_OPER_ROWS[0], _OPER_ROWS[1] + 1):
        d = fee.iloc[r - _OPER_ROWS[0]]
        _set(ws, f"A{r}", d["sku_id"], font=BOLD_FONT)
        _set(ws, f"B{r}", d["parent_sku_id"])
        _set(ws, f"C{r}", d["abc_xyz_tier"], align="center")
        _set(ws, f"D{r}", float(d["unit_volume_cuft"]), fmt="0.00000")
        _set(ws, f"E{r}", int(d["inv_age_0_90"]), fmt=INT_FMT)
        _set(ws, f"F{r}", int(d["inv_age_91_180"]), fmt=INT_FMT)
        _set(ws, f"G{r}", int(d["inv_age_181_270"]), fmt=INT_FMT)
        _set(ws, f"H{r}", int(d["inv_age_271_365"]), fmt=INT_FMT)
        _set(ws, f"I{r}", int(d["inv_age_365_plus"]), fmt=INT_FMT)
        _set(ws, f"J{r}", f"='{SHEETS['fba']}'!H{r}", fmt=DEC2_FMT)
        _set(ws, f"K{r}", f"='{SHEETS['fba']}'!I{r}", fmt=CURRENCY_FMT)
        _set(ws, f"L{r}", f"=G{r}*D{r}*1.00", fmt=CURRENCY_FMT)
        _set(ws, f"M{r}", f"=H{r}*D{r}*5.45", fmt=CURRENCY_FMT)
        _set(ws, f"N{r}", f"=I{r}*MAX(D{r}*6.90,0.30)", fmt=CURRENCY_FMT)
        _set(ws, f"O{r}", f"=SUM(L{r}:N{r})", font=BOLD_FONT, fill=RED_FILL, fmt=CURRENCY_FMT)
        _set(ws, f"P{r}", f"=SUM(G{r}:I{r})", fmt=INT_FMT)
        _set(ws, f"Q{r}", f"=P{r}*Removal_Fee", fmt=CURRENCY_FMT)
        _set(ws, f"R{r}", f"=IF(O{r}>0,Q{r}/O{r},\"\")", fmt=DEC2_FMT)
        _set(
            ws, f"S{r}",
            f"=IF(AND(P{r}>0,ISNUMBER(R{r}),R{r}<=AIS_Payback),\"EXECUTE REMOVAL\","
            f"IF(O{r}>0,\"DISCOUNT 25% NOW\",\"HEALTHY\"))",
            font=BOLD_FONT, align="center",
        )
        _set(ws, f"T{r}", f"=O{r}*12", fmt=CURRENCY_FMT)
        _set(ws, f"U{r}", f"=IF(S{r}=\"EXECUTE REMOVAL\",T{r}-Q{r},0)", fmt=CURRENCY_FMT)
        _set(
            ws, f"V{r}",
            f"=IF(AND(P{r}>0,O{r}>0),\"Before 14th 23:59 PT\",\"-\")",
            align="center",
        )
        _set(ws, f"W{r}", f"=SUM(G{r}:I{r})", fmt=INT_FMT)
        _set(
            ws, f"X{r}",
            f"=IF(P{r}=0,\"Clear\",IF(R{r}<=AIS_Payback,\"Remove before cliff\",\"Discount to accelerate FIFO\"))",
            font=SMALL_FONT,
        )

    _add_table(ws, "tbl_Fee_Audit", "A4:X164", "TableStyleMedium3")
    _widths(
        ws,
        {
            "A": 28, "B": 20, "C": 8, "D": 9, "E": 8, "F": 8, "G": 9, "H": 9,
            "I": 8, "J": 11, "K": 9, "L": 12, "M": 12, "N": 12, "O": 12, "P": 11,
            "Q": 11, "R": 11, "S": 22, "T": 13, "U": 13, "V": 20, "W": 10, "X": 26,
        },
    )
    ws.freeze_panes = "C5"

    ws.conditional_formatting.add(
        "G5:I164",
        FormulaRule(formula=["SUM($G5:$I5)>0"],
                    fill=PatternFill("solid", fgColor="FAD2CF"),
                    font=Font(color="A61C00", bold=True)),
    )
    ws.conditional_formatting.add(
        "S5:S164",
        FormulaRule(formula=["$S5=\"EXECUTE REMOVAL\""],
                    fill=PatternFill("solid", fgColor=RED_FILL),
                    font=Font(color=RED_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "S5:S164",
        FormulaRule(formula=["$S5=\"DISCOUNT 25% NOW\""],
                    fill=PatternFill("solid", fgColor=AMBER_FILL),
                    font=Font(color=AMBER_TEXT, bold=True)),
    )


# ---------------------------------------------------------------------------
# Sheet 00 - executive control tower
# ---------------------------------------------------------------------------
def _build_exec_sheet(wb: Workbook, summary: dict, container: dict) -> None:
    ws = wb.create_sheet(SHEETS["exec"], 0)
    ws.sheet_properties.tabColor = NAVY
    _page_setup(ws)
    _merge_banner(ws, 1, 10, "OMNICHANNEL INVENTORY CONTROL TOWER · WEEKLY S&OP")
    _merge_subtitle(
        ws, 2, 10,
        f"Snapshot {summary.get('snapshot_date', config.SNAPSHOT_DATE)} · "
        "Deterministic seed 42 · 160 child FNSKUs · 365-day history · "
        "sign-off package generated from live formulas on tabs 02-05",
    )

    _section(ws, 4, 10, "EXECUTIVE KPI CONTROL TOWER")
    for idx, label in enumerate(
        ["Metric", "Value", "Unit", "Source", "Threshold / policy", "Status"], start=1
    ):
        c = ws.cell(row=5, column=idx, value=label)
        c.font = HEADER_FONT
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = GRID_BORDER
    ws.row_dimensions[5].height = 26

    fba = _QL["fba"]
    fac = _QL["factory"]
    fee = _QL["fee"]
    dem = _QL["demand"]

    kpis: list[tuple[str, str, str, str, str, str, str]] = [
        ("Catalog child FNSKUs", "=COUNTA(tbl_Catalog[sku_id])", "SKUs",
         "Tab 01 · tbl_Catalog", "160 target", '=IF(B6=160,"OK","REVIEW")', INT_FMT),
        ("FBA available units", f"=SUM({fba}!$D$5:$D$164)", "units",
         "Tab 03 · Col D", "Physical stock", '=IF(B7>0,"OK","REVIEW")', INT_FMT),
        ("3PL on-hand units", f"=SUM({fac}!$D$5:$D$164)", "units",
         "Tab 04 · Col D", "Upstream pool", '=IF(B8>0,"OK","REVIEW")', INT_FMT),
        ("Open PO units", f"=SUM({fac}!$AA$5:$AA$164)", "units",
         "Tab 04 · Col AA", "In-transit / production", '=IF(B9>0,"OK","REVIEW")', INT_FMT),
        ("Inventory value at landed cost",
         f"=SUMPRODUCT(tbl_Catalog[landed_cost],({fba}!D5:D164+{fac}!D5:D164+{fac}!AA5:AA164))",
         "USD", "Catalog × system units", "Working-capital exposure",
         '=IF(B10>0,"OK","REVIEW")', CURRENCY_0_FMT),
        ("FNSKUs with 30d HDoS < 14d", f"=COUNTIF({fba}!$F$5:$F$164,\"<14\")", "SKUs",
         "Tab 03 · Col F", "Critical stockout band", '=IF(B11=0,"OK","CRITICAL")', INT_FMT),
        ("Non-exempt FNSKUs in 14-28d fee band",
         f"=COUNTIFS({fba}!$F$5:$F$164,\">=14\",{fba}!$G$5:$G$164,\"<28\","
         f"{fba}!$F$5:$F$164,\"<28\",{fba}!$J$5:$J$164,FALSE)",
         "SKUs", "Tab 03 · Col F/G/J", "30-day window; charge model uses max(30d, 90d)",
         '=IF(B12=0,"OK","ACTION")', INT_FMT),
        ("30-day low-inventory fee exposure", f"=SUM({fba}!$Z$5:$Z$164)", "USD",
         "Tab 03 · Col Z", "$0.32 / $0.63 / $0.89 per unit",
         '=IF(B13=0,"OK","ACTION")', CURRENCY_FMT),
        ("3PL → FBA transfer cartons", f"=SUM({fba}!$T$5:$T$164)", "cartons",
         "Tab 03 · Col T", "Master-carton multiples",
         '=IF(B14>0,"READY","REVIEW")', INT_FMT),
        ("3PL → FBA transfer units", f"=SUM({fba}!$S$5:$S$164)", "units",
         "Tab 03 · Col S", "Age-safe ≤120d", '=IF(B15>0,"READY","REVIEW")', INT_FMT),
        ("Factory PO units", f"=SUM({fac}!$U$5:$U$164)", "units",
         "Tab 04 · Col U", "Aggregate MOQ ≥5,000",
         '=IF(B16>=Aggregate_MOQ,"MET","GAP")', INT_FMT),
        ("Factory PO cartons", f"=SUM({fac}!$V$5:$V$164)", "cartons",
         "Tab 04 · Col V", "Integer master cartons", '=IF(B17>0,"READY","REVIEW")', INT_FMT),
        ("Container volume", f"=SUM({fac}!$W$5:$W$164)", "CBM",
         "Tab 04 · Col W", "20' GP = 28.8 CBM", '=IF(B18>=LCL_Breakeven,"FCL","LCL")', DEC3_FMT),
        ("Container utilisation", "=IF(CBM_20GP>0,B18/CBM_20GP,0)", "%",
         "CBM / 28.8", "≥95% optimal fill", '=IF(B19>=0.95,"OPTIMAL","DEAD ZONE")', PCT1_FMT),
        ("Aggregate PO MOQ", '=IF(B16>=Aggregate_MOQ,"MET","GAP: "&TEXT(Aggregate_MOQ-B16,"#,##0")&" u")',
         "status", "Tab 04 · Col U", "5,000-unit floor", '=IF(B16>=Aggregate_MOQ,"MET","GAP")', None),
        ("Projected monthly AIS", f"=SUM({fee}!$O$5:$O$164)", "USD",
         "Tab 05 · Col O", "Aged inventory surcharge", '=IF(B21>0,"MONITOR","OK")', CURRENCY_FMT),
        ("Removal recommendations", f"=COUNTIF({fee}!$S$5:$S$164,\"EXECUTE REMOVAL\")", "SKUs",
         "Tab 05 · Col S", "Payback ≤12 months", '=IF(B22>0,"ACTION","OK")', INT_FMT),
        ("Annualised AIS exposure", f"=SUM({fee}!$T$5:$T$164)", "USD",
         "Tab 05 · Col T", "12 × monthly AIS", '=IF(B23>0,"MONITOR","OK")', CURRENCY_0_FMT),
        ("Combined fee + AIS exposure", "=B13+B21", "USD",
         "Tabs 03 + 05", "Weekly + monthly leakage", '=IF(B24>0,"MONITOR","OK")', CURRENCY_FMT),
    ]

    for offset, (label, formula, unit, source, policy, status, fmt) in enumerate(kpis):
        r = 6 + offset
        _set(ws, f"A{r}", label, font=BOLD_FONT, fill=LIGHT_GREY)
        _set(ws, f"B{r}", formula, font=BOLD_FONT,
             fill=(GREEN_FILL if offset < 5 else PALE_BLUE), fmt=fmt, align="right")
        _set(ws, f"C{r}", unit, align="center")
        _set(ws, f"D{r}", source, font=SMALL_FONT)
        _set(ws, f"E{r}", policy, font=SMALL_FONT)
        _set(ws, f"F{r}", status, font=BOLD_FONT, align="center")

    _section(ws, 26, 10, "EXCEPTION MATRIX · WHAT REQUIRES A DECISION THIS WEEK")
    matrix_headers = ["Severity", "Domain", "Exception", "Count", "Owner", "Required action"]
    for idx, label in enumerate(matrix_headers, start=1):
        c = ws.cell(row=27, column=idx, value=label)
        c.font = HEADER_FONT
        c.fill = PatternFill("solid", fgColor=BLUE)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = GRID_BORDER
    ws.row_dimensions[27].height = 24

    exceptions = [
        ("CRITICAL", "Demand", "FNSKUs with HDoS < 14 days", "=B11",
         "Inventory Ops", "Expedite SPD / release 3PL cartons before fee lock"),
        ("HIGH", "Fee", "Non-exempt FNSKUs in 14-28d band", "=B12",
         "Finance + Ops", "Transfer now; do not wait for the next Monday cycle"),
        ("HIGH", "Aging", "Units >180 days at FBA", f"=SUM({fee}!$W$5:$W$164)",
         "Compliance", "Removal or 25% markdown before the 14th, 23:59 PT"),
        ("MEDIUM", "Container", "Factory cartons deferred to next cycle", f"=SUM({fac}!$AB$5:$AB$164)",
         "Logistics", "Roll into next Monday's 20' GP allocation"),
        ("MEDIUM", "MOQ", "Lines with MOQ-driven DOS override", f"=COUNTIF({fac}!$Z$5:$Z$164,\"MOQ OVERRIDE\")",
         "Procurement", "Negotiate lower MOQ or split production lots"),
        ("LOW", "Analytics", "FNSKUs with positive unconstraining uplift", f"=COUNTIF({dem}!$U$5:$U$164,\">0\")",
         "Demand Planning", "Audit censored-demand assumptions in Tab 02"),
    ]
    severity_fill = {"CRITICAL": RED_FILL, "HIGH": AMBER_FILL, "MEDIUM": PURPLE_FILL, "LOW": GREEN_FILL}
    for offset, (severity, domain, exception, count, owner, action) in enumerate(exceptions):
        r = 28 + offset
        _set(ws, f"A{r}", severity, font=BOLD_FONT, fill=severity_fill[severity], align="center")
        _set(ws, f"B{r}", domain, align="center")
        _set(ws, f"C{r}", exception, font=BODY_FONT)
        _set(ws, f"D{r}", count, font=BOLD_FONT, fmt=INT_FMT, align="center")
        _set(ws, f"E{r}", owner)
        _set(ws, f"F{r}", action, wrap=True)
        ws.row_dimensions[r].height = 30

    _section(ws, 35, 10, "CONTAINER FILL GAUGE")
    ws.merge_cells("A36:J36")
    _set(
        ws, "A36",
        '="["&REPT("█",ROUND(B19*20,0))&REPT("·",20-ROUND(B19*20,0))&"]  "&TEXT(B19,"0.0%")'
        '&"  ·  "&TEXT(B18,"0.000")&" / "&TEXT(CBM_20GP,"0.0")&" CBM"',
        font=Font(name="Consolas", size=12, bold=True, color=NAVY),
        fill=PALE_BLUE, align="left",
    )
    ws.row_dimensions[36].height = 28
    ws.merge_cells("A37:J37")
    _set(
        ws, "A37",
        '=IF(AND(B18>=LCL_Breakeven,MOD(B18,CBM_20GP)<24),"DEAD ZONE → EXECUTE TIER-A KNAPSACK TOP-UP",'
        '"OPTIMAL FILL → BOOK 20\' GP FCL AND TRANSMIT PO")',
        font=BOLD_FONT, fill=GREEN_FILL, align="center",
    )

    _section(ws, 39, 10, "S&OP GOVERNANCE & SIGN-OFF")
    signoff = [
        ("Prepared by", "Supply Chain Analytics · automated control tower", None),
        ("Snapshot date", "=Snapshot_Date", None),
        ("S&OP decision", '=IF(AND(B11<=5,B12<=5),"APPROVE WEEKLY PLAN","CONDITIONAL APPROVAL - REVIEW EXCEPTIONS")', None),
        ("Approved by", "", None),
        ("Approval date", "", None),
    ]
    for offset, (label, value, fmt) in enumerate(signoff):
        r = 40 + offset
        _set(ws, f"A{r}", label, font=BOLD_FONT, fill=LIGHT_GREY)
        ws.merge_cells(f"B{r}:D{r}")
        _set(ws, f"B{r}", value, font=BOLD_FONT, fill=PALE_BLUE, fmt=fmt)
        for c in range(2, 5):
            ws.cell(row=r, column=c).border = GRID_BORDER

    ws.merge_cells("F40:J44")
    _set(
        ws, "F40",
        "Governance: this workbook is the S&OP sign-off artifact for SOP-OPS-004. "
        "It is regenerated deterministically from seed 42. Any manual override must be "
        "recorded in the change log and re-validated against the Python quality gate "
        "(pytest) before release to the 3PL and factory.",
        font=SMALL_FONT, fill=LIGHT_GREY, wrap=True,
    )
    for r in range(40, 45):
        for c in range(6, 11):
            ws.cell(row=r, column=c).border = GRID_BORDER

    _widths(
        ws,
        {"A": 34, "B": 18, "C": 10, "D": 30, "E": 30, "F": 46,
         "G": 12, "H": 12, "I": 12, "J": 12},
    )
    ws.freeze_panes = "A6"

    # Conditional formatting on KPI status / values ----------------------
    ws.conditional_formatting.add(
        "B11",
        CellIsRule(operator="greaterThan", formula=["0"],
                   fill=PatternFill("solid", fgColor=RED_FILL),
                   font=Font(color=RED_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "B12:B13",
        CellIsRule(operator="greaterThan", formula=["0"],
                   fill=PatternFill("solid", fgColor=AMBER_FILL),
                   font=Font(color=AMBER_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "B19",
        DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1,
                    color="70AD47", showValue=True),
    )
    ws.conditional_formatting.add(
        "F6:F24",
        FormulaRule(formula=["OR($F6=\"CRITICAL\",$F6=\"ACTION\",$F6=\"DEAD ZONE\")"],
                    fill=PatternFill("solid", fgColor=RED_FILL),
                    font=Font(color=RED_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "F6:F24",
        FormulaRule(formula=["OR($F6=\"MONITOR\",$F6=\"REVIEW\",$F6=\"GAP\")"],
                    fill=PatternFill("solid", fgColor=AMBER_FILL),
                    font=Font(color=AMBER_TEXT, bold=True)),
    )
    ws.conditional_formatting.add(
        "F6:F24",
        FormulaRule(formula=["OR($F6=\"OK\",$F6=\"MET\",$F6=\"READY\",$F6=\"OPTIMAL\",$F6=\"FCL\")"],
                    fill=PatternFill("solid", fgColor=GREEN_FILL),
                    font=Font(color=GREEN_TEXT, bold=True)),
    )

    # Data validation for the sign-off decision cell.
    dv = DataValidation(
        type="list",
        formula1='"APPROVE WEEKLY PLAN,CONDITIONAL APPROVAL - REVIEW EXCEPTIONS,REJECT - REBASE PLAN"',
        allow_blank=True,
    )
    ws.add_data_validation(dv)
    dv.add("B42")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build_workbook(
    result: dict[str, Any] | None = None,
    output_path: Path | str | None = None,
) -> Path:
    """Build the six-tab workbook and return its path.

    Parameters
    ----------
    result:
        Output of :func:`src.engine.pipeline.run_pipeline`.  When omitted the
        pipeline is executed in-memory (no persistence side effects).
    output_path:
        Destination ``.xlsx``.  Defaults to
        ``outputs/Weekly_Operations_Replenishment_Model.xlsx``.
    """

    if result is None:
        result = run_pipeline(persist=False)

    tables = _prepare(result)
    summary = result["summary"]
    container = summary.get("container", {})
    if not container and "container_plan" in result:
        container = result["container_plan"].to_dict()

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.title = "Omnichannel Multi-Echelon Inventory Control Tower"
    wb.properties.subject = "Weekly S&OP replenishment, MOQ and container plan"
    wb.properties.creator = "Supply Chain Analytics"
    wb.properties.description = (
        "Deterministic seed 42 · live Excel 365 formulas · SOP-OPS-004 sign-off artifact"
    )
    wb.properties.keywords = "inventory; multi-echelon; unconstraining; FBA; MOQ; container"

    _build_exec_sheet(wb, summary, container)
    _build_catalog_sheet(wb, tables["catalog"], summary)
    _build_demand_sheet(wb, tables["demand"], summary)
    _build_fba_sheet(wb, tables["fba"])
    _build_factory_sheet(wb, tables["factory"], container)
    _build_fee_sheet(wb, tables["fee"])

    wb.calculation.calcMode = "auto"
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.active = 0

    destination = (
        Path(output_path)
        if output_path is not None
        else config.OUTPUTS_DIR / "Weekly_Operations_Replenishment_Model.xlsx"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destination)
    return destination


def generate_excel_workbook(
    result: dict[str, Any] | None = None,
    output_path: Path | str | None = None,
) -> Path:
    """Backwards-compatible alias used by the CLI."""

    return build_workbook(result=result, output_path=output_path)
