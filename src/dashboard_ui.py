import os
import time
from typing import Any
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

CHART_INTERVAL_OPTIONS = {
    "1 minute": "1min",
    "5 minutes": "5min",
    "15 minutes": "15min",
}

HISTORY_WINDOW_OPTIONS = {
    "1 hour": pd.Timedelta(hours=1),
    "1 trading day": pd.Timedelta(hours=6, minutes=30),
    "Full session": None,
}

LOT_DISPLAY_OPTIONS = ("All lots", "Highest-risk lots")


class GridDashboardUI:
    @staticmethod
    def render_multi_lot_chart(
        time_history: list,
        price_history: list,
        open_lots: list[DashboardLot],
        chart_interval: str,
    ) -> go.Figure:
        fig = go.Figure()

        if not time_history or not price_history:
            logger.debug("Empty price history passed to chart render context. Displaying default axes.")
            fig.update_layout(title="Waiting for Ingestion Ticks...", template="plotly_dark")
            return fig

        # Convert raw tick data into user-selected OHLC Candlestick bars
        df_ticks = pd.DataFrame({'price': price_history}, index=pd.to_datetime(time_history))
        df_ohlc = df_ticks['price'].resample(chart_interval).ohlc().dropna()

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
            title=f"Volatility Harvesting Grid Stack ({chart_interval} Candlesticks)",
            xaxis_title="Time Frame", 
            yaxis_title="Price ($)", 
            template="plotly_dark",
            margin=dict(r=150),
            xaxis_rangeslider_visible=False
        )
        return fig

    @staticmethod
    def generate_live_order_ledger(
        open_lots: list[DashboardLot],
        current_price: float,
        last_buy_price: float,
        grid_step: float,
        currency_precision: int,
        current_timestamp,
    ) -> pd.DataFrame:
        ledger_data = []
        currency_format = f"${{:.{currency_precision}f}}"
        next_buy_target = last_buy_price * (1.0 - grid_step)
        dist_to_grid_drop = current_price - next_buy_target
        dist_to_grid_drop_pct = safe_ratio(dist_to_grid_drop, current_price)

        for lot in open_lots:
            notional_exposure = lot.buy_price * lot.shares
            unrealized_profit = (current_price - lot.buy_price) * lot.shares
            unrealized_profit_pct = safe_ratio(current_price - lot.buy_price, lot.buy_price)
            dist_to_target = lot.target_sell_price - current_price
            dist_to_target_pct = safe_ratio(dist_to_target, current_price)
            ledger_data.append({
                "Lot ID": lot.lot_id,
                "Age": format_lot_age(lot, current_timestamp),
                "Notional Exposure": currency_format.format(notional_exposure),
                "Buy Price": currency_format.format(lot.buy_price),
                "Target Exit": currency_format.format(lot.target_sell_price),
                "Unrealized P&L": currency_format.format(unrealized_profit),
                "Unrealized P&L %": format_percent(unrealized_profit_pct),
                "Dist to Grid Drop": currency_format.format(dist_to_grid_drop),
                "Dist to Grid Drop %": format_percent(dist_to_grid_drop_pct),
                "Dist to Target": currency_format.format(dist_to_target),
                "Dist to Target %": format_percent(dist_to_target_pct),
                "_sort_unrealized_profit": unrealized_profit,
            })

        if not ledger_data:
            return pd.DataFrame(ledger_data)

        return (
            pd.DataFrame(ledger_data)
            .sort_values("_sort_unrealized_profit", ascending=True)
            .drop(columns=["_sort_unrealized_profit"])
        )


def safe_ratio(numerator: float, denominator: float) -> float | None:
    """Return a numeric ratio, or None when the denominator cannot support a decision-safe percentage."""
    if denominator == 0:
        return None

    return numerator / denominator


def format_percent(value: float | None) -> str:
    """Format a ratio as a percent for ledger display."""
    if value is None:
        return "N/A"

    return f"{value:.2%}"


def format_lot_age(lot: DashboardLot, current_timestamp) -> str:
    """Format lot age from existing bridge timestamps, falling back gracefully when unavailable."""
    lot_age_seconds = getattr(lot, "age_seconds", None)
    if lot_age_seconds is None:
        lot_timestamp = getattr(lot, "timestamp", None)
        if lot_timestamp is None or current_timestamp is None:
            return "Unknown"

        lot_age_seconds = (
            pd.to_datetime(current_timestamp) - pd.to_datetime(lot_timestamp)
        ).total_seconds()

    if lot_age_seconds < 0:
        return "Unknown"

    days, remainder = divmod(int(lot_age_seconds), 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def render_display_preferences(container) -> dict[str, Any]:
    """Render user-configurable display preferences for chart, history, precision, and lot scope."""
    with container.container():
        with st.expander("Display Preferences", expanded=False):
            chart_label = st.selectbox(
                "Chart interval",
                list(CHART_INTERVAL_OPTIONS.keys()),
                index=0,
            )
            history_label = st.selectbox(
                "History window",
                list(HISTORY_WINDOW_OPTIONS.keys()),
                index=1,
            )
            currency_precision = st.number_input(
                "Currency precision",
                min_value=0,
                max_value=4,
                value=2,
                step=1,
                help="Controls the number of decimal places used for dollar values in metrics and the ledger.",
            )
            lot_display = st.radio(
                "Lot display",
                LOT_DISPLAY_OPTIONS,
                index=0,
                help="Show every open lot, or focus charts and ledger on the open lots with the worst unrealized P&L.",
            )
            high_risk_lot_limit = st.number_input(
                "Highest-risk lot count",
                min_value=1,
                max_value=100,
                value=10,
                step=1,
                disabled=lot_display == "All lots",
            )

    return {
        "chart_interval": CHART_INTERVAL_OPTIONS[chart_label],
        "history_window": HISTORY_WINDOW_OPTIONS[history_label],
        "currency_precision": int(currency_precision),
        "lot_display": lot_display,
        "high_risk_lot_limit": int(high_risk_lot_limit),
    }


def trim_history_to_window(time_history: list, price_history: list, history_window: pd.Timedelta | None) -> None:
    """Trim in-place histories to the selected recent window while preserving full-session mode."""
    if history_window is None or not time_history:
        return

    latest_timestamp = pd.to_datetime(time_history[-1])
    earliest_allowed = latest_timestamp - history_window

    while time_history and pd.to_datetime(time_history[0]) < earliest_allowed:
        time_history.pop(0)
        price_history.pop(0)


def select_lots_for_display(
    open_lots: list[DashboardLot],
    current_price: float,
    lot_display: str,
    high_risk_lot_limit: int,
) -> list[DashboardLot]:
    """Return all lots or the lots with the worst current unrealized P&L for focused display."""
    if lot_display == "All lots":
        return open_lots

    return sorted(
        open_lots,
        key=lambda lot: (current_price - lot.buy_price) * lot.shares,
    )[:high_risk_lot_limit]


def format_currency(value: float, precision: int) -> str:
    """Format a dollar value with the selected display precision."""
    return f"${value:.{precision}f}"


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
    display_preferences = st.sidebar.empty()
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

            preferences = render_display_preferences(display_preferences)
            render_command_controls(command_controls, ws, state.grid_step)

            if not time_history or time_history[-1] != state_timestamp:
                time_history.append(state_timestamp)
                price_history.append(current_price)

                trim_history_to_window(time_history, price_history, preferences["history_window"])

            capital_in_open_lots = sum(lot.buy_price * lot.shares for lot in open_lots)
            cycles = state.closed_lots_count
            stuck_count = len(open_lots)
            closed_cycles_per_open_lot = cycles / stuck_count if stuck_count > 0 else float(cycles)

            num_sells = cycles
            num_buys = stuck_count + cycles
            estimated_orders = num_buys + num_sells
            
            realized_profit = state.realized_profit
            unrealized_profit = sum((current_price - lot.buy_price) * lot.shares for lot in open_lots)
            total_profit = realized_profit + unrealized_profit
            
            currency_precision = preferences["currency_precision"]
            display_lots = select_lots_for_display(
                open_lots,
                current_price,
                preferences["lot_display"],
                preferences["high_risk_lot_limit"],
            )

            metric_price.metric(
                "Live Ticker",
                format_currency(current_price, currency_precision),
                help="Most recent price received from the backend state payload.",
            )
            metric_active_lots.metric(
                "Active Inventory Lots",
                f"{stuck_count}",
                help="Number of currently open inventory lots reported by the backend.",
            )
            metric_stuck.metric(
                "Capital in Open Lots",
                format_currency(capital_in_open_lots, currency_precision),
                help="Notional capital currently tied to open lots, calculated as buy price multiplied by shares.",
            )
            metric_velocity.metric(
                "Closed Cycles per Open Lot",
                f"{closed_cycles_per_open_lot:.2f}",
                help="Closed lot cycles divided by current open lots; shown as a strategy diagnostic rather than a risk limit.",
            )
            
            realized_str = (
                f"+{format_currency(realized_profit, currency_precision)} Realized"
                if realized_profit >= 0
                else f"-{format_currency(abs(realized_profit), currency_precision)} Realized"
            )
            metric_profit.metric(
                "Total P&L",
                format_currency(total_profit, currency_precision),
                realized_str,
                delta_color="normal",
                help="Realized plus locally calculated unrealized P&L for the currently open lots.",
            )
            metric_total_orders.metric(
                "Estimated Orders",
                f"{estimated_orders}",
                help="Estimated from open lots plus closed lots because exact order counts are not currently provided by volatility-bridge.",
            )
            metric_buys.metric(
                "Estimated Buys",
                f"{num_buys}",
                help="Estimated as current open lots plus closed cycles until exact buy counts are provided by volatility-bridge.",
            )
            metric_sells.metric(
                "Estimated Sells",
                f"{num_sells}",
                help="Estimated from closed lot cycles until exact sell counts are provided by volatility-bridge.",
            )
            
            # Pass strictly typed models to our renderer
            fig = GridDashboardUI.render_multi_lot_chart(
                time_history,
                price_history,
                display_lots,
                preferences["chart_interval"],
            )
            
            render_counter += 1
            chart_placeholder.plotly_chart(
                fig, 
                use_container_width=True, 
                key=f"live_multi_lot_chart_{render_counter}"
            )
            
            df = GridDashboardUI.generate_live_order_ledger(
                display_lots,
                current_price,
                state.last_buy_price,
                state.grid_step,
                currency_precision,
                state_timestamp,
            )
            if not df.empty:
                with table_placeholder.container():
                    if preferences["lot_display"] == "Highest-risk lots" and len(open_lots) > len(display_lots):
                        st.caption(
                            f"Showing {len(display_lots)} highest-risk lots out of {len(open_lots)} open lots."
                        )
                    st.dataframe(df, use_container_width=True)
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
