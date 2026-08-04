"""
Django management command to run the ML training pipeline.

Usage:
    python manage.py train_model
    python manage.py train_model --symbol EURUSD --granularity 900 --years 3
    python manage.py train_model --symbol XAUUSD --threshold 0.2 --horizon 6
"""

from django.core.management.base import BaseCommand, CommandError
from trading_app.ml.labels import LABEL_THRESHOLD_PCT
from trading_app.ml.pipeline import run_pipeline
from trading_app.ml.symbols import deriv_symbol_for


class Command(BaseCommand):
    help = "Run the ML training pipeline for a trading symbol"

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol", type=str, default="EURUSD",
            help="Trading symbol (default: EURUSD)",
        )
        parser.add_argument(
            "--granularity", type=int, default=900,
            help="Candle granularity in seconds (default: 900 = 15min)",
        )
        parser.add_argument(
            "--years", type=float, default=2.0,
            help="Years of historical data to collect (default: 2.0)",
        )
        parser.add_argument(
            "--horizon", type=int, default=4,
            help="Prediction horizon in bars (default: 4)",
        )
        parser.add_argument(
            "--threshold", type=float, default=LABEL_THRESHOLD_PCT,
            help=f"Price change threshold in %% (default: {LABEL_THRESHOLD_PCT})",
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
            help="Include resolved live predictions (PredictionRecords) in training",
        )
        parser.add_argument(
            "--data-symbol", type=str, default=None,
            help="Deriv API symbol to collect data for (default: auto-mapped from --symbol)",
        )

    def handle(self, *args, **options):
        symbol = options["symbol"]
        granularity = options["granularity"]
        years = options["years"]
        horizon = options["horizon"]
        threshold = options["threshold"]
        windows = options["windows"]
        output = options["output"]
        use_feedback = options["feedback"]
        data_symbol = options["data_symbol"] or deriv_symbol_for(symbol)

        self.stdout.write(self.style.NOTICE(
            f"Starting ML pipeline for {symbol}\n"
            f"  Data symbol: {data_symbol}\n"
            f"  Granularity: {granularity}s ({_gran_label(granularity)})\n"
            f"  History: {years:.1f} years\n"
            f"  Horizon: {horizon} bars\n"
            f"  Threshold: {threshold}%\n"
            f"  Walk-forward windows: {windows}"
        ))

        feedback_rows = None
        if use_feedback:
            from trading_app.ml.feedback import load_feedback_rows
            from trading_app.ml.features import FEATURE_COLUMNS

            feedback_rows = load_feedback_rows(symbol, FEATURE_COLUMNS)
            self.stdout.write(f"  Feedback rows: {len(feedback_rows) if feedback_rows else 0}")

        try:
            results = run_pipeline(
                symbol=symbol,
                data_symbol=data_symbol,
                granularity=granularity,
                years=years,
                horizon=horizon,
                threshold_pct=threshold,
                n_windows=windows,
                output_dir=output,
                feedback_rows=feedback_rows,
            )
        except Exception as e:
            raise CommandError(f"Pipeline failed: {e}")

        self.stdout.write(self.style.SUCCESS("\n=== Pipeline Complete ==="))
        self.stdout.write(f"Symbol: {symbol}")

        for step in results.get("steps", []):
            name = step.pop("step", "unknown")
            self.stdout.write(f"\n  [{name}]")
            for k, v in step.items():
                self.stdout.write(f"    {k}: {v}")

        self.stdout.write(f"\n  Model: {results.get('model_path', 'N/A')}")
        self.stdout.write(f"  Metadata: {results.get('metadata_path', 'N/A')}")
        self.stdout.write(f"  Results: {results.get('output_dir', 'N/A')}/pipeline_results.json")


def _gran_label(g: int) -> str:
    labels = {60: "1m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 14400: "4h", 86400: "1d"}
    return labels.get(g, f"{g}s")
