"""
Offline verification for loop-prevention changes.

This script simulates recent ToolMessage presence in state messages and checks
that analyst nodes do NOT re-bind tools in that case, returning a direct text
report instead of triggering another tool call cycle.

It does not call any external APIs.
"""

from langchain_core.messages import HumanMessage, ToolMessage

from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
from tradingagents.agents.analysts.social_media_analyst import (
    create_social_media_analyst,
)
from tradingagents.agents.analysts.fundamentals_analyst import (
    create_fundamentals_analyst,
)


class FakeLLM:
    def __init__(self):
        self.bind_called = False
        self.invocations = 0

    def bind_tools(self, tools):
        # Record that a bind attempt happened; return self to satisfy LCEL pipe
        self.bind_called = True
        return self

    def invoke(self, messages):
        # Return an object mimicking AIMessage with no tool calls
        self.invocations += 1

        class _Res:
            def __init__(self):
                self.content = "ok"
                self.tool_calls = []

        return _Res()

    # Make it acceptable to LCEL as a callable runnable
    def __call__(self, messages):
        return self.invoke(messages)


class FakeTool:
    def __init__(self, name: str):
        self.name = name


class FakeToolkit:
    def __init__(self, online_tools: bool = False):
        self.config = {
            "online_tools": online_tools,
            "quick_think_llm": "gpt-5-mini",
            "max_conv_messages": 50,
        }
        # Provide placeholder tool definitions for prompt formatting when needed
        self.get_YFin_data = FakeTool("get_YFin_data")
        self.get_stockstats_indicators_report = FakeTool(
            "get_stockstats_indicators_report"
        )
        self.get_stockstats_indicators_report_online = FakeTool(
            "get_stockstats_indicators_report_online"
        )
        self.get_YFin_data_online = FakeTool("get_YFin_data_online")
        self.get_global_news_openai = FakeTool("get_global_news_openai")
        self.get_google_news = FakeTool("get_google_news")
        self.get_finnhub_news = FakeTool("get_finnhub_news")
        self.get_reddit_news = FakeTool("get_reddit_news")
        self.get_stock_news_openai = FakeTool("get_stock_news_openai")
        self.get_reddit_stock_info = FakeTool("get_reddit_stock_info")
        self.get_fundamentals_openai = FakeTool("get_fundamentals_openai")
        self.get_finnhub_company_insider_sentiment = FakeTool(
            "get_finnhub_company_insider_sentiment"
        )
        self.get_finnhub_company_insider_transactions = FakeTool(
            "get_finnhub_company_insider_transactions"
        )
        self.get_simfin_balance_sheet = FakeTool("get_simfin_balance_sheet")
        self.get_simfin_cashflow = FakeTool("get_simfin_cashflow")
        self.get_simfin_income_stmt = FakeTool("get_simfin_income_stmt")

    def budget_messages(
        self, messages, model_name, reply_tokens_budget=1024, safety_margin=0.9, max_messages=50
    ):
        # Pass-through for offline test
        return messages


def _make_state_with_tool_msg():
    return {
        "messages": [
            HumanMessage(content="hi"),
            ToolMessage(content="tool-out", tool_call_id="t1"),
        ],
        "trade_date": "2024-05-10",
        "company_of_interest": "NVDA",
    }


def verify_market():
    llm = FakeLLM()
    tk = FakeToolkit(online_tools=False)
    node = create_market_analyst(llm, tk)
    out = node(_make_state_with_tool_msg())
    assert isinstance(out, dict)
    assert "messages" in out
    assert "market_report" in out and str(out["market_report"]).strip() != ""
    # With recent tool message, we should NOT re-bind tools
    assert llm.bind_called is False
    print("[OK] market_analyst: no re-bind when recent ToolMessage present")


def verify_news():
    llm = FakeLLM()
    tk = FakeToolkit(online_tools=True)
    node = create_news_analyst(llm, tk)
    out = node(_make_state_with_tool_msg())
    assert isinstance(out, dict)
    assert "messages" in out
    assert "news_report" in out and str(out["news_report"]).strip() != ""
    assert llm.bind_called is False
    print("[OK] news_analyst: no re-bind when recent ToolMessage present")


def verify_social():
    llm = FakeLLM()
    tk = FakeToolkit(online_tools=True)
    node = create_social_media_analyst(llm, tk)
    out = node(_make_state_with_tool_msg())
    assert isinstance(out, dict)
    assert "messages" in out
    assert "sentiment_report" in out and str(out["sentiment_report"]).strip() != ""
    assert llm.bind_called is False
    print("[OK] social_analyst: no re-bind when recent ToolMessage present")


def verify_fundamentals():
    llm = FakeLLM()
    tk = FakeToolkit(online_tools=True)
    node = create_fundamentals_analyst(llm, tk)
    out = node(_make_state_with_tool_msg())
    assert isinstance(out, dict)
    assert "messages" in out
    assert "fundamentals_report" in out and str(out["fundamentals_report"]).strip() != ""
    assert llm.bind_called is False
    print("[OK] fundamentals_analyst: no re-bind when recent ToolMessage present")


if __name__ == "__main__":
    verify_market()
    verify_news()
    verify_social()
    verify_fundamentals()
    print("All offline loop-prevention checks passed.")


