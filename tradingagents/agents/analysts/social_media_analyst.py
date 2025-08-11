from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
import time
import json


def create_social_media_analyst(llm, toolkit):
    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        company_name = state["company_of_interest"]

        if toolkit.config["online_tools"]:
            tools = [
                toolkit.get_stock_news_openai,
                toolkit.get_reddit_stock_info,
            ]
        else:
            tools = [
                toolkit.get_reddit_stock_info,
            ]

        system_message = (
            "You are a social media and company specific news researcher/analyst tasked with analyzing social media posts, recent company news, and public sentiment for a specific company over the past week. You will be given a company's name your objective is to write a comprehensive long report detailing your analysis, insights, and implications for traders and investors on this company's current state after looking at social media and what people are saying about that company, analyzing sentiment data of what people feel each day about the company, and looking at recent company news. Try to look at all sources possible from social media to sentiment to news. Do not simply state the trends are mixed, provide detailed and finegrained analysis and insights that may help traders make decisions."
            + """ Make sure to append a Makrdown table at the end of the report to organize key points in the report, organized and easy to read.\n\nCRITICAL EXECUTION RULES: (1) If this round has not yet fetched data, call at least one tool to gather information. If the context already contains recent tool outputs, do NOT call tools again—produce the final report directly. (2) Never ask the user for additional input. (3) When calling tools, use the provided context values: ticker={ticker} and curr_date={current_date}. (4) If any data is missing, proceed with reasonable assumptions and produce a self-contained report for the last 7 days.\n\nVERIFICATION REQUIREMENTS: For every key claim, cross-verify with fetched posts and sentiment metrics. Call at least two distinct sources (e.g., OpenAI stock news and Reddit company info). Cite sources and date windows explicitly; if uncertain, mark as 'Undetermined' and fetch more data.""",
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
                    " For your reference, the current date is {current_date}. The current company we want to analyze is {ticker}."
                    " Always begin by invoking at least one of the tools to retrieve inputs. When providing tool arguments, pass ticker={ticker} and curr_date={current_date}."
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

        # Budget conversation history to prevent 8192 errors
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
            "sentiment_report": report,
        }

    return social_media_analyst_node
