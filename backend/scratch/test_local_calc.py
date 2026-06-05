import requests
import pandas as pd
from datetime import datetime

url = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?period1=820454400&period2=1780000000&interval=1d"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

try:
    res = requests.get(url, headers=headers)
    data = res.json()
    chart = data.get("chart", {}).get("result", [])[0]
    timestamps = chart.get("timestamp", [])
    quotes = chart.get("indicators", {}).get("quote", [])[0]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": quotes.get("open", []),
        "high": quotes.get("high", []),
        "low": quotes.get("low", []),
        "close": quotes.get("close", []),
        "volume": quotes.get("volume", [])
    })

    # Drop rows with missing values
    df = df.dropna().reset_index(drop=True)
    df["date"] = df["timestamp"].apply(lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d"))

    # Calculate indicators
    df['sma_10'] = df['close'].rolling(window=10).mean()
    df['sma_50'] = df['close'].rolling(window=50).mean()

    # RSI 14
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()

    avg_gain_vals = avg_gain.values
    avg_loss_vals = avg_loss.values
    gain_vals = gain.values
    loss_vals = loss.values

    for i in range(15, len(df)):
        avg_gain_vals[i] = (avg_gain_vals[i-1] * 13 + gain_vals[i]) / 14
        avg_loss_vals[i] = (avg_loss_vals[i-1] * 13 + loss_vals[i]) / 14

    df['rsi_14'] = 100 - (100 / (1 + avg_gain_vals / (avg_loss_vals + 1e-10)))

    # MACD
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    print("DataFrame shape:", df.shape)
    print("First 15 rows with indicators:")
    print(df[["date", "close", "sma_10", "sma_50", "rsi_14", "macd", "macd_signal"]].head(15))
    print("\nLast 5 rows with indicators:")
    print(df[["date", "close", "sma_10", "sma_50", "rsi_14", "macd", "macd_signal"]].tail(5))
except Exception as e:
    print("Error:", e)
