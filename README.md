# Volatility UI

Streamlit dashboard for the Volatility Harvester backend.

## Shared model dependency

This project uses the [`volatility-bridge`](https://github.com/AshitakaLax/volatility-bridge) package as the canonical source for the Pydantic models exchanged between the backend trading loop and this UI. The dependency is declared in `pyproject.toml` and imported from `volatility_bridge.volatile_models`.

`src.models` intentionally re-exports those shared bridge models for compatibility with any code that still imports from this repository directly.

## Running the dashboard

Install the project dependencies, then run the Streamlit UI:

```bash
streamlit run src/dashboard_ui.py
```

By default, the dashboard connects to the backend websocket broadcaster at `ws://127.0.0.1:8765`. To override the port, create a `config.yaml` file with:

```yaml
system:
  websocket_port: 8765
```
