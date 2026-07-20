import time
import os
import yaml
import logging
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from websocket import create_connection, WebSocketTimeoutException, WebSocketConnectionClosedException

from models import (
    DashboardLot,
    DashboardStatePayload,
    UICommandEmergencyHalt,
    UICommandLiquidateAll,
    UICommandMessage,
    UICommandResumeTrading,
    UICommandUpdateConfig,
)

# Setup dashboard log targets
logger = logging.getLogger("DashboardUI")

class GridDashboardUI:
    @staticmethod
    def render_multi_lot_chart(time_history: list, price_history: list, open_lots: list[DashboardLot]) -> go.Figure:
        fig = go.Figure()

        if not time_history or not price_history:
            logger.debug("Empty price history passed to chart render context. Displaying default axes.")
            fig.update_layout(title="Waiting for Ingestion Ticks...", template="plotly_dark")
            return fig

        # Convert raw tick data into 1-minute OHLC Candlestick bars
        df_ticks = pd.DataFrame({'price': price_history}, index=pd.to_datetime(time_history))
        df_ohlc = df_ticks['price'].resample('1min').ohlc().dropna()

        # Plot Live Candlesticks
        fig.add_trace(go.Candlestick(
            x=df_ohlc.index,
            open=df_ohlc['open'],
            high=df_ohlc['high'],
            low=df_ohlc['low'],
            close=df_ohlc['close'],
            name='TQQQ Price',
            increasing_line_color='#2ecc71',
            decreasing_line_color='#e74c3c'
        ))

        current_time = time_history[-1]
        start_time = time_history[0]

        for lot in open_lots:
            # We now safely access properties directly from the Pydantic model
            lot_start = lot.timestamp if lot.timestamp else start_time

            fig.add_shape(
                type="rect",
                x0=lot_start, y0=lot.buy_price,
                x1=current_time, y1=lot.target_sell_price,
                fillcolor="rgba(46, 204, 113, 0.1)",
                line=dict(color="rgba(255, 255, 255, 0)"),
                layer="below"
            )

            fig.add_shape(
                type="line", x0=lot_start, y0=lot.buy_price, x1=current_time, y1=lot.buy_price,
                line=dict(color="royalblue", width=1.5, dash="dash")
            )
            
            fig.add_shape(
                type="line", x0=lot_start, y0=lot.target_sell_price, x1=current_time, y1=lot.target_sell_price,
                line=dict(color="#2ecc71", width=1.5)
            )

            fig.add_annotation(
                x=current_time, y=lot.buy_price, text=f"Buy: {lot.lot_id[:6]}", 
                showarrow=False, xanchor="left", font=dict(color="royalblue", size=10)
            )
            fig.add_annotation(
                x=current_time, y=lot.target_sell_price, text=f"Target: {lot.lot_id[:6]}", 
                showarrow=False, xanchor="left", font=dict(color="#2ecc71", size=10)
            )

        fig.update_layout(
            title="Volatility Harvesting Grid Stack (1-Min Candlesticks)", 
            xaxis_title="Time Frame", 
            yaxis_title="Price ($)", 
            template="plotly_dark",
            margin=dict(r=150),
            xaxis_rangeslider_visible=False
        )
        return fig

    @staticmethod
    def generate_live_order_ledger(open_lots: list[DashboardLot], current_price: float, last_buy_price: float, grid_step: float) -> pd.DataFrame:
        ledger_data = []
        next_buy_target = last_buy_price * (1.0 - grid_step)
        dist_to_grid_drop = current_price - next_buy_target
        
        for lot in open_lots:
            dist_to_target = lot.target_sell_price - current_price
            ledger_data.append({
                "Lot ID": lot.lot_id,
                "Buy Price": f"${lot.buy_price:.2f}",
                "Target Exit": f"${lot.target_sell_price:.2f}",
                "Dist to Grid Drop": f"${dist_to_grid_drop:.2f}",
                "Dist to Target": f"${dist_to_target:.2f}"
            })
            
        return pd.DataFrame(ledger_data)


def send_ui_command(ws, command: UICommandMessage) -> None:
    """Send a typed UI command to the backend over the active websocket."""
    ws.send(command.model_dump_json(exclude_none=True))


def render_command_controls(container, ws, grid_step: float | None = None) -> None:
    """Render backend control commands backed by shared bridge models."""
    with container.container():
        st.header("Trading Controls")

        if ws is None:
            st.info("Controls enable after the dashboard connects to the backend websocket.")
            return

        if st.button("Emergency Halt"):
            send_ui_command(ws, UICommandEmergencyHalt(command="emergency_halt"))
            st.success("Emergency halt command sent.")

        if st.button("Resume Trading"):
            send_ui_command(ws, UICommandResumeTrading(command="resume_trading"))
            st.success("Resume trading command sent.")

        if st.button("Liquidate All"):
            send_ui_command(ws, UICommandLiquidateAll(command="liquidate_all"))
            st.success("Liquidate all command sent.")

        st.subheader("Update Config")
        default_grid_step = grid_step if grid_step is not None else 0.01
        new_grid_step = st.number_input(
            "Grid step",
            min_value=0.0001,
            max_value=1.0,
            value=float(default_grid_step),
            step=0.0001,
            format="%.4f",
        )
        new_profit_target = st.number_input(
            "Profit target",
            min_value=0.0001,
            max_value=1.0,
            value=0.01,
            step=0.0001,
            format="%.4f",
        )

        if st.button("Apply Config"):
            send_ui_command(
                ws,
                UICommandUpdateConfig(
                    command="update_config",
                    new_grid_step=new_grid_step,
                    new_profit_target=new_profit_target,
                ),
            )
            st.success("Config update command sent.")

def main():
    st.set_page_config(layout="wide", page_title="AI Volatility Harvester")
    st.title("AI Volatility Harvesting Dashboard")
    
    col1, col2, col3, col4 = st.columns(4)
    metric_price = col1.empty()
    metric_active_lots = col2.empty()
    metric_stuck = col3.empty()
    metric_velocity = col4.empty()
    
    col5, col6, col7, col8 = st.columns(4)
    metric_profit = col5.empty()
    metric_total_orders = col6.empty()
    metric_buys = col7.empty()
    metric_sells = col8.empty()
    
    chart_placeholder = st.empty()
    table_placeholder = st.empty()
    command_controls = st.sidebar.empty()

    time_history = []
    price_history = []
    render_counter = 0

    ws_port = 8765
    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml", "r") as f:
                config = yaml.safe_load(f)
                ws_port = config.get("system", {}).get("websocket_port", 8765)
        except Exception:
            pass

    ws_url = f"ws://127.0.0.1:{ws_port}"
    ws = None
    connected = False

    while True:
        try:
            # 1. Implement Timeout-Aware Connection Manager
            if not connected or ws is None:
                logger.info(f"Connecting to State Broadcaster at {ws_url}...")
                ws = create_connection(ws_url, timeout=5) # 5s timeout limits hanging during initial handshake
                connected = True
                logger.info("Successfully connected to live algorithm stream!")

            # 2. Block smartly. If no tick arrives in 10 seconds (e.g. illiquid or market close),
            # this raises an exception to keep the Streamlit thread actively looping and healthy.
            ws.settimeout(10.0)
            message = ws.recv()

            # 3. Pydantic Type Validation directly upon receipt
            state = DashboardStatePayload.model_validate_json(message)

            current_price = state.current_price
            open_lots = state.open_lots
            state_timestamp = state.timestamp

            render_command_controls(command_controls, ws, state.grid_step)

            if not time_history or time_history[-1] != state_timestamp:
                time_history.append(state_timestamp)
                price_history.append(current_price)

                if len(time_history) > 3600:
                    time_history.pop(0)
                    price_history.pop(0)
            
            stuck_capital = sum(lot.buy_price * lot.shares for lot in open_lots)
            cycles = state.closed_lots_count
            stuck_count = len(open_lots)
            velocity = cycles / stuck_count if stuck_count > 0 else float(cycles)
            
            num_sells = cycles
            num_buys = stuck_count + cycles
            total_orders = num_buys + num_sells
            
            realized_profit = state.realized_profit
            unrealized_profit = sum((current_price - lot.buy_price) * lot.shares for lot in open_lots)
            total_profit = realized_profit + unrealized_profit
            
            metric_price.metric("Live Ticker", f"${current_price:.2f}")
            metric_active_lots.metric("Active Inventory Lots", f"{stuck_count}")
            metric_stuck.metric("Stuck Capital Value", f"${stuck_capital:.2f}")
            metric_velocity.metric("Capital Velocity Index", f"{velocity:.2f} Cycles/Lot")
            
            realized_str = f"+${realized_profit:.2f} Realized" if realized_profit >= 0 else f"-${abs(realized_profit):.2f} Realized"
            metric_profit.metric("Today's Profit (Total)", f"${total_profit:.2f}", realized_str, delta_color="normal")
            metric_total_orders.metric("Total Completed Orders", f"{total_orders}")
            metric_buys.metric("Number of Buys", f"{num_buys}")
            metric_sells.metric("Number of Sells", f"{num_sells}")
            
            # Pass strictly typed models to our renderer
            fig = GridDashboardUI.render_multi_lot_chart(time_history, price_history, open_lots)
            
            render_counter += 1
            chart_placeholder.plotly_chart(
                fig, 
                use_container_width=True, 
                key=f"live_multi_lot_chart_{render_counter}"
            )
            
            df = GridDashboardUI.generate_live_order_ledger(open_lots, current_price, state.last_buy_price, state.grid_step)
            if not df.empty:
                table_placeholder.dataframe(df, use_container_width=True)
            else:
                table_placeholder.info("No open lots sitting in inventory. Waiting for grid drop.")

        except WebSocketTimeoutException:
            # Normal behavior in a low-volume market. We simply catch it and pass to ensure 
            # our UI remains healthy and doesn't crash from a silent network drop.
            continue
        except (WebSocketConnectionClosedException, ConnectionRefusedError, OSError):
            st.warning("Awaiting State Broadcaster Connection... Is the main trading script running?")
            if ws:
                ws.close()
            ws = None
            connected = False
            time.sleep(3)
        except Exception as e:
            logger.error(f"Error parsing state bridge payload: {e}", exc_info=True)
            st.error(f"Error reading state: {e}")
            connected = False
            time.sleep(2)

if __name__ == "__main__":
    main()
