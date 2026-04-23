import os
import sys

server_file = 'server.py'
with open(server_file, 'r') as f:
    content = f.read()

replacement = """                    import sec_financials
                    ticker = qs.get('ticker', ['SPY'])[0]
                    periods = int(qs.get('periods', [8])[0])
                    period_type = qs.get('type', ['Q'])[0]
                    data = sec_financials.fetch_financials(ticker, periods, period_type)
                    # Fallback to yahoo if sec returns nothing (e.g., for ADRs like BHP, RIO)
                    if not data.get('income'):
                        import yahoo_financials
                        y_data = yahoo_financials.get_financials(ticker, periods)
                        data['source'] = 'Yahoo Finance (ADR/Fallback)'
                        data['income'] = y_data.get('income', [])
                        data['balance'] = y_data.get('balance', [])
                        data['cashflow'] = y_data.get('cashflow', [])
                    return self.send_json(data)"""

content = content.replace("                    return self.send_json(sec_financials.fetch_financials(qs.get('ticker', ['SPY'])[0], int(qs.get('periods', [8])[0]), qs.get('type', ['Q'])[0]))", replacement)

with open(server_file, 'w') as f:
    f.write(content)
