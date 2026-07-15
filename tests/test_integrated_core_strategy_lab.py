import unittest

import numpy as np
import pandas as pd

from tools.integrated_core_strategy_lab import ALL_SYMBOLS, signal_weights


class IntegratedCoreContractTests(unittest.TestCase):
    def synthetic_panel(self) -> pd.DataFrame:
        index = pd.date_range("2018-01-01", periods=30, freq="MS")
        data = {}
        for offset, symbol in enumerate(ALL_SYMBOLS):
            data[symbol] = 100.0 * np.cumprod(np.full(len(index), 1.01 + offset * 0.00001))
        return pd.DataFrame(data, index=index)

    def test_signal_weights_sum_to_one(self) -> None:
        weights = signal_weights(self.synthetic_panel())
        self.assertTrue(np.allclose(weights.sum(axis=1).to_numpy(), 1.0))

    def test_early_months_stay_in_reserves_for_trend_sleeves(self) -> None:
        weights = signal_weights(self.synthetic_panel())
        first = weights.iloc[0]
        self.assertAlmostEqual(float(first["BIL"]), 0.32)
        self.assertAlmostEqual(float(first["153130.KS"]), 0.20)


if __name__ == "__main__":
    unittest.main()
