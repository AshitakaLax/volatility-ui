# Risk Assessment and UX Review

This review evaluates the current Streamlit dashboard as a financial-risk workstation, Python application, and user experience for a volatility harvesting strategy. It separates changes that can be made inside this UI from upstream schema/API work that must be implemented in `volatility-bridge` so the UI and backend remain contract-aligned.

## Executive summary

The dashboard already provides a useful real-time loop: current price, open-lot inventory, realized/unrealized profit, a candlestick chart, open-lot overlays, a lot ledger, and basic backend controls. The main gap is that the current view is operational rather than risk-oriented. A user can see activity, but cannot quickly answer the most important risk questions:

- How much capital is currently exposed, and how much buying power remains?
- What is the portfolio drawdown today and since strategy start?
- How close is the strategy to configured risk limits?
- Which lot, price level, or market regime is driving risk?
- Are controls safe enough for destructive actions such as emergency halt or liquidation?

## Current UI observations

- The main page starts with eight metric cards for price, active lots, stuck capital, velocity, profit, and order counts.
- Price history is retained in memory and rendered as 1-minute OHLC candlesticks with open-lot buy and target overlays.
- The order ledger shows per-lot buy price, target exit, distance to the next grid drop, and distance to target.
- Sidebar controls can send emergency halt, resume trading, liquidate all, and grid/profit target updates over the active websocket.
- Configuration discovery is limited to `system.websocket_port` in a local `config.yaml` file.

## Recommended UI-only updates

These improvements can be implemented in `volatility-ui` without requiring new fields from `volatility-bridge`.

### 1. Add risk-first grouping and alert hierarchy

Reorder the dashboard into dedicated sections:

1. **Connection and trading status**: websocket state, last payload timestamp, data staleness, backend mode.
2. **Risk summary**: capital exposed, unrealized P&L, realized P&L, total P&L, open lots, max adverse open lot.
3. **Strategy activity**: buys, sells, closed lots, grid velocity.
4. **Charts and diagnostics**: price, lots, targets, and ledger.

Use color semantics consistently:

- Green: profitable or safely within limits.
- Amber: warning thresholds approaching.
- Red: breached thresholds or stale/invalid state.
- Blue/neutral: informational state.

### 2. Show data freshness and connection risk

Add visible indicators for:

- Last payload time.
- Seconds since last payload.
- Websocket connected/disconnected state.
- Payload parse failures.
- Number of reconnect attempts in the current session.

A trading dashboard should never allow the user to confuse a stale screen with a live system.

### 3. Improve destructive command safety

The current command buttons are too easy to click accidentally. Add:

- Confirmation checkboxes or typed confirmations for `Emergency Halt`, `Resume Trading`, and `Liquidate All`.
- A command preview showing the exact JSON payload before sending.
- A timestamped command audit panel showing last command, send result, and websocket error if any.
- Disabled states while disconnected or while a command is pending.

### 4. Clarify financial terminology

Rename or add help text for terms that may be ambiguous:

- `Stuck Capital Value` → `Capital in Open Lots`.
- `Today's Profit (Total)` → `Total P&L` unless the backend guarantees that the value resets daily.
- `Capital Velocity Index` → `Closed Cycles per Open Lot` or move it to a strategy diagnostics section.
- `Total Completed Orders` currently combines buys and sells; label it `Estimated Orders` unless the backend supplies exact order counts.

### 5. Make the ledger decision-oriented

Add columns that help the user prioritize risk:

- Unrealized P&L per lot.
- Unrealized P&L percentage per lot.
- Distance to target as both dollars and percent.
- Age of lot.
- Notional exposure per lot.
- Sort lots by worst unrealized P&L or oldest age by default.

Most of these can be computed locally from existing `DashboardLot` fields if lot timestamps and shares are always present.

### 6. Add chart overlays for risk zones

Enhance the chart with:

- Current price horizontal line.
- Average open-lot cost line.
- Break-even line for all open inventory.
- Shaded drawdown bands from the highest observed price or highest observed equity.
- Toggle controls for lot labels to reduce visual clutter with many lots.

### 7. Add empty, loading, and degraded states

Improve user guidance for:

- No backend connection.
- Connected but no ticks received yet.
- Market closed or no recent ticks.
- Payload schema mismatch.
- Unsupported bridge version.

Each state should explain what is happening and the likely next action.

### 8. Add user-configurable display preferences

Add sidebar controls for:

- Chart interval: 1 minute, 5 minutes, 15 minutes.
- History window: 1 hour, 1 trading day, full session.
- Currency formatting precision.
- Whether to show all lots or only highest-risk lots.

## Risk-assessment updates that need `volatility-bridge`

The following fields should be added to the shared `volatility-bridge` models. These details are written as implementation-ready requirements for an AI agent working in that repository.

### Bridge update 1: Add account and portfolio risk snapshot

**Goal:** Allow the UI to show exposure, buying power, equity, and drawdown without reconstructing account state from lots.

**Implementation instructions for `volatility-bridge`:**

1. Create a Pydantic model named `DashboardRiskSnapshot` in the canonical models module that currently defines `DashboardStatePayload`.
2. Add the following fields:
   - `account_equity: float`
   - `cash_available: float`
   - `buying_power: float`
   - `gross_exposure: float`
   - `net_exposure: float`
   - `open_lot_notional: float`
   - `realized_profit: float`
   - `unrealized_profit: float`
   - `total_profit: float`
   - `day_profit: float | None = None`
   - `max_drawdown: float | None = None`
   - `max_drawdown_pct: float | None = None`
   - `exposure_pct_of_equity: float | None = None`
3. Add `risk_snapshot: DashboardRiskSnapshot | None = None` to `DashboardStatePayload`.
4. Keep existing top-level fields such as `realized_profit` for backward compatibility during migration.
5. Export `DashboardRiskSnapshot` from the package public API if the project has an `__init__.py` export list.
6. Add unit tests that validate serialization and deserialization of a payload with and without `risk_snapshot`.

### Bridge update 2: Add configured risk limits and breach status

**Goal:** Let the UI show when strategy exposure is close to or beyond configured limits.

**Implementation instructions for `volatility-bridge`:**

1. Create a Pydantic model named `DashboardRiskLimitStatus`.
2. Add the following fields:
   - `max_open_lots: int | None = None`
   - `max_capital_deployed: float | None = None`
   - `max_daily_loss: float | None = None`
   - `max_drawdown_pct: float | None = None`
   - `halt_on_breach: bool = True`
   - `breached_limits: list[str] = []`
   - `warning_limits: list[str] = []`
   - `risk_state: Literal["normal", "warning", "breached", "halted"] = "normal"`
3. Add `risk_limits: DashboardRiskLimitStatus | None = None` to `DashboardStatePayload`.
4. Use `Field(default_factory=list)` for list defaults to avoid shared mutable defaults.
5. Add tests for normal, warning, breached, and halted payload examples.

### Bridge update 3: Add market/session state

**Goal:** Help the UI distinguish real connection problems from normal quiet periods, market close, or illiquid conditions.

**Implementation instructions for `volatility-bridge`:**

1. Create a Pydantic model named `DashboardMarketState`.
2. Add the following fields:
   - `symbol: str`
   - `market_session: Literal["pre_market", "regular", "after_hours", "closed", "unknown"] = "unknown"`
   - `last_tick_timestamp: datetime | None = None`
   - `last_trade_price: float | None = None`
   - `last_quote_bid: float | None = None`
   - `last_quote_ask: float | None = None`
   - `spread: float | None = None`
   - `spread_pct: float | None = None`
   - `is_data_stale: bool = False`
   - `data_staleness_seconds: float | None = None`
3. Add `market_state: DashboardMarketState | None = None` to `DashboardStatePayload`.
4. Ensure datetime serialization is ISO-8601 compatible with Pydantic v2 JSON serialization.
5. Add payload fixture tests covering open market, closed market, and stale data.

### Bridge update 4: Expand lot-level risk fields

**Goal:** Let the UI rank open lots by risk and display per-lot exposure without duplicating backend portfolio logic.

**Implementation instructions for `volatility-bridge`:**

1. Extend `DashboardLot` with optional fields so existing producers remain compatible:
   - `notional_value: float | None = None`
   - `unrealized_profit: float | None = None`
   - `unrealized_profit_pct: float | None = None`
   - `age_seconds: float | None = None`
   - `max_adverse_excursion: float | None = None`
   - `max_favorable_excursion: float | None = None`
   - `risk_tags: list[str] = []`
2. Use `Field(default_factory=list)` for `risk_tags`.
3. Add model tests proving older payloads without these fields still parse.
4. Add examples showing a high-risk lot tagged with values such as `oldest_lot`, `largest_loss`, or `near_stop`.

### Bridge update 5: Add command acknowledgement messages

**Goal:** Let the UI confirm that a backend command was accepted, rejected, or failed instead of only showing that a websocket send occurred.

**Implementation instructions for `volatility-bridge`:**

1. Create a model named `UICommandAcknowledgement`.
2. Add fields:
   - `command_id: str`
   - `command: str`
   - `status: Literal["accepted", "rejected", "failed"]`
   - `message: str | None = None`
   - `timestamp: datetime`
3. Add optional `command_id: str | None = None` to every UI command model so the UI can correlate a command with an acknowledgement.
4. Document whether acknowledgements are sent on the existing state websocket or a separate command-response channel.
5. Add tests for command JSON round trips and acknowledgement parsing.

### Bridge update 6: Add bridge contract versioning

**Goal:** Prevent silent UI failures when the backend and UI are using incompatible model versions.

**Implementation instructions for `volatility-bridge`:**

1. Add `bridge_schema_version: str` to `DashboardStatePayload`, defaulting to the current package version when possible.
2. Add `producer_name: str | None = None` and `producer_version: str | None = None`.
3. Document semantic version compatibility rules.
4. Add a changelog entry whenever `DashboardStatePayload`, `DashboardLot`, or UI command models change.
5. Add tests showing the default schema version appears in serialized payloads.

## Suggested implementation roadmap

1. **Quick UI wins:** Rename ambiguous labels, add help text, show connection freshness, and add safer command confirmations.
2. **Risk ledger:** Add locally computed per-lot P&L, percent distance, age, and sorting.
3. **Bridge risk snapshot:** Implement `DashboardRiskSnapshot` and update the UI to prefer backend-provided risk values.
4. **Risk limits and market state:** Add visual warning banners and degraded-state handling.
5. **Command acknowledgements and versioning:** Add auditable command handling and compatibility checks.

## Acceptance criteria

- A user can identify current exposure, current P&L, and risk-limit status within five seconds of opening the dashboard.
- The UI clearly distinguishes live, stale, disconnected, and market-closed states.
- Destructive controls require deliberate confirmation and show backend acknowledgement.
- Open lots are sorted and annotated by risk impact, not merely listed.
- The UI continues to parse older bridge payloads while adopting new optional bridge fields.
