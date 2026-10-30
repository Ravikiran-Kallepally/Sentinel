"""Dispute (complaint) volume forecasting: seasonal-naive baseline vs an LSTM.

Operations teams staff dispute desks from a volume forecast. The series has weekly seasonality, a slow
trend and occasional spikes (a merchant breach).

Lesson built into this module: always compare against a strong simple baseline. "Same day last week"
(seasonal naive) is hard to beat on weekly-seasonal data. If the LSTM is only marginally better, the simpler
model is usually the right production choice - cheaper, more stable and easier to explain.

Evaluation is one-step-ahead on the last `test_days` (each prediction uses actual history up to the day
before), which is a fair, standard backtest for both models.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from torch import nn

from sentinel.config import get_settings

WINDOW = 28


class LSTMForecaster(nn.Module):
    def __init__(self, hidden: int = 32):
        super().__init__()
        self.lstm = nn.LSTM(input_size=8, hidden_size=hidden, batch_first=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


def _inputs(values: np.ndarray, dows: np.ndarray) -> np.ndarray:
    """Per-day input: scaled value + one-hot day of week (7) -> 8 features."""
    onehot = np.eye(7)[dows]
    return np.concatenate([values[:, None], onehot], axis=1)


@dataclass
class ForecastModel:
    state: dict
    mean: float
    std: float
    history: list[float]
    last_date: str

    def _net(self) -> LSTMForecaster:
        m = LSTMForecaster()
        m.load_state_dict(self.state)
        m.eval()
        return m

    def forecast(self, horizon: int = 14) -> list[dict]:
        """Recursive multi-step forecast beyond the last observed day."""
        net = self._net()
        hist = list(self.history)
        start = pd.Timestamp(self.last_date)
        out = []
        for h in range(1, horizon + 1):
            # Mirror the training windows exactly: rows for days t-27..t, values for days t-27..t-1 with the
            # last row carrying yesterday's value and the target day's weekday.
            target = start + pd.Timedelta(days=h)
            dates = [target - pd.Timedelta(days=WINDOW - 1 - i) for i in range(WINDOW)]
            vals = (np.array(hist[-(WINDOW - 1):] + [hist[-1]]) - self.mean) / self.std
            x = _inputs(vals, np.array([d.dayofweek for d in dates]))
            with torch.no_grad():
                y = float(net(torch.tensor(x[None], dtype=torch.float32))) * self.std + self.mean
            y = max(0.0, y)
            hist.append(y)
            out.append({"date": str((start + pd.Timedelta(days=h)).date()), "forecast": round(y, 1)})
        return out


def train(daily: pd.DataFrame, test_days: int = 21) -> tuple[ForecastModel, dict]:
    s = get_settings()
    torch.manual_seed(s.seed)
    series = daily.groupby("day").complaints.sum().asfreq("D", fill_value=0)
    values = series.values.astype(float)
    dows = series.index.dayofweek.values
    n_train = len(values) - test_days
    mean, std = values[:n_train].mean(), values[:n_train].std() + 1e-6
    z = (values - mean) / std
    X = _inputs(z, dows)

    def windows(lo: int, hi: int):
        xs, ys = [], []
        for t in range(max(lo, WINDOW), hi):
            # The window ends the day before t; the dow one-hot of the *target* day sits in the last row's
            # slot so the model knows which weekday it is predicting.
            w = X[t - WINDOW + 1: t + 1].copy()
            w[-1, 0] = z[t - 1]
            xs.append(w)
            ys.append(z[t])
        return torch.tensor(np.array(xs), dtype=torch.float32), torch.tensor(np.array(ys), dtype=torch.float32)

    xtr, ytr = windows(0, n_train)
    net = LSTMForecaster()
    opt = torch.optim.Adam(net.parameters(), lr=0.01)
    for _ in range(300):
        opt.zero_grad()
        loss = ((net(xtr) - ytr) ** 2).mean()
        loss.backward()
        opt.step()
    net.eval()

    xte, yte = windows(n_train, len(values))
    with torch.no_grad():
        pred = net(xte).numpy() * std + mean
    actual = values[n_train:]
    naive = values[n_train - 7: len(values) - 7]  # same day last week
    mae = lambda a, b: float(np.mean(np.abs(a - b)))  # noqa: E731
    rmse = lambda a, b: float(np.sqrt(np.mean((a - b) ** 2)))  # noqa: E731
    metrics = {
        "lstm_mae": round(mae(pred, actual), 3), "lstm_rmse": round(rmse(pred, actual), 3),
        "seasonal_naive_mae": round(mae(naive, actual), 3), "seasonal_naive_rmse": round(rmse(naive, actual), 3),
        "test_days": test_days,
    }
    metrics["lstm_beats_baseline"] = metrics["lstm_mae"] < metrics["seasonal_naive_mae"]
    model = ForecastModel({k: v.clone() for k, v in net.state_dict().items()}, float(mean), float(std),
                          values.tolist(), str(series.index[-1].date()))
    backtest = [{"date": str(d.date()), "actual": float(a), "lstm": round(float(p), 1), "seasonal_naive": float(nv)}
                for d, a, p, nv in zip(series.index[n_train:], actual, pred, naive)]
    history = [{"date": str(d.date()), "actual": float(v)} for d, v in zip(series.index, values)]
    return model, {"metrics": metrics, "backtest": backtest, "history": history, "future": model.forecast(14)}
