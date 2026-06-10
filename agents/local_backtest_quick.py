import os
import datetime
import math
import numpy as np
import pandas as pd
import backtrader as bt
from dotenv import load_dotenv

load_dotenv()


class InstitutionalGuruStrategy(bt.Strategy):
    """
    Simple strategy: 9-EMA trend filter + RSI thresholds. Used for quick local
    backtest using synthetic/locally generated CSV data to bypass yfinance issues.
    """
    params = (
        ("ema_period", 9),
        ("rsi_lower", 30),
        ("rsi_upper", 70),
    )

    def __init__(self):
        self.data_close = self.datas[0].close
        self.ema = bt.indicators.ExponentialMovingAverage(self.data_close, period=self.p.ema_period)
        self.rsi = bt.indicators.RelativeStrengthIndex(self.data_close)

    def log(self, txt, dt=None):
        dt = dt or self.datas[0].datetime.date(0)
        print(f"[{dt.isoformat()}] {txt}")

    def next(self):
        if not self.position:
            # Bullish entry: price above EMA and RSI below lower bound (dip-buy)
            if self.data_close[0] > self.ema[0] and self.rsi[0] < self.p.rsi_lower:
                self.log(f"BUY @ {self.data_close[0]:.2f}")
                # size=1 here for quick run; treat as 1 lot in analysis
                self.buy(size=1)
        else:
            # Exit when price below EMA or RSI becomes overbought
            if self.data_close[0] < self.ema[0] or self.rsi[0] > self.p.rsi_upper:
                self.log(f"SELL @ {self.data_close[0]:.2f}")
                self.close()


def generate_robust_historical_csv(filename="nifty_historical_clean.csv"):
    """Generate a synthetic, well-formed historical CSV to feed Backtrader.

    This avoids external data sources and ensures the backtest runs in any env.
    """
    print("Generating local synthetic historical CSV...")
    np.random.seed(101)
    # 500 business days ending today
    date_range = pd.bdate_range(end=datetime.date.today(), periods=500)
    # simulate drift + noise around 23000
    closes = 23000.0 + np.cumsum(np.random.normal(loc=2.0, scale=60.0, size=len(date_range)))
    opens = closes + np.random.normal(loc=0.0, scale=20.0, size=len(date_range))
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(loc=30.0, scale=20.0, size=len(date_range)))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(loc=30.0, scale=20.0, size=len(date_range)))
    volumes = np.random.randint(100000, 900000, size=len(date_range))

    df = pd.DataFrame({
        "Date": date_range.strftime("%Y-%m-%d"),
        "Open": opens.round(2),
        "High": highs.round(2),
        "Low": lows.round(2),
        "Close": closes.round(2),
        "Volume": volumes,
    })
    df.to_csv(filename, index=False)
    print(f"Saved synthetic CSV to {filename}")
    return filename


def run_backtrader_matrix():
    print("Starting local backtest using synthetic CSV...")
    cerebro = bt.Cerebro()
    cerebro.addstrategy(InstitutionalGuruStrategy)

    csv_file = "nifty_historical_clean.csv"
    if not os.path.exists(csv_file):
        generate_robust_historical_csv(csv_file)

    data_feed = bt.feeds.GenericCSVData(
        dataname=csv_file,
        fromdate=datetime.datetime.now() - datetime.timedelta(days=400),
        todate=datetime.datetime.now(),
        nullvalue=0.0,
        dtformat=("%Y-%m-%d"),
        datetime=0,
        open=1,
        high=2,
        low=3,
        close=4,
        volume=5,
        openinterest=-1,
    )

    cerebro.adddata(data_feed)
    cerebro.broker.setcash(50000.0)
    cerebro.broker.setcommission(commission=0.0002)

    print(f"Starting portfolio value: ₹{cerebro.broker.getvalue():.2f}")
    cerebro.run()
    print(f"Final portfolio value:   ₹{cerebro.broker.getvalue():.2f}")


if __name__ == "__main__":
    run_backtrader_matrix()
