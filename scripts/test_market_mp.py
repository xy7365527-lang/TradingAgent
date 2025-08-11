import os
import datetime

from tradingagents.agents.utils.agent_utils import Toolkit
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.default_config import DEFAULT_CONFIG
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage


def main() -> None:
    today = datetime.date.today().strftime("%Y-%m-%d")
    cfg = DEFAULT_CONFIG.copy()

    # Ensure online tools to mimic GUI/CLI default
    cfg["online_tools"] = True
    # Keep force_online behavior from config

    # Init LLM (fallback to a lightweight model name if needed)
    try:
        llm = ChatOpenAI(model=cfg["quick_think_llm"], base_url=cfg["backend_url"])
    except Exception:
        llm = ChatOpenAI(model="gpt-4o-mini", base_url=cfg["backend_url"])  # fallback

    tk = Toolkit(config=cfg)
    node = create_market_analyst(llm, tk)

    state = {
        "messages": [HumanMessage(content="MP")],
        "trade_date": today,
        "company_of_interest": "MP",
    }

    for step in range(8):
        out = node(state)
        msgs = out.get("messages", [])
        if msgs:
            state["messages"].extend(msgs)
            res = msgs[-1]
            # Tool call loop
            tool_calls = getattr(res, "tool_calls", None) or []
            if tool_calls:
                for tc in tool_calls:
                    try:
                        name = tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
                        args = tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})
                        fn = getattr(tk, name)
                        try:
                            out_text = fn.invoke(args)  # LangChain tool style
                        except Exception:
                            out_text = fn(**args)  # Direct call fallback
                        tc_id = tc["id"] if isinstance(tc, dict) else str(getattr(tc, "id", ""))
                        state["messages"].append(ToolMessage(content=str(out_text), tool_call_id=str(tc_id)))
                    except Exception as e:
                        tc_id = tc["id"] if isinstance(tc, dict) else str(getattr(tc, "id", ""))
                        state["messages"].append(ToolMessage(content="ERROR:" + str(e), tool_call_id=str(tc_id)))
                continue

        # No tool_calls → final content present in market_report
        report = out.get("market_report") or ""
        print("Market report length:", len(report))
        print("Preview:")
        print((report or "").split("\n", 10)[0:10])
        break


if __name__ == "__main__":
    main()


