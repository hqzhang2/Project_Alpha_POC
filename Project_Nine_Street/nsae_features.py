import pandas as pd
import numpy as np
import yfinance as yf
from ta.trend import ADXIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator
from sklearn.preprocessing import StandardScaler
import warnings

class NSAEFeatureEngineer:
    def __init__(self, tickers: list, start_date: str, end_date: str = None):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self.raw_data = pd.DataFrame()
        self.features = pd.DataFrame()
        self.macro_data = pd.DataFrame()
        
    def fetch_data(self):
        print(f"Fetching data for {len(self.tickers)} tickers plus macro indicators (^VIX, HYG, TLT)...")
        df_list = []
        for ticker in self.tickers:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.download(ticker, start=self.start_date, end=self.end_date, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df['Ticker'] = ticker
                df_list.append(df)
                
        self.raw_data = pd.concat(df_list)
        self.raw_data.reset_index(inplace=True)
        self.raw_data.rename(columns={'Date': 'date', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
        
        # Fetch Macro data
        macro_tickers = ['HYG', 'TLT', 'DX-Y.NYB'] # DX-Y.NYB is the US Dollar Index
        macro_dfs = []
        for mt in macro_tickers:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mdf = yf.download(mt, start=self.start_date, end=self.end_date, progress=False)
            if not mdf.empty:
                if isinstance(mdf.columns, pd.MultiIndex):
                    mdf.columns = mdf.columns.droplevel(1)
                mdf['Ticker'] = mt
                macro_dfs.append(mdf)
                
        if macro_dfs:
            md = pd.concat(macro_dfs).reset_index()
            md.rename(columns={'Date': 'date', 'Close': 'close'}, inplace=True)
            self.macro_data = md.pivot(index='date', columns='Ticker', values='close').ffill()
            
            # Calculate Macro Features
            if 'DX-Y.NYB' in self.macro_data.columns:
                self.macro_data['dxy_mom'] = self.macro_data['DX-Y.NYB'].pct_change(20) # 1 month dollar momentum
            
            if 'HYG' in self.macro_data.columns and 'TLT' in self.macro_data.columns:
                self.macro_data['credit_spread_ratio'] = self.macro_data['HYG'] / self.macro_data['TLT']
                self.macro_data['credit_spread_mom'] = self.macro_data['credit_spread_ratio'].pct_change(20)
                
        return self.raw_data

    def generate_features(self):
        print("Generating TA features using ta...")
        feature_dfs = []
        
        for ticker, group in self.raw_data.groupby('Ticker'):
            df = group.copy()
            df.set_index('date', inplace=True)
            
            # ADX
            adx_ind = ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
            df['ADX'] = adx_ind.adx()
            
            # RSI
            rsi_ind = RSIIndicator(close=df['close'], window=14)
            df['RSI'] = rsi_ind.rsi()
            
            # Bollinger Bands width
            bb_ind = BollingerBands(close=df['close'], window=20, window_dev=2)
            df['BB_width'] = bb_ind.bollinger_wband()
            
            # Vol ratio
            df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()
            
            # OBV slope
            obv_ind = OnBalanceVolumeIndicator(close=df['close'], volume=df['volume'])
            df['OBV'] = obv_ind.on_balance_volume()
            df['OBV_slope'] = df['OBV'].diff(5)
            
            # SMAs for compatibility in gating
            sma50 = SMAIndicator(close=df['close'], window=50)
            df['SMA_50'] = sma50.sma_indicator()
            
            sma100 = SMAIndicator(close=df['close'], window=100)
            df['SMA_100'] = sma100.sma_indicator()
            
            df['Ticker'] = ticker
            
            # Merge macro data
            if not self.macro_data.empty:
                df = df.join(self.macro_data, how='left').ffill()
                
            feature_dfs.append(df.reset_index())
            
        self.features = pd.concat(feature_dfs).dropna()
        return self.features

    def calculate_continuous_signal(self):
        print("Calculating normalized continuous signals with expanding TimeSeriesSplit...")
        df = self.features.copy()
        df = df.sort_values(by='date')
        
        normalized_chunks = []
        min_periods = 30
        dates = df['date'].unique()
        
        if len(dates) <= min_periods:
            df['nsae_signal'] = 0
            self.features = df
            return self.features
            
        factors = ['ADX', 'RSI', 'BB_width', 'vol_ratio', 'OBV_slope']
        
        for i in range(min_periods, len(dates)):
            train_dates = dates[:i]
            current_date = dates[i]
            
            train_df = df[df['date'].isin(train_dates)]
            current_df = df[df['date'] == current_date].copy()
            
            scaler = StandardScaler()
            
            if len(train_df) > 1:
                scaler.fit(train_df[factors])
                scaled_current = scaler.transform(current_df[factors])
                raw_signal = np.sum(scaled_current, axis=1)
            else:
                raw_signal = 0
                
            current_df['raw_signal'] = raw_signal
            normalized_chunks.append(current_df)
            
        df_scored = pd.concat(normalized_chunks)
        
        expanding_min = df_scored['raw_signal'].expanding().min()
        expanding_max = df_scored['raw_signal'].expanding().max()
        denom = np.where((expanding_max - expanding_min) == 0, 1, (expanding_max - expanding_min))
        
        scaled_01 = (df_scored['raw_signal'] - expanding_min) / denom
        df_scored['nsae_signal'] = (scaled_01 * 2) - 1.0
        
        self.features = df_scored
        return self.features
