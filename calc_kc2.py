import pandas as pd
import numpy as np

def calc_kc(symbol, Ns, as_of_date):
    df = pd.read_csv(f"/home/paragon/proj/nse-puller/out/stocks/{symbol}.csv")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    # Calculate TR exactly as the repo does
    prev_close = df['close'].shift(1)
    true_range = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low'] - prev_close).abs(),
    ], axis=1).max(axis=1)
    
    target_date = pd.to_datetime(as_of_date)
    future_dates = df[df['date'] >= target_date]
    if len(future_dates) == 0:
        print(f"{symbol}: No date found")
        return
    resolved_date = future_dates.iloc[0]['date']
    idx = future_dates.index[0]
    
    print(f"--- Case: {symbol} ---")
    print(f"Requested Date: {as_of_date}")
    print(f"Resolved Date: {resolved_date.date()}")
    
    for day_count in Ns:
        # middle = EMA(close, N) with adjust=False and min_periods=N
        middle_series = df['close'].ewm(span=day_count, adjust=False, min_periods=day_count).mean()
        middle = middle_series.iloc[idx]
        
        # ATR logic
        tr_shifted = true_range.iloc[1:].reset_index(drop=True)
        atr = pd.Series(index=tr_shifted.index, dtype=float)
        
        # SMA for first 'day_count' elements
        atr.iloc[day_count - 1] = float(tr_shifted.iloc[:day_count].mean())
        
        for index in range(day_count, len(tr_shifted)):
            atr.iloc[index] = ((float(atr.iloc[index - 1]) * (day_count - 1)) + float(tr_shifted.iloc[index])) / day_count
            
        # Prepend NaN
        atr = pd.concat([pd.Series([float("nan")]), atr], ignore_index=True)
        atr.index = df.index
        
        atr_value = atr.iloc[idx]
        
        upper = middle + (2.0 * atr_value)
        lower = middle - (2.0 * atr_value)
        bandwidth = 0.0 if middle == 0.0 else ((upper - lower) / middle) * 100.0
        
        print(f"N={day_count}:")
        print(f"  upper: {upper:.6f}")
        print(f"  middle: {middle:.6f}")
        print(f"  lower: {lower:.6f}")
        print(f"  bandwidth: {bandwidth:.6f}")

calc_kc('ABB', [20], '2008-03-21')
calc_kc('HAL', [20, 21], '2024-01-27')
calc_kc('TRENT', [20], '2020-03-21')
