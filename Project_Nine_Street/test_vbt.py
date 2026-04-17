import vectorbt as vbt
import pandas as pd
import numpy as np

close = pd.Series([100, 105, 102, 108, 110])
weights = pd.Series([0.5, 0.5, 0.0, 1.0, 1.0])
pf = vbt.Portfolio.from_orders(close, size=weights, size_type='targetpercent')
print(pf.value())
