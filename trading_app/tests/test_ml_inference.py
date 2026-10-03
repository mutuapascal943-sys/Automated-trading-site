import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from trading_app.ml.inference import load_latest_model


class ModelSelectionTests(TestCase):
    def test_model_selection_matches_symbol_and_granularity_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            model_path = output_dir / 'R_75_300_test_model.pkl'
            metadata_path = output_dir / 'R_75_300_test_metadata.json'
            model_path.touch()
            metadata_path.write_text(json.dumps({
                'symbol': 'R_75',
                'granularity': 300,
                'feature_columns': ['rsi_14'],
            }))

            with patch('trading_app.ml.inference.pickle.load', return_value=object()):
                model_data = load_latest_model(directory, 'Volatility 75 Index', 300)
                no_wrong_timeframe = load_latest_model(directory, 'Volatility 75 Index', 900)

        self.assertIsNotNone(model_data)
        self.assertEqual(model_data[1], ['rsi_14'])
        self.assertEqual(model_data[2]['granularity'], 300)
        self.assertIsNone(no_wrong_timeframe)