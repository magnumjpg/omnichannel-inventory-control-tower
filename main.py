"""Command-line entry point for the omnichannel inventory control tower.

Usage
-----
    python main.py --generate-data
    python main.py --run-pipeline
    python main.py --export-excel
    python main.py --run-all

The pipeline is deterministic (seed 42).  ``--run-all`` is the canonical
weekly refresh: regenerate the six ERP feeds, recompute the unconstrained
demand and two-echelon plans, write the executive briefing, and build the
six-tab Excel sign-off artifact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from src import config
from src.data.generator import generate_all
from src.deliverables.briefing_report import write_executive_briefing
from src.deliverables.excel_generator import build_workbook
from src.engine.pipeline import run_pipeline


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _print_summary(result: dict[str, Any], workbook: Path | None = None,
                   briefing: Path | None = None) -> None:
    s = result["summary"]
    container = s["container"]
    print("")
    print("=" * 68)
    print("  OMNICHANNEL MULTI-ECHELON CONTROL TOWER · WEEKLY RUN")
    print("=" * 68)
    print(f"  Snapshot                     : {s['snapshot_date']}")
    print(f"  Child FNSKUs                 : {s['sku_count']:,}")
    print(f"  FBA available                : {s['fba_available_units']:,} u")
    print(f"  3PL on hand                  : {s['three_pl_units']:,} u")
    print(f"  Open factory POs             : {s['open_po_units']:,} u")
    print(f"  FNSKUs with HDoS < 14d       : {s['stockout_skus_hdos_lt_14']:,}")
    print(f"  Non-exempt 14-28d fee band   : {s['fee_band_skus_hdos_14_28']:,}")
    print(f"  Aged-cliff FNSKUs (>270d)    : {s['aged_cliff_skus']:,}")
    print(f"  3PL -> FBA transfer          : {s['transfer_units']:,} u / "
          f"{s['transfer_cartons']:,} cartons")
    print(f"  Factory PO                   : {s['factory_units']:,} u / "
          f"{s['factory_cartons']:,} cartons / {s['factory_cbm']:.3f} CBM")
    print(f"  Container                    : {container['container_type']} · "
          f"{container['total_cbm']:.3f} / {container['capacity_cbm']:.1f} CBM "
          f"({container['utilization']:.1%})")
    print(f"  Aggregate PO MOQ             : "
          f"{'MET' if s['aggregate_moq_met'] else 'NOT MET'}")
    print(f"  30-day fee exposure          : {_money(s['fee_exposure_usd'])}")
    print(f"  Projected monthly AIS        : "
          f"{_money(s['projected_monthly_ais_usd'])}")
    print(f"  Removal recommendations      : {s['removal_recommendations']:,}")
    if workbook is not None:
        print(f"  Excel workbook               : {workbook}")
    if briefing is not None:
        print(f"  Executive briefing           : {briefing}")
    print("=" * 68)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inventory-control-tower",
        description=(
            "Omnichannel multi-echelon inventory allocation, demand "
            "unconstraining and container knapsack engine."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --run-all\n"
            "  python main.py --generate-data\n"
            "  python main.py --run-pipeline\n"
            "  python main.py --export-excel"
        ),
    )
    parser.add_argument(
        "--generate-data",
        action="store_true",
        help="Regenerate the six relational ERP feeds in data/raw/ (seed 42).",
    )
    parser.add_argument(
        "--run-pipeline",
        action="store_true",
        help="Run demand unconstraining, buffers, factory PO and container knapsack.",
    )
    parser.add_argument(
        "--export-excel",
        action="store_true",
        help="Build the six-tab live-formula Excel workbook in outputs/.",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Generate feeds, run the pipeline, write the briefing and export Excel.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=config.SEED,
        help=f"Random seed for --generate-data (default: {config.SEED}).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the run summary (artifacts are still written).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    requested = args.generate_data or args.run_pipeline or args.export_excel or args.run_all
    if not requested:
        parser.print_help()
        return 0

    if args.generate_data or args.run_all:
        print(f"[1/3] Generating deterministic ERP feeds (seed={args.seed}) ...")
        files = generate_all(seed=args.seed)
        for name, path in files.items():
            print(f"      {name:26s} -> {path}")

    # --run-pipeline and --export-excel both need a fresh, internally
    # consistent plan.  Persisting the processed CSVs makes the workbook
    # auditable against the Python engine.
    result: dict[str, Any] | None = None
    briefing: Path | None = None
    workbook: Path | None = None

    if args.run_pipeline or args.export_excel or args.run_all:
        print("[2/3] Running unconstraining, buffers, MOQs and knapsack ...")
        try:
            result = run_pipeline(persist=True)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print("Run `python main.py --generate-data` first.", file=sys.stderr)
            return 1
        briefing = write_executive_briefing(result)
        print(f"      briefing -> {briefing}")

    if args.export_excel or args.run_all:
        print("[3/3] Building the six-tab live-formula Excel workbook ...")
        assert result is not None
        workbook = build_workbook(result=result)
        print(f"      workbook -> {workbook}")

    if not args.quiet and result is not None:
        _print_summary(result, workbook=workbook, briefing=briefing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
