import os
import sys
import pandas as pd
import numpy as np

# Adjust path to import ml_engine modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml_engine.models import walk_forward_evaluate

def main():
    print("Running walk-forward evaluation to generate OOS predictions...")
    # Run the walk-forward with n_splits=5
    oos = walk_forward_evaluate(n_splits=5)
    if oos is None or oos.empty:
        print("Error: walk-forward returned empty dataframe.")
        return

    y = oos["target_win"].values
    ret = oos["trade_ret"].values
    p = oos["prob"].values
    round_trip_fee = 0.001

    results = []
    # Test thresholds from 0.05 to 0.40 in steps of 0.01
    thresholds = np.arange(0.05, 0.41, 0.01)

    print("\nGrid searching thresholds...")
    for t in thresholds:
        msk = p >= t
        n = int(msk.sum())
        if n == 0:
            continue

        wr = float(y[msk].mean())
        net = ret[msk] - round_trip_fee
        mean_net = float(net.mean())
        total_net = float(net.sum())

        # Calculate standard deviation of net returns for t-test / Sharpe-like metric
        std_net = float(net.std()) if n > 1 else 0.0
        t_stat = (mean_net / (std_net / np.sqrt(n))) if (std_net > 0 and n > 1) else 0.0

        results.append({
            "threshold": t,
            "signals": n,
            "win_rate_pct": wr * 100.0,
            "mean_net_ret": mean_net,
            "total_net_ret": total_net,
            "std_net": std_net,
            "t_stat": t_stat
        })

    df_res = pd.DataFrame(results)

    # Sort by total net return
    df_sorted = df_res.sort_values(by="total_net_ret", ascending=False)

    print("\n=== GRID SEARCH RESULTS (Sorted by Threshold) ===")
    headers = ["Threshold", "Signals", "Win Rate %", "Mean Net Ret", "Total Net Ret", "Std Net", "t-stat"]
    print(df_res.to_markdown(index=False, headers=headers))

    print("\n=== TOP 5 THRESHOLDS BY TOTAL NET RETURN ===")
    for i, row in df_sorted.head(5).iterrows():
        print(f"Threshold: {row['threshold']:.2f} | Signals: {int(row['signals'])} | Win Rate: {row['win_rate_pct']:.1f}% | Mean Net Ret: {row['mean_net_ret']:.5f} | Total Net Ret: {row['total_net_ret']:.3f} | t-stat: {row['t_stat']:.3f}")

if __name__ == "__main__":
    main()
