from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json


def create_news_analyst(llm, toolkit):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        if toolkit.config["online_tools"]:
            tools = [
                toolkit.get_global_news_openai,
                toolkit.get_google_news,
                toolkit.get_finnhub_news,
                toolkit.get_reddit_news,
            ]
        else:
            tools = [
                toolkit.get_finnhub_news,
                toolkit.get_reddit_news,
                toolkit.get_google_news,
            ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Look at news from EODHD, and finnhub to be comprehensive. Do not simply state the trends are mixed, provide detailed and finegrained analysis and insights that may help traders make decisions."
            + """ Make sure to append a Makrdown table at the end of the report to organize key points in the report, organized and easy to read.\n\nCRITICAL EXECUTION RULES: (1) If this round has not yet fetched data, call at least one tool to gather information. If the context already contains recent tool outputs, do NOT call tools again—produce the final report directly. (2) Never ask the user for additional input. (3) When calling tools, use the provided context values: ticker={ticker} and curr_date={current_date}. (4) If any data is missing, proceed with reasonable assumptions and produce a self-contained report for the last 7 days.\n\nVERIFICATION REQUIREMENTS: For every key claim in your report, fetch corroborating data from at least two distinct sources (e.g., Google News plus Finnhub/Reddit). Explicitly cite the sources and the covered date range. If a claim cannot be determined, clearly mark it as 'Undetermined' and continue gathering data."""
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    " For your reference, the current date is {current_date}. We are looking at the company {ticker}."
                    " If this round has not yet fetched data, begin by invoking at least one of the tools to retrieve inputs. If the context already contains recent tool outputs, do NOT call tools again—proceed to write the report. When providing tool arguments, pass ticker={ticker} and curr_date={current_date}."
                    " Do not ask the user any questions; proceed autonomously and output a complete report.",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(ticker=ticker)

        # Avoid rebinding tools if recent tool outputs exist to prevent loops
        def _is_tool_message(msg) -> bool:
            try:
                from langchain_core.messages import ToolMessage  # type: ignore
                return isinstance(msg, ToolMessage)
            except Exception:
                return type(msg).__name__ == "ToolMessage"

        recent_msgs = state["messages"] if isinstance(state.get("messages"), list) else []
        recent_tool_present = any(_is_tool_message(m) for m in recent_msgs[-4:])

        chain = prompt | (llm if recent_tool_present else llm.bind_tools(tools))
        # Guard against context overflow by budgeting messages
        try:
            model_name = toolkit.config.get("quick_think_llm")
            max_msgs = int(toolkit.config.get("max_conv_messages", 50) or 50)
            budgeted_messages = toolkit.budget_messages(
                state["messages"],
                model_name,
                reply_tokens_budget=1024,
                safety_margin=0.9,
                max_messages=max_msgs,
            )
        except Exception:
            budgeted_messages = state["messages"]

        result = chain.invoke(budgeted_messages)

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
