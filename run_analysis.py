"""
Ein Befehl fuer die komplette Analyse:

    py run_analysis.py                          # nimmt data/raw/bloomberg_export.xlsx (oder .csv)
    py run_analysis.py --input pfad/zur/datei   # beliebiger Bloomberg-Export
    py run_analysis.py --synthetic              # Testlauf mit synthetischen Daten -> output/synthetic/

Ablauf:
    1. Bloomberg-Export laden, Einheiten normalisieren, validieren  -> data/processed/prices_long.csv
    2. Jahrgangsfenster schneiden, Spread berechnen                 -> output/spread_daily.csv
    3. Kennzahlen pro Jahrgang, Saison-Matrizen, Gesamtueberblick   -> output/vintage_stats.csv, seasonal_*.csv, summary.json
    4. Charts (PNG + interaktives Dashboard)                        -> output/chart_*.png, output/dashboard.html
    5. Report                                                       -> output/report.md, output/report.html
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from spread import config  # noqa: E402

log = logging.getLogger("run_analysis")


def _find_default_input() -> Path | None:
    """Sucht den Bloomberg-Export in data/raw: bevorzugt bloomberg_export.xlsx, sonst .csv/.xlsm."""
    candidates = [
        config.DEFAULT_RAW_FILE,
        config.DATA_RAW / "bloomberg_export.csv",
        config.DATA_RAW / "bloomberg_export.xlsm",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Irgendein Nicht-Synthetik-File in data/raw
    for p in sorted(config.DATA_RAW.glob("*")):
        if p.suffix.lower() in {".xlsx", ".xlsm", ".csv"} and not p.name.startswith("synthetic"):
            return p
    return None


def _ensure_synthetic() -> Path:
    synth = config.DATA_RAW / "synthetic_export.xlsx"
    if not synth.exists():
        log.info("Erzeuge synthetische Testdaten ...")
        sys.path.insert(0, str(PROJECT_ROOT / "tests"))
        import make_synthetic  # type: ignore

        make_synthetic.main([])
    return synth


def _fmt(x, nd=2):
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RB/HO Jun-vs-Dec Crack-Spread-Analyse")
    ap.add_argument("--input", "-i", type=Path, default=None, help="Bloomberg-Export (.xlsx/.xlsm/.csv)")
    ap.add_argument("--synthetic", action="store_true", help="Testlauf mit synthetischen Daten")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Zielordner (Standard: output/, bei --synthetic output/synthetic/)")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    t0 = time.time()

    # ------------------------------------------------------------------ Input
    if args.synthetic:
        raw_path = _ensure_synthetic()
    elif args.input is not None:
        raw_path = args.input
    else:
        raw_path = _find_default_input()
    if raw_path is None or not Path(raw_path).exists():
        log.error(
            "Kein Bloomberg-Export gefunden. Erwartet: %s (oder --input angeben, oder --synthetic).",
            config.DEFAULT_RAW_FILE,
        )
        return 2
    log.info("Input: %s", raw_path)

    out_dir: Path = args.out_dir or (config.OUTPUT_DIR / "synthetic" if args.synthetic else config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.synthetic:
        log.warning("SYNTHETISCHE TESTDATEN - Ergebnisse in %s sind keine Marktdaten.", out_dir)
    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ 1. Laden
    from spread import load, stats, vintages

    prices = load.load_bloomberg_export(raw_path)
    prices_long_path = config.PRICES_LONG_FILE if not args.synthetic else config.DATA_PROCESSED / "prices_long_synthetic.csv"
    prices.to_csv(prices_long_path, index=False)
    validation = load.validate_prices_long(prices)
    validation_path = out_dir / "data_validation.csv"
    validation.to_csv(validation_path, index=False)
    n_missing = int((validation["status"] == "missing").sum()) if "status" in validation else 0
    n_problem = int((~validation["status"].isin(["ok"])).sum()) if "status" in validation else 0
    log.info(
        "Preise geladen: %d Zeilen, %d Ticker, %s bis %s | Validierung: %d Ticker mit Auffaelligkeiten (%d fehlen)",
        len(prices), prices["ticker"].nunique(), prices["date"].min().date(), prices["date"].max().date(),
        n_problem, n_missing,
    )
    if n_problem:
        print("\n=== Datenvalidierung (nur Auffaelligkeiten) ===")
        cols = [c for c in ["ticker", "status", "n", "first_date", "last_date", "expected_expiry", "actual_last_date"] if c in validation]
        print(validation.loc[~validation["status"].isin(["ok"]), cols].to_string(index=False))

    # ------------------------------------------------------------------ 2. Spread
    spread_daily = vintages.build_spread_daily(prices)
    spread_daily.to_csv(out_dir / "spread_daily.csv", index=False)
    try:
        exp_check = vintages.expected_expiry_check(spread_daily)
        exp_check.to_csv(out_dir / "expiry_check.csv", index=False)
    except Exception as e:  # pragma: no cover
        log.warning("expected_expiry_check fehlgeschlagen: %s", e)
        exp_check = None

    # ------------------------------------------------------------------ 3. Statistik
    vstats = stats.vintage_stats(spread_daily)
    vstats.to_csv(out_dir / "vintage_stats.csv", index=False)
    seasonal = stats.seasonal_tables(spread_daily)
    for key, df in seasonal.items():
        df.to_csv(out_dir / f"seasonal_{key}.csv")
    overall = stats.overall_summary(spread_daily, vstats)
    (out_dir / "summary.json").write_text(json.dumps(overall, indent=2, default=str, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ 4./5. Charts + Report
    chart_paths: dict = {}
    if not args.no_charts:
        from spread import charts, report

        chart_paths = charts.make_all_charts(spread_daily, vstats, seasonal, out_dir)
        notes = []
        if n_problem:
            notes.append(f"{n_problem} Ticker mit Auffaelligkeiten in der Validierung (siehe data_validation.csv), davon {n_missing} fehlend.")
        if exp_check is not None and "match" in exp_check and not bool(exp_check["match"].all()):
            bad = exp_check.loc[~exp_check["match"].astype(bool)]
            notes.append(f"Verfallsdatum weicht bei {len(bad)} Jahrgaengen vom erwarteten letzten Mai-Geschaeftstag ab: {bad['vintage'].tolist()}.")
        notes.append(f"Quelle: {Path(raw_path).name}, {len(prices)} Preiszeilen, {prices['ticker'].nunique()} Ticker.")
        report_paths = report.write_report(
            spread_daily, vstats, seasonal, overall, chart_paths, out_dir,
            validation_notes="\n".join(f"- {n}" for n in notes),
        )
        chart_paths.update(report_paths)

    # ------------------------------------------------------------------ Konsole
    print("\n=== Kennzahlen pro Jahrgang (spread_bbl in $/bbl) ===")
    show = [c for c in ["vintage", "window_start", "window_end", "n_days", "first_value", "last_value",
                        "min", "min_date", "max", "n_negative_days", "pct_negative_days"] if c in vstats]
    with_pd = vstats[show].copy()
    for c in ["first_value", "last_value", "min", "max", "pct_negative_days"]:
        if c in with_pd:
            with_pd[c] = with_pd[c].map(lambda v: _fmt(v))
    for c in ["window_start", "window_end", "min_date"]:
        if c in with_pd:
            with_pd[c] = with_pd[c].astype(str).str.slice(0, 10)
    print(with_pd.to_string(index=False))

    print("\n=== Gesamt ===")
    for k, v in overall.items():
        print(f"{k:35s} {v}")
    print(f"\nOutputs in {out_dir}  ({time.time() - t0:.1f}s)")
    for k, p in chart_paths.items():
        print(f"  {k:22s} {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
