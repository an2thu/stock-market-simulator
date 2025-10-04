import yfinance as yf
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # For non-GUI backend
from flask import Flask, render_template, request, jsonify
import json

app = Flask(__name__)

DATABASE = 'stock_market.db'

# Initialize database
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    
    # Stock prices table
    c.execute('''CREATE TABLE IF NOT EXISTS stock_prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        date DATE NOT NULL,
        open_price REAL,
        high_price REAL,
        low_price REAL,
        close_price REAL,
        volume INTEGER,
        UNIQUE(ticker, date)
    )''')
    
    # Technical indicators table
    c.execute('''CREATE TABLE IF NOT EXISTS technical_indicators (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        date DATE NOT NULL,
        sma_20 REAL,
        sma_50 REAL,
        daily_return REAL,
        volatility REAL,
        UNIQUE(ticker, date)
    )''')
    
    # Portfolio table
    c.execute('''CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        shares INTEGER NOT NULL,
        purchase_price REAL NOT NULL,
        purchase_date DATE NOT NULL
    )''')
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# Fetch stock data from Yahoo Finance
@app.route('/api/fetch-stock/<ticker>', methods=['POST'])
def fetch_stock_data(ticker):
    try:
        # Get 12 months of data
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        
        stock = yf.Ticker(ticker)
        df = stock.history(start=start_date, end=end_date)
        
        if df.empty:
            return jsonify({'error': 'No data found for ticker'}), 404
        
        conn = get_db()
        
        # Store stock prices
        for date, row in df.iterrows():
            conn.execute('''INSERT OR REPLACE INTO stock_prices 
                           (ticker, date, open_price, high_price, low_price, close_price, volume)
                           VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (ticker.upper(), date.strftime('%Y-%m-%d'), 
                         row['Open'], row['High'], row['Low'], row['Close'], row['Volume']))
        
        # Calculate technical indicators
        df['SMA_20'] = df['Close'].rolling(window=20).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        df['Daily_Return'] = df['Close'].pct_change()
        df['Volatility'] = df['Daily_Return'].rolling(window=20).std()
        
        # Store indicators
        for date, row in df.iterrows():
            if pd.notna(row['SMA_20']):
                conn.execute('''INSERT OR REPLACE INTO technical_indicators 
                               (ticker, date, sma_20, sma_50, daily_return, volatility)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                            (ticker.upper(), date.strftime('%Y-%m-%d'),
                             row['SMA_20'], row['SMA_50'], 
                             row['Daily_Return'], row['Volatility']))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'Data fetched for {ticker}',
            'records': len(df)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Get stock price history
@app.route('/api/stock/<ticker>/history', methods=['GET'])
def get_stock_history(ticker):
    conn = get_db()
    
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = 'SELECT * FROM stock_prices WHERE ticker = ?'
    params = [ticker.upper()]
    
    if start_date:
        query += ' AND date >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND date <= ?'
        params.append(end_date)
    
    query += ' ORDER BY date'
    
    prices = conn.execute(query, params).fetchall()
    conn.close()
    
    return jsonify([dict(p) for p in prices])

# Get technical indicators
@app.route('/api/stock/<ticker>/indicators', methods=['GET'])
def get_technical_indicators(ticker):
    conn = get_db()
    
    indicators = conn.execute('''
        SELECT ti.*, sp.close_price
        FROM technical_indicators ti
        JOIN stock_prices sp ON ti.ticker = sp.ticker AND ti.date = sp.date
        WHERE ti.ticker = ?
        ORDER BY ti.date DESC
        LIMIT 100
    ''', (ticker.upper(),)).fetchall()
    conn.close()
    
    return jsonify([dict(i) for i in indicators])

# Portfolio management
@app.route('/api/portfolio', methods=['GET', 'POST'])
def manage_portfolio():
    if request.method == 'POST':
        data = request.json
        conn = get_db()
        conn.execute('''INSERT INTO portfolio (ticker, shares, purchase_price, purchase_date)
                       VALUES (?, ?, ?, ?)''',
                    (data['ticker'].upper(), data['shares'], 
                     data['purchase_price'], data['purchase_date']))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Stock added to portfolio'})
    
    else:
        conn = get_db()
        portfolio = conn.execute('''
            SELECT p.*, sp.close_price as current_price,
                   (sp.close_price - p.purchase_price) * p.shares as profit_loss,
                   ((sp.close_price - p.purchase_price) / p.purchase_price) * 100 as return_pct
            FROM portfolio p
            LEFT JOIN (
                SELECT ticker, close_price, date
                FROM stock_prices
                WHERE (ticker, date) IN (
                    SELECT ticker, MAX(date)
                    FROM stock_prices
                    GROUP BY ticker
                )
            ) sp ON p.ticker = sp.ticker
        ''').fetchall()
        conn.close()
        
        return jsonify([dict(p) for p in portfolio])

# Portfolio analytics
@app.route('/api/portfolio/analytics', methods=['GET'])
def portfolio_analytics():
    conn = get_db()
    
    portfolio = conn.execute('''
        SELECT p.ticker, p.shares, p.purchase_price, p.purchase_date,
               sp.close_price as current_price
        FROM portfolio p
        LEFT JOIN (
            SELECT ticker, close_price, date
            FROM stock_prices
            WHERE (ticker, date) IN (
                SELECT ticker, MAX(date)
                FROM stock_prices
                GROUP BY ticker
            )
        ) sp ON p.ticker = sp.ticker
    ''').fetchall()
    
    if not portfolio:
        return jsonify({'error': 'Portfolio is empty'}), 404
    
    total_investment = sum(p['purchase_price'] * p['shares'] for p in portfolio)
    total_current_value = sum((p['current_price'] or 0) * p['shares'] for p in portfolio)
    total_profit_loss = total_current_value - total_investment
    total_return_pct = (total_profit_loss / total_investment) * 100 if total_investment > 0 else 0
    
    # Calculate daily returns for portfolio
    tickers = [p['ticker'] for p in portfolio]
    weights = {p['ticker']: (p['shares'] * (p['current_price'] or 0)) / total_current_value 
               for p in portfolio if p['current_price']}
    
    # Get historical returns
    all_returns = []
    for ticker in tickers:
        returns = conn.execute('''
            SELECT date, daily_return
            FROM technical_indicators
            WHERE ticker = ? AND daily_return IS NOT NULL
            ORDER BY date DESC
            LIMIT 30
        ''', (ticker,)).fetchall()
        
        if returns:
            weighted_returns = [r['daily_return'] * weights.get(ticker, 0) for r in returns]
            all_returns.append(weighted_returns)
    
    conn.close()
    
    # Calculate portfolio volatility
    if all_returns:
        portfolio_returns = np.sum(all_returns, axis=0)
        portfolio_volatility = np.std(portfolio_returns) * np.sqrt(252)  # Annualized
    else:
        portfolio_volatility = 0
    
    return jsonify({
        'total_investment': float(total_investment),
        'current_value': float(total_current_value),
        'profit_loss': float(total_profit_loss),
        'return_percentage': float(total_return_pct),
        'volatility': float(portfolio_volatility),
        'holdings_count': len(portfolio)
    })

# Stock comparison
@app.route('/api/compare', methods=['POST'])
def compare_stocks():
    tickers = request.json.get('tickers', [])
    
    if not tickers or len(tickers) < 2:
        return jsonify({'error': 'Provide at least 2 tickers'}), 400
    
    conn = get_db()
    comparison = []
    
    for ticker in tickers:
        # Get latest price
        latest = conn.execute('''
            SELECT close_price, date
            FROM stock_prices
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT 1
        ''', (ticker.upper(),)).fetchone()
        
        if not latest:
            continue
        
        # Get 30-day performance
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        old_price = conn.execute('''
            SELECT close_price
            FROM stock_prices
            WHERE ticker = ? AND date >= ?
            ORDER BY date
            LIMIT 1
        ''', (ticker.upper(), thirty_days_ago)).fetchone()
        
        # Calculate metrics
        performance_30d = 0
        if old_price:
            performance_30d = ((latest['close_price'] - old_price['close_price']) / 
                              old_price['close_price']) * 100
        
        # Get average volatility
        avg_volatility = conn.execute('''
            SELECT AVG(volatility) as avg_vol
            FROM technical_indicators
            WHERE ticker = ? AND volatility IS NOT NULL
        ''', (ticker.upper(),)).fetchone()
        
        comparison.append({
            'ticker': ticker.upper(),
            'current_price': latest['close_price'],
            'performance_30d': float(performance_30d),
            'avg_volatility': float(avg_volatility['avg_vol'] or 0)
        })
    
    conn.close()
    return jsonify(comparison)

# Backtesting simple strategy
@app.route('/api/backtest/<ticker>', methods=['POST'])
def backtest_strategy(ticker):
    data = request.json
    strategy = data.get('strategy', 'sma_crossover')
    initial_capital = data.get('initial_capital', 10000)
    
    conn = get_db()
    
    # Get historical data with indicators
    stock_data = conn.execute('''
        SELECT sp.date, sp.close_price, ti.sma_20, ti.sma_50
        FROM stock_prices sp
        JOIN technical_indicators ti ON sp.ticker = ti.ticker AND sp.date = ti.date
        WHERE sp.ticker = ? AND ti.sma_50 IS NOT NULL
        ORDER BY sp.date
    ''', (ticker.upper(),)).fetchall()
    
    conn.close()
    
    if not stock_data:
        return jsonify({'error': 'Insufficient data for backtesting'}), 404
    
    # Simple SMA crossover strategy
    capital = initial_capital
    shares = 0
    trades = []
    
    for i in range(1, len(stock_data)):
        prev = stock_data[i-1]
        curr = stock_data[i]
        
        # Buy signal: SMA 20 crosses above SMA 50
        if (prev['sma_20'] <= prev['sma_50'] and 
            curr['sma_20'] > curr['sma_50'] and 
            shares == 0):
            shares = capital // curr['close_price']
            capital -= shares * curr['close_price']
            trades.append({
                'date': curr['date'],
                'action': 'BUY',
                'price': curr['close_price'],
                'shares': shares
            })
        
        # Sell signal: SMA 20 crosses below SMA 50
        elif (prev['sma_20'] >= prev['sma_50'] and 
              curr['sma_20'] < curr['sma_50'] and 
              shares > 0):
            capital += shares * curr['close_price']
            trades.append({
                'date': curr['date'],
                'action': 'SELL',
                'price': curr['close_price'],
                'shares': shares
            })
            shares = 0
    
    # Calculate final portfolio value
    final_price = stock_data[-1]['close_price']
    final_value = capital + (shares * final_price)
    total_return = ((final_value - initial_capital) / initial_capital) * 100
    
    return jsonify({
        'initial_capital': initial_capital,
        'final_value': float(final_value),
        'total_return': float(total_return),
        'trades': trades,
        'total_trades': len(trades)
    })

# Generate chart
@app.route('/api/chart/<ticker>', methods=['GET'])
def generate_chart(ticker):
    conn = get_db()
    
    # Get last 90 days of data
    ninety_days_ago = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    
    data = conn.execute('''
        SELECT sp.date, sp.close_price, ti.sma_20, ti.sma_50
        FROM stock_prices sp
        LEFT JOIN technical_indicators ti ON sp.ticker = ti.ticker AND sp.date = ti.date
        WHERE sp.ticker = ? AND sp.date >= ?
        ORDER BY sp.date
    ''', (ticker.upper(), ninety_days_ago)).fetchall()
    
    conn.close()
    
    if not data:
        return jsonify({'error': 'No data available'}), 404
    
    # Create chart
    df = pd.DataFrame([dict(d) for d in data])
    df['date'] = pd.to_datetime(df['date'])
    
    plt.figure(figsize=(12, 6))
    plt.plot(df['date'], df['close_price'], label='Close Price', linewidth=2)
    
    if df['sma_20'].notna().any():
        plt.plot(df['date'], df['sma_20'], label='SMA 20', linestyle='--', alpha=0.7)
    if df['sma_50'].notna().any():
        plt.plot(df['date'], df['sma_50'], label='SMA 50', linestyle='--', alpha=0.7)
    
    plt.xlabel('Date')
    plt.ylabel('Price ($)')
    plt.title(f'{ticker.upper()} Stock Price - Last 90 Days')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    # Save chart
    chart_path = f'static/charts/{ticker.lower()}_chart.png'
    plt.savefig(chart_path, dpi=100, bbox_inches='tight')
    plt.close()
    
    return jsonify({'chart_url': f'/{chart_path}'})

# Get available stocks
@app.route('/api/stocks', methods=['GET'])
def get_stocks():
    conn = get_db()
    stocks = conn.execute('''
        SELECT DISTINCT ticker, MAX(date) as last_updated
        FROM stock_prices
        GROUP BY ticker
        ORDER BY ticker
    ''').fetchall()
    conn.close()
    
    return jsonify([dict(s) for s in stocks])

# Home page
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    init_db()
    # Create charts directory if it doesn't exist
    import os
    os.makedirs('static/charts', exist_ok=True)
    app.run(debug=True, port=5002)
