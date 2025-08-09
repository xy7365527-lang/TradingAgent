from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def main() -> None:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "openai"
    config["backend_url"] = "https://api.openai.com/v1"
    # 强制在线数据端口 + 启用 OpenAI Responses 兜底（用于 Reddit 在线搜索等）
    config["online_tools"] = True
    config["use_openai_responses"] = True
    config["oai_responses_streaming"] = True
    # 使用稳定默认模型
    config["deep_think_llm"] = "gpt-4o"
    config["quick_think_llm"] = "gpt-4o-mini"
    config["max_debate_rounds"] = 1

    ta = TradingAgentsGraph(debug=True, config=config)
    _, decision = ta.propagate("NVDA", "2024-05-10")
    print(decision)


if __name__ == "__main__":
    main()


