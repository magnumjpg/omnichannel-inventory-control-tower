"""Session fixtures for the omnichannel control-tower test suite.

The expensive work (seed-42 generation, the full engine pipeline and the
six-tab workbook build) happens exactly once per pytest session inside an
isolated temporary directory.  Tests therefore never mutate ``data/`` or
``outputs/`` in the repository.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.generator import generate_all  # noqa: E402
from src.deliverables.excel_generator import build_workbook  # noqa: E402
from src.engine.pipeline import run_pipeline  # noqa: E402


@pytest.fixture(scope="session")
def project(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """Generate feeds, run the pipeline and build the workbook once."""

    base = tmp_path_factory.mktemp("control_tower")
    raw_dir = base / "raw"
    processed_dir = base / "processed"
    output_dir = base / "outputs"

    files = generate_all(output_dir=raw_dir, processed_dir=processed_dir)
    result = run_pipeline(data_dir=raw_dir, output_dir=processed_dir, persist=True)
    workbook_path = build_workbook(
        result=result,
        output_path=output_dir / "Weekly_Operations_Replenishment_Model.xlsx",
    )

    truth = pd.read_csv(processed_dir / "generation_truth.csv")
    # Reload from disk so the tests also validate the persisted contracts.
    persisted = {
        path.stem: pd.read_csv(path)
        for path in sorted(processed_dir.glob("*.csv"))
        if path.stem != "generation_truth"
    }

    return SimpleNamespace(
        base=base,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        output_dir=output_dir,
        files=files,
        feeds=result["feeds"],
        truth=truth,
        demand=result["demand"],
        fba_plan=result["fba_plan"],
        factory_plan=result["factory_plan"],
        container_plan=result["container_plan"],
        fee_audit=result["fee_audit"],
        summary=result["summary"],
        result=result,
        persisted=persisted,
        workbook_path=workbook_path,
    )
