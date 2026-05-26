import pandas as pd
import numpy as np

def calc_kc(symbol, Ns, as_of_date):
    df = pd.read_csv(f"/home/paragon/proj/nse-puller/out/stocks/{symbol}.csv")
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    df['TR'] = pd.DataFrame({'tr1': tr1, 'tr2': tr2, 'tr3': tr3}).max(axis=1)
    
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
    
    for N in Ns:
        middle = df['close'].ewm(span=N, adjust=False, min_periods=N).mean()
        
        # Calculate ATR natively as commonly implemented without SMA seed
        atr_no_seed = df['TR'].ewm(alpha=1/N, adjust=False, min_periods=N).mean()
        
        # Calculate ATR with SMA seed (Wilder's original method often used)
        tr = df['TR']
        atr_sma_seed = pd.Series(index=tr.index, dtype=float)
        
        # find the first valid TR index
        first_valid_idx = tr.first_valid_index()
        
        # Calculate SMA of TR for the first N periods
        if first_valid_idx is not None and first_valid_idx + N <= len(tr):
            # SMA over the first N valid periods (which starts at first_valid_idx to first_valid_idx + N)
            sma_val = tr.loc[first_valid_idx : first_valid_idx + N - 1].mean()
            atr_sma_seed.loc[first_valid_idx + N - 1] = sma_val
            
            # EMA for the rest
            for i in range(first_valid_idx + N, len(tr)):
                atr_sma_seed.iloc[i] = (atr_sma_seed.iloc[i-1] * (N-1) + tr.iloc[i]) / N
                
        m_val = middle.iloc[idx]
        
        # Which ATR to use? Standard trading libraries like pandas-ta use SMA seed for Wilder ATR.
        a_val = atr_sma_seed.iloc[idx]
        
        upper = m_val + 2.0 * a_val
        lower = m_val - 2.0 * a_val
        bandwidth = ((upper - lower) / m_val) * 100 if m_val != 0 else 0.0
        
        print(f"N={N}:")
        print(f"  Middle:       {m_val:.6f}")
        print(f"  Upper:        {upper:.6f}")
        print(f"  Lower:        {lower:.6f}")
        print(f"  Bandwidth:    {bandwidth:.6f}")
        
        a_val2 = atr_no_seed.iloc[idx]
        upper2 = m_val + 2.0 * a_val2
        lower2 = m_val - 2.0 * a_val2
        bandwidth2 = ((upper2 - lower2) / m_val) * 100 if m_val != 0 else 0.0
        print(f"  [Alt no-SMA-seed Middle: {m_val:.6f}, Bandwidth: {bandwidth2:.6f}, Upper: {upper2:.6f}, Lower: {lower2:.6f}]")

calc_kc('ABB', [20], '2008-03-21')
calc_kc('HAL', [20, 21], '2024-01-27')
calc_kc('TRENT', [20], '2020-03-21')
