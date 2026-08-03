"""
Train ML models for every market available in the bot panel.

Usage:
    python manage.py train_all_models
    python manage.py train_all_models --years 2 --granularity 900
    python manage.py train_all_models --markets EURUSD CRASH1000 R_75 --years 1
    python manage.py train_all_models --skip-existing --feedback
"""

from django.core.management.base import BaseCommand, CommandError
from trading_app.ml.pipeline import run_pipeline

# Frontend market label -> training symbol used for the model prefix.
MARKET_SYMBOLS: dict[str, str] = {
    "EUR/USD": "EURUSD",
    "GBP/USD": "GBPUSD",
    "USD/JPY": "USDJPY",
    "AUD/USD": "AUDUSD",
    "USD/CAD": "USDCAD",
    "NZD/USD": "NZDUSD",
    "XAU/USD": "XAUUSD",
    "BTC/USD": "BTCUSD",
    "Boom 1000 Index": "BOOM1000",
    "Crash 1000 Index": "CRASH1000",
    "Volatility 75 Index": "R_75",
    "Volatility 100 Index": "R_100",
}


class Command(BaseCommand):
    help = "Run the ML training pipeline for all (or selected) bot-panel markets"

    def add_arguments(self, parser):
        parser.add_argument(
            "--granularity", type=int, default=900,
            help="Candle granularity in seconds (default: 900 = 15min)",
        )
        parser.add_argument(
            "--years", type=float, default=2.0,
            help="Years of historical data to collect per market (default: 2.0)",
        )
        parser.add_argument(
            "--horizon", type=int, default=4,
            help="Prediction horizon in bars (default: 4)",
        )
        parser.add_argument(
            "--threshold", type=float, default=0.1,
            help="Price change threshold in %% (default: 0.1)",
        )
        parser.add_argument(
            "--windows", type=int, default=5,
            help="Number of walk-forward windows (default: 5)",
        )
        parser.add_argument(
            "--output", type=str, default=None,
            help="Output directory (default: ml_output/)",
        )
        parser.add_argument(
            "--feedback", action="store_true",
            help="Include resolved live predictions in training",
        )
        parser.add_argument(
            "--markets", nargs="+", default=None,
            help="Training symbols to train (default: all 12 bot-panel markets)",
        )
        parser.add_argument(
            "--skip-existing", action="store_true",
            help="Skip symbols that already have a trained model",
        )

    def handle(self, *args, **options):
        granularity = options["granularity"]
        years = options["years"]
        horizon = options["horizon"]
        threshold = options["threshold"]
        windows = options["windows"]
        output = options["output"]
        use_feedback = options["feedback"]
        skip_existing = options["skip_existing"]

        requested = set(options["markets"] or [])
        targets = [
            (label, symbol)
            for label, symbol in MARKET_SYMBOLS.items()
            if not requested or symbol in requested or label in requested
        ]

        if not targets:
            raise CommandError(f"No matching markets. Choose from: {', '.join(MARKET_SYMBOLS.values())}")

        self.stdout.write(self.style.NOTICE(
            f"Training {len(targets)} market(s): {', '.join(s for _, s in targets)}\n"
            f"  Granularity: {granularity}s | Years: {years:.1f} | Horizon: {horizon} | "
            f"Threshold: {threshold}% | Feedback: {use_feedback}"
        ))

        failed = []
        for label, symbol in targets:
            if skip_existing and _model_exists(symbol, output):
                self.stdout.write(f"  SKIP  {symbol} ({label}) — model already exists")
                continue
            self.stdout.write(self.style.WARNING(f"\n=== Training {symbol} ({label}) ==="))
            try:
                results = run_pipeline(
                    symbol=symbol,
                    granularity=granularity,
                    years=years,
                    horizon=horizon,
                    threshold_pct=threshold,
                    n_windows=windows,
                    output_dir=output,
                    feedback_rows=_feedback_rows(symbol) if use_feedback else None,
                )
                self.stdout.write(self.style.SUCCESS(f"  DONE  {symbol} -> {results.get('model_path')}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  FAIL  {symbol}: {e}"))
                failed.append(symbol)

        if failed:
            self.stdout.write(self.style.ERROR(f"\nCompleted with failures: {', '.join(failed)}"))
        else:
            self.stdout.write(self.style.SUCCESS("\nAll markets trained successfully."))


def _model_exists(symbol: str, output: str | None) -> bool:
    from pathlib import Path
    from trading_app.ml.inference import OUTPUT_DIR, _normalize_symbol

    out = Path(output) if output else OUTPUT_DIR
    if not out.exists():
        return False
    return any(
        _normalize_symbol(f.name.split("_")[0]) == _normalize_symbol(symbol)
        for f in out.glob("*_model.pkl")
    )


def _feedback_rows(symbol: str):
    from trading_app.ml.feedback import load_feedback_rows
    from trading_app.ml.features import FEATURE_COLUMNS

    return load_feedback_rows(symbol, FEATURE_COLUMNS)
