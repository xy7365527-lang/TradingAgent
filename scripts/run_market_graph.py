import os
import datetime
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def main() -> None:
    today = datetime.date.today().strftime("%Y-%m-%d")
    cfg = DEFAULT_CONFIG.copy()
    # Single analyst for minimal reproduction
    analysts = ["market"]

    print("Provider:", cfg.get("llm_provider"), "Model:", cfg.get("quick_think_llm"))
    print("Date:", today, "Ticker:", "MP")

    graph = TradingAgentsGraph(analysts, config=cfg, debug=True)
    final_state, decision = graph.propagate("MP", today)
    print("Final decision:", decision)
    mr = final_state.get("market_report") or ""
    print("Market report len:", len(mr))
    if mr:
        print("Market report head:\n", "\n".join(mr.splitlines()[:20]))


if __name__ == "__main__":
    main()


