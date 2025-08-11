from tradingagents.utils.result_saver import save_analysis_results


def main() -> None:
    config = {"results_dir": "./results"}
    ticker = "DEMO"
    date_str = "2099-01-01"
    final_state = {
        "market_report": "Market summary...",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_plan": "",
        "trader_investment_plan": "",
        "final_trade_decision": "HOLD",
    }
    decision = "HOLD"
    saved = save_analysis_results(config, ticker, date_str, final_state, decision, ui_lang="zh")
    print("Saved to:", saved["base_dir"])
    print("- final_state:", saved["final_state"])
    print("- final_report:", saved["final_report"])
    print("- decision:", saved["decision"])


if __name__ == "__main__":
    main()


