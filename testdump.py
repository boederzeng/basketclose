import streamlit as st
import ccxt
import time
import pandas as pd
import matplotlib.pyplot as plt
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import plotly.graph_objects as go
import random

# List of motivational quotes
motivational_quotes = [
    "The key to success is to focus on goals, not obstacles.",
    "Success is not final, failure is not fatal: It is the courage to continue that counts.",
    "The only limit to our realization of tomorrow will be our doubts of today.",
    "The stock market is a device for transferring money from the impatient to the patient.",
    "I never lose. I either win or learn.",
]


# Initialize PnL history
pnl_history = []

# Load configuration from environment variables or config file
api_key = st.secrets["binance_api_key"]
secret_key = st.secrets["binance_api_secret"]
telegram_token = st.secrets["telegram_bot_token"]
telegram_chat_id = st.secrets["telegram_chat_id"]

# Initialize Binance Futures client
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': secret_key,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'recvWindow': 30000,  # Increase the recvWindow (allowed time difference in ms)
    }
})



def synchronize_time(exchange):
    try:
        server_time = exchange.fetch_time()
        local_time = int(time.time() * 1000)
        time_difference = server_time - local_time
        exchange.options['adjustForTimeDifference'] = True  # Adjusts request timestamps
        exchange.options['timeDifference'] = time_difference  # Stores the time difference
        print(f"Time synchronized with server. Time difference: {time_difference} ms")
    except Exception as e:
        print(f"Failed to synchronize time with the server: {e}")

synchronize_time(exchange)


# Utility functions
def send_telegram_message(message):
    """Sends a message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    data = {"chat_id": telegram_chat_id, "text": message}
    requests.post(url, data=data)

def get_open_positions():
    """Fetches open positions from the exchange."""
    synchronize_time(exchange)
    balance = exchange.fetch_balance()
    all_positions = balance['info']['positions']
    open_positions = [pos for pos in all_positions if float(pos['positionAmt']) != 0]
    return open_positions


def close_position(position):
    """Closes a single position by trying limit orders first, then market order."""
    synchronize_time(exchange)
    symbol = position['symbol']
    amount = abs(float(position['positionAmt']))
    side = 'long' if float(position['positionAmt']) > 0 else 'short'
    order_side = 'sell' if side == 'long' else 'buy'

    try:
        orderbook = exchange.fetch_order_book(symbol)
        price = orderbook['asks'][0][0] if order_side == 'sell' else orderbook['bids'][0][0]
        adjusted_amount = exchange.amount_to_precision(symbol, amount)
        price = exchange.price_to_precision(symbol, price)

        # Place a post-only limit order
        params = {'timeInForce': 'GTX'}  # Ensure post-only
        order = exchange.create_order(symbol, 'limit', order_side, float(adjusted_amount), float(price), params)
        st.write(f"Limit order placed for {symbol} at {price} with amount {adjusted_amount}")

        # Wait for 10 seconds to check if the order is filled
        time.sleep(10)
        order_status = exchange.fetch_order(order['id'], symbol)['status']

        if order_status == 'closed':
            st.write(f"Limit order filled for {symbol}")
        else:
            st.write(f"Limit order not filled, cancelling and trying market order for {symbol}")
            exchange.cancel_order(order['id'], symbol)

            # Place a market order
            market_order = exchange.create_market_order(symbol, order_side, amount)
            st.write(f"Market order placed for {symbol}: {market_order}")

    except ccxt.NetworkError as e:
        st.error(f"Network error while closing position for {symbol}: {e}")
    except ccxt.ExchangeError as e:
        st.error(f"Exchange error while closing position for {symbol}: {e}")
    except Exception as e:
        st.error(f"Unexpected error while closing position for {symbol}: {e}")


def close_all_positions():
    """Closes all open positions with limit orders first, then market orders."""
    open_positions = get_open_positions()

    with ThreadPoolExecutor(max_workers=len(open_positions)) as executor:
        futures = []
        for pos in open_positions:
            future = executor.submit(close_position, pos)
            future.add_done_callback(lambda f: f.result() if f.exception() else None)
            futures.append(future)

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                st.error(f"Error closing position: {e}")

    if not get_open_positions():
        final_balance = exchange.fetch_balance()['total']['USDT']
        st.write(f"All positions have been successfully closed. Final Balance: ${final_balance:.2f}")
        send_telegram_message(
            f"Auto-Close Feature has successfully completed. All positions are closed. Final Balance: ${final_balance:.2f}")
    else:
        st.error("Failed to close all positions. Some positions may still be open.")
        send_telegram_message("Failed to close all positions. Some positions may still be open.")

def display_floating_pnl(tp_mode):
    """Displays the current floating PnL and calculates the take profit target."""
    # Fetch current target profit and TP percentage from session state
    synchronize_time(exchange)

    target_profit = st.session_state.get('target_profit', 0)
    tp_percentage = st.session_state.get('tp_percentage', 0)

    # Initialize or recalculate TP target if not set or if TP mode changes
    if 'tp_target_dollar' not in st.session_state or st.session_state.tp_mode != tp_mode:
        st.session_state.tp_mode = tp_mode
        current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
        if tp_mode == 'Percentage of Balance':
            st.session_state.tp_target_dollar = (tp_percentage / 100.0) * current_margin_balance
        else:
            st.session_state.tp_target_dollar = target_profit
    tp_target_dollar = st.session_state.tp_target_dollar

    open_positions = get_open_positions()
    total_pnl = sum(float(pos['unrealizedProfit']) for pos in open_positions)
    current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
    displayed_margin_balance = current_margin_balance + 300  # Add $300 to the displayed margin balance

    if total_pnl >= 0:
        motivational_message = "Great job! Keep up the good work!"
    else:
        motivational_message = "Don't worry, you've got this! Stay focused and keep pushing forward."

    pnl_placeholder.text(
        f"Current Total Unrealized PnL: ${total_pnl:.2f}\n"
        f"Current Margin Balance: ${displayed_margin_balance:.2f}\n"
        f"Calculated TP Target: ${tp_target_dollar:.2f}\n\n"
        f"{motivational_message}"
    )

    current_time = pd.Timestamp.now()
    pnl_history.append({'Time': current_time, 'Total PnL': total_pnl})

    position_details = [{'Symbol': pos['symbol'], 'Unrealized PnL': f"${float(pos['unrealizedProfit']):.2f}"} for pos in open_positions if float(pos['unrealizedProfit']) != 0]
    if position_details:
        df = pd.DataFrame(position_details)
        table_placeholder.table(df)
    else:
        table_placeholder.write("No open positions with non-zero PnL found.")

    return total_pnl, tp_target_dollar

def update_tp_target():
    if tp_mode == 'Fixed Dollar Amount':
        st.session_state.tp_target_dollar = st.session_state.target_profit
    elif tp_mode == 'Percentage of Balance':
        current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
        st.session_state.tp_target_dollar = (st.session_state.tp_percentage / 100.0) * current_margin_balance
    else:  # Target Margin Balance mode
        current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
        st.session_state.tp_target_dollar = st.session_state.target_margin_balance - current_margin_balance



def plot_pnl_history():
    """Plots the PnL history over time with profit/loss zones."""
    if pnl_history:
        df = pd.DataFrame(pnl_history)

        # Create a Plotly figure
        fig = go.Figure()

        # Add the PnL line trace
        fig.add_trace(go.Scatter(x=df['Time'], y=df['Total PnL'], mode='lines+markers', name='Total PnL'))

        # Add the take profit target line
        fig.add_shape(type='line', x0=df['Time'].min(), y0=st.session_state.tp_target_dollar,
                      x1=df['Time'].max(), y1=st.session_state.tp_target_dollar,
                      line=dict(color='red', width=2, dash='dash'), name='Take Profit Target')

        # Add profit and loss zones
        fig.add_shape(type='rect', x0=df['Time'].min(), y0=0, x1=df['Time'].max(), y1=df['Total PnL'].max(),
                      fillcolor='rgba(0, 255, 0, 0.2)', line=dict(width=0), name='Profit Zone')
        fig.add_shape(type='rect', x0=df['Time'].min(), y0=df['Total PnL'].min(), x1=df['Time'].max(), y1=0,
                      fillcolor='rgba(255, 0, 0, 0.2)', line=dict(width=0), name='Loss Zone')

        # Customize chart layout
        fig.update_layout(title='Total Unrealized PnL Over Time',
                          xaxis_title='Time',
                          yaxis_title='Total Unrealized PnL',
                          hovermode='x unified',
                          legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                          template='plotly_white')

        # Customize chart styling
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(0, 0, 0, 0.1)')
        fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(0, 0, 0, 0.1)')

        # Display the chart using Streamlit
        chart_placeholder.plotly_chart(fig, use_container_width=True)
    else:
        chart_placeholder.write("No PnL data to display.")

# Streamlit app
st.title("Basket-Close")
log_placeholder = st.empty()

# Initialize session state for auto-refresh
if 'last_refresh_time' not in st.session_state:
    st.session_state.last_refresh_time = time.time()

# Before your Streamlit app logic (where you define the radio button for tp_mode)
if 'tp_mode' not in st.session_state:
    st.session_state['tp_mode'] = 'Fixed Dollar Amount'  # Default value

# Later in your code, when defining the radio button
tp_mode = st.radio("Select Take Profit Mode", ('Fixed Dollar Amount', 'Percentage of Balance', 'Target Margin Balance'), index=('Fixed Dollar Amount', 'Percentage of Balance', 'Target Margin Balance').index(st.session_state.tp_mode))


tp_percentage = 0.0  # Default value for tp_percentage


if tp_mode == 'Fixed Dollar Amount':
    st.slider("Target Profit ($)", 1, 400, 10, key='target_profit')
elif tp_mode == 'Percentage of Balance':
    st.slider("Take Profit Percentage", 0.01, 100.0, 10.0, key='tp_percentage')
else:  # Target Margin Balance mode
    current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
    min_value = round(current_margin_balance, 2)
    max_value = round(current_margin_balance * 3, 2)
    default_value = round(current_margin_balance * 1.1, 2)
    st.slider("Target Margin Balance ($)", min_value, max_value, default_value, step=0.01, key='target_margin_balance')



st.session_state.tp_mode = tp_mode


auto_close = st.checkbox("Enable Auto-Close Feature")

# Sidebar for additional options
st.sidebar.title("Advanced Options")
enable_telegram_notifications = st.sidebar.checkbox("Enable Telegram Notifications", value=False)
telegram_update_interval = st.sidebar.number_input("Telegram Update Interval (seconds)", min_value=5, max_value=3600,
                                                   value=30, step=1)

pnl_placeholder = st.empty()
positions_expander = st.expander("Open Positions")
table_placeholder = positions_expander.empty()
pnl_chart_expander = st.expander("PnL Chart")
chart_placeholder = pnl_chart_expander.empty()

update_tp_target()

# Display a random motivational quote
motivational_quote = random.choice(motivational_quotes)
st.write(f"<i>'{motivational_quote}'</i>", unsafe_allow_html=True)

# Main app logic
if auto_close:
    st.write("Auto-Close Feature is ON. Monitoring PnL...")
    iteration_counter = 0
    time_since_last_update = 0
    while True:
        # Directly update TP target based on current slider values
        if tp_mode == 'Fixed Dollar Amount':
            tp_target_dollar = st.session_state.target_profit
        elif tp_mode == 'Percentage of Balance':
            current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
            tp_percentage = st.session_state.tp_percentage  # Make sure tp_percentage reflects the slider's current state
            tp_target_dollar = (tp_percentage / 100.0) * current_margin_balance
        else:  # Target Margin Balance mode
            current_margin_balance = float(exchange.fetch_balance()['total']['USDT'])
            tp_target_dollar = st.session_state.target_margin_balance - current_margin_balance

        total_pnl, tp_target_dollar = display_floating_pnl(tp_mode)
        plot_pnl_history()

        # Determine if it's time to send a Telegram update
        if enable_telegram_notifications and time_since_last_update >= telegram_update_interval:
            pnl_message = f"Current PnL Status: Total PnL: ${total_pnl:.2f}, Target: ${tp_target_dollar:.2f}"
            send_telegram_message(pnl_message)
            time_since_last_update = 0

        iteration_counter += 1
        time_since_last_update += 5

        if total_pnl >= tp_target_dollar:
            close_all_positions()
            break
        time.sleep(5)
else:
    st.write("Auto-Close Feature is OFF.")