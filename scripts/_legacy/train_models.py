"""Train per-stock LightGBM+CatBoost models for the full universe.

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --symbols OGDC HBL

Outputs:
    models/{SYMBOL}_lgbm.pkl
    models/{SYMBOL}_cb.cbm
    models/metrics.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.table import Table

from brain.features import build_features, feature_columns
from brain.models import save_metrics, train_one
from config.universe import symbols as universe_symbols


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--test-fraction", type=float, default=0.15)
    args = parser.parse_args()

    console = Console()
    console.rule("[bold cyan]Building features")
    df = build_features(include_macro=True)
    cols = feature_columns(df)
    console.print(f"[green]Features ready:[/green] {df.shape[0]:,} rows x {len(cols)} features")

    symbols = [s.upper() for s in (args.symbols or universe_symbols())]

    table = Table(title="Per-stock training results (out-of-sample tail)")
    table.add_column("Symbol", style="cyan")
    table.add_column("n_train", justify="right")
    table.add_column("n_test", justify="right")
    table.add_column("AUC", justify="right")
    table.add_column("Acc", justify="right")
    table.add_column("LogLoss", justify="right")
    table.add_column("UpRateTest", justify="right")
    table.add_column("Top 3 features (gain)", style="dim")

    results = []
    for sym in symbols:
        r = train_one(df, sym, cols, test_fraction=args.test_fraction)
        if r is None:
            table.add_row(sym, "-", "-", "-", "-", "-", "-", "[red]insufficient data[/red]")
            continue

        def _mark_auc(a: float) -> str:
            if a >= 0.60:
                return f"[bold green]{a:.3f}[/bold green]"
            if a >= 0.54:
                return f"[green]{a:.3f}[/green]"
            if a >= 0.50:
                return f"[yellow]{a:.3f}[/yellow]"
            return f"[red]{a:.3f}[/red]"

        top3 = ", ".join(n for n, _ in r.top_features[:3])
        table.add_row(
            sym,
            f"{r.n_train}", f"{r.n_test}",
            _mark_auc(r.auc),
            f"{r.accuracy:.3f}",
            f"{r.log_loss:.3f}",
            f"{r.up_rate_test:.2f}",
            top3,
        )
        results.append(r)

    console.print(table)

    if results:
        mean_auc = sum(r.auc for r in results) / len(results)
        mean_acc = sum(r.accuracy for r in results) / len(results)
        n_positive_auc = sum(1 for r in results if r.auc > 0.55)
        console.print(
            f"\n[bold]Summary:[/bold] mean AUC={mean_auc:.3f}  "
            f"mean Acc={mean_acc:.3f}  "
            f"symbols with AUC>0.55: {n_positive_auc}/{len(results)}"
        )
        p = save_metrics(results)
        console.print(f"Metrics saved to {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
