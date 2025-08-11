from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from typing import List
from typing import Annotated
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import RemoveMessage
from langchain_core.tools import tool
from datetime import date, timedelta, datetime
import functools
import pandas as pd
import os
from dateutil.relativedelta import relativedelta
from langchain_openai import ChatOpenAI
import tradingagents.dataflows.interface as interface
from tradingagents.default_config import DEFAULT_CONFIG
from langchain_core.messages import HumanMessage
from tradingagents.utils.token_utils import (
    estimate_tokens_for_text,
    estimate_tokens_for_messages,
    context_limit_for_model,
)


def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]
        
        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]
        
        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")
        
        return {"messages": removal_operations + [placeholder]}
    
    return delete_messages


class Toolkit:
    _config = DEFAULT_CONFIG.copy()

    @staticmethod
    def _trim_text(text: object, limit: int) -> str:
        """Trim text to a safe character length, preserving head and tail.

        - Converts any input to string
        - Keeps head ~65%, tail ~35% when truncating
        - Inserts a marker in the middle to indicate truncation
        """
        try:
            s = str(text)
        except Exception:
            s = ""
        if limit <= 0 or len(s) <= limit:
            return s
        # Reserve space for the marker
        marker = f"\n\n... [TRIMMED to {limit} chars; original {len(s)}] ...\n\n"
        reserve = len(marker)
        if limit <= reserve + 10:
            return s[:limit]
        head = int((limit - reserve) * 0.65)
        tail = (limit - reserve) - head
        return s[:head] + marker + s[-tail:]

    @classmethod
    def _trim_tool_output(cls, text: object) -> str:
        limit = int(cls._config.get("max_tool_output_chars", 6000) or 6000)
        return cls._trim_text(text, limit)

    @classmethod
    def trim_section(cls, text: object) -> str:
        """Trim long sections (reports/history) before embedding into prompts."""
        limit = int(cls._config.get("max_section_chars", 6000) or 6000)
        return cls._trim_text(text, limit)

    @classmethod
    def dynamic_budget_text(
        cls,
        text: object,
        model_name: str | None,
        reply_tokens_budget: int = 1024,
        safety_margin: float = 0.9,
        hard_cap_chars: int | None = None,
    ) -> str:
        """Ensure text fits into token budget for the given model.

        - model_name: used to infer context window
        - reply_tokens_budget: reserve tokens for model's output
        - safety_margin: multiply available tokens for safety
        - hard_cap_chars: optional additional char-based cap
        """
        s = str(text) if text is not None else ""
        if not s:
            return s
        try:
            ctx = context_limit_for_model(model_name)
        except Exception:
            ctx = 8192
        avail = max(512, int((ctx - max(256, reply_tokens_budget)) * float(safety_margin)))
        # Estimate token usage and shrink by chars if needed
        used = estimate_tokens_for_text(s, model_name)
        if used <= avail:
            if hard_cap_chars and len(s) > hard_cap_chars:
                return cls._trim_text(s, hard_cap_chars)
            return s
        # Convert token excess to char target via ratio
        # Use conservative 2.5 chars per token mapping
        target_chars = int(avail * 2.5)
        if hard_cap_chars:
            target_chars = min(target_chars, hard_cap_chars)
        return cls._trim_text(s, max(1000, target_chars))

    # -------- LLM-based compression (map-reduce) --------
    @classmethod
    def _split_into_chunks(cls, text: str, chunk_chars: int) -> list[str]:
        if chunk_chars <= 0 or len(text) <= chunk_chars:
            return [text]
        chunks: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            end = min(n, i + chunk_chars)
            chunks.append(text[i:end])
            i = end
        return chunks

    @classmethod
    def compress_section(
        cls,
        llm,
        text: object,
        model_name: str | None,
        target_tokens: int | None = None,
    ) -> str:
        """Map-reduce summarization preserving facts; falls back to trim.

        - Uses provided llm (same provider) for small summaries
        - When compression disabled or errors occur, falls back to trim_section
        - target_tokens controls the merged summary size
        """
        cfg = cls._config
        if not bool(cfg.get("enable_llm_compression", True)):
            return cls.trim_section(text)
        s = str(text or "")
        if not s:
            return s
        try:
            # If already small enough, return as-is
            tok = estimate_tokens_for_text(s, model_name)
            tgt = int(target_tokens or int(cfg.get("compression_target_tokens", 1200)))
            if tok <= int(tgt * 1.2):
                return s

            chunk_chars = int(cfg.get("compression_chunk_chars", 6000))
            chunks = cls._split_into_chunks(s, chunk_chars)
            map_summaries: list[str] = []
            # Map step: summarize each chunk with a fact-preserving instruction
            for idx, chunk in enumerate(chunks, 1):
                prompt = [
                    {
                        "role": "system",
                        "content": (
                            "You are a precise summarizer. Produce a faithful, lossless summary focusing on: "
                            "numbers, dates, entities, indicators, and explicit conclusions. Keep neutral tone."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Chunk {idx}/{len(chunks)}: Please summarize faithfully in bullet points (<= 120 lines).\n\n{chunk}",
                    },
                ]
                try:
                    resp = llm.invoke(prompt)
                    map_summaries.append(str(getattr(resp, "content", resp)))
                except Exception:
                    map_summaries.append(cls._trim_text(chunk, 1500))

            # Reduce step: merge the bullet summaries into a compact brief
            merged = "\n\n".join(map_summaries)
            reduce_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You merge bullet summaries into a compact, fact-preserving brief. "
                        "Keep dates/numbers/tickers verbatim; group by theme; avoid redundancy."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Merge the following bullet summaries into a concise brief (<= target tokens).\n\n"
                        f"Target tokens: {int(target_tokens or int(cfg.get('compression_target_tokens', 1200)))}\n\n{merged}"
                    ),
                },
            ]
            try:
                final = llm.invoke(reduce_prompt)
                summary = str(getattr(final, "content", final))
            except Exception:
                summary = cls._trim_text(merged, 4000)

            # Final token budget guard
            return cls.dynamic_budget_text(summary, model_name, reply_tokens_budget=1024, safety_margin=0.9)
        except Exception:
            return cls.trim_section(text)

    @classmethod
    def budget_messages(
        cls,
        messages: list,
        model_name: str | None,
        reply_tokens_budget: int = 1024,
        safety_margin: float = 0.9,
        max_messages: int | None = None,
    ) -> list:
        """Keep the most recent messages within token budget.

        - Drops from the head (oldest first) until within budget
        - Adds a small overhead per message to account for roles and formatting
        - Optionally enforce an upper bound on number of messages
        """
        if not isinstance(messages, list) or not messages:
            return messages

        # 1) Drop trailing assistant with pending tool_calls (incomplete tool block)
        #    防止尾部存在未执行工具调用的 Assistant 消息导致后续拼接异常
        try:
            if messages and isinstance(messages[-1], AIMessage):
                last_ai = messages[-1]
                if bool(getattr(last_ai, "tool_calls", None)):
                    messages = messages[:-1]
        except Exception:
            pass
        try:
            ctx = context_limit_for_model(model_name)
        except Exception:
            ctx = 8192
        allowed = max(512, int((ctx - max(256, reply_tokens_budget)) * float(safety_margin)))
        # quick exit: cap by count first
        items = messages[-max_messages:] if (isinstance(max_messages, int) and max_messages > 0 and len(messages) > max_messages) else list(messages)

        # Walk from tail to head and accumulate tokens
        kept_reversed: list = []
        total_tokens = 0
        per_msg_overhead = 8
        for m in reversed(items):
            try:
                t = estimate_tokens_for_messages([m], model_name) + per_msg_overhead
            except Exception:
                t = per_msg_overhead + 16
            if total_tokens + t > allowed and len(kept_reversed) > 0:
                break
            kept_reversed.append(m)
            total_tokens += t
        kept = list(reversed(kept_reversed))

        # 3) 结构修复：确保所有保留下来的 AIMessage.tool_calls 都有对应的后续 ToolMessage；
        #    并移除任何缺乏响应的 AIMessage 以及与之相关的 ToolMessage，避免 OpenAI 400 错误。
        try:
            from langchain_core.messages import ToolMessage  # 局部导入，避免环境差异
        except Exception:
            ToolMessage = None  # 类型不可用时，仍可通过名称匹配

        def _is_tool_message(msg) -> bool:
            if ToolMessage is not None and isinstance(msg, ToolMessage):
                return True
            return type(msg).__name__ == "ToolMessage"

        def _extract_ai_tool_call_ids(ai_msg) -> set[str]:
            ids: set[str] = set()
            try:
                tool_calls = getattr(ai_msg, "tool_calls", None) or []
            except Exception:
                tool_calls = []
            for tc in tool_calls:
                tc_id = None
                try:
                    if isinstance(tc, dict):
                        tc_id = tc.get("id") or tc.get("tool_call_id")
                    else:
                        tc_id = getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)
                except Exception:
                    tc_id = None
                if tc_id:
                    try:
                        ids.add(str(tc_id))
                    except Exception:
                        pass
            return ids

        # 索引扫描：记录 kept 中各类消息的位置
        ai_index_to_ids: dict[int, set[str]] = {}
        tool_id_to_indices: dict[str, list[int]] = {}
        ai_indices_in_kept: list[int] = []
        for idx, msg in enumerate(kept):
            try:
                if isinstance(msg, AIMessage):
                    ai_indices_in_kept.append(idx)
                    ids = _extract_ai_tool_call_ids(msg)
                    if ids:
                        ai_index_to_ids[idx] = ids
                elif _is_tool_message(msg):
                    tool_call_id = None
                    try:
                        tool_call_id = getattr(msg, "tool_call_id", None)
                    except Exception:
                        tool_call_id = None
                    if tool_call_id:
                        key = str(tool_call_id)
                        tool_id_to_indices.setdefault(key, []).append(idx)
            except Exception:
                continue

        # 计算每个 AI 的“已响应” tool_call_id：要求存在于其后的 ToolMessage，且出现在下一条“非工具消息(AI/Human)”之前
        ai_index_to_responded_ids: dict[int, set[str]] = {}
        allowed_tool_call_ids: set[str] = set()
        # 计算每个 AI 的“下一条非工具消息(AI/Human)”的索引（若不存在则视为 len(kept)）
        def _next_non_tool_index(curr_idx: int) -> int:
            for j in range(curr_idx + 1, len(kept)):
                try:
                    if not _is_tool_message(kept[j]):
                        return j
                except Exception:
                    return j
            return len(kept)

        for ai_idx, ids in ai_index_to_ids.items():
            responded: set[str] = set()
            next_cut = _next_non_tool_index(ai_idx)
            for tc_id in ids:
                indices = tool_id_to_indices.get(tc_id) or []
                # 必须在 (ai_idx, next_cut) 区间内存在匹配的 ToolMessage，才算“已响应”
                in_window = any(ai_idx < k < next_cut for k in indices)
                if in_window:
                    responded.add(tc_id)
            if responded:
                ai_index_to_responded_ids[ai_idx] = responded
                allowed_tool_call_ids.update(responded)

        # 依据判定结果重建消息序列：
        # - AIMessage：若含 tool_calls，则裁剪为仅保留“已响应”的 id；若一个也没有，则清空其 tool_calls
        # - ToolMessage：仅在其 tool_call_id 属于 allowed_tool_call_ids 时保留
        repaired: list = []
        for idx, msg in enumerate(kept):
            try:
                if isinstance(msg, AIMessage):
                    # 提取当前 AI 的工具调用
                    original_ids = _extract_ai_tool_call_ids(msg)
                    if not original_ids:
                        repaired.append(msg)
                        continue
                    responded_ids = ai_index_to_responded_ids.get(idx, set())
                    # 若部分响应，则裁剪 tool_calls 阵列
                    try:
                        original_tool_calls = getattr(msg, "tool_calls", None) or []
                    except Exception:
                        original_tool_calls = []

                    trimmed_tool_calls = []
                    if responded_ids:
                        for tc in original_tool_calls:
                            try:
                                if isinstance(tc, dict):
                                    tc_id = tc.get("id") or tc.get("tool_call_id")
                                else:
                                    tc_id = getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)
                            except Exception:
                                tc_id = None
                            if tc_id and str(tc_id) in responded_ids:
                                trimmed_tool_calls.append(tc)
                    # 生成新的 AIMessage，确保 tool_calls 与后续 ToolMessage 一致
                    try:
                        if trimmed_tool_calls:
                            repaired.append(AIMessage(content=getattr(msg, "content", ""), tool_calls=trimmed_tool_calls))
                        else:
                            # 无已响应 id，则保留纯文本回答，清空 tool_calls
                            repaired.append(AIMessage(content=getattr(msg, "content", "")))
                    except Exception:
                        # 回退：保留原消息，避免丢失上下文
                        repaired.append(msg)
                elif _is_tool_message(msg):
                    tool_call_id = None
                    try:
                        tool_call_id = getattr(msg, "tool_call_id", None)
                    except Exception:
                        tool_call_id = None
                    if tool_call_id and str(tool_call_id) in allowed_tool_call_ids:
                        repaired.append(msg)
                    else:
                        # 丢弃无匹配或被裁剪掉的 ToolMessage
                        continue
                else:
                    repaired.append(msg)
            except Exception:
                # 任何异常情况下，保守地保留消息，避免过度删除
                repaired.append(msg)

        # 若修复后非空则返回
        if repaired:
            return repaired

        # 回退：选择 items 中从尾到头的第一个非工具消息
        fallback = None
        for msg in reversed(items):
            try:
                if not _is_tool_message(msg):
                    fallback = msg
                    break
            except Exception:
                fallback = msg
                break
        if fallback is not None:
            return [fallback]

        # 最后兜底：占位消息，避免空列表或非法开头
        try:
            return [HumanMessage(content="Continue")]
        except Exception:
            return items[-1:]

    @classmethod
    def update_config(cls, config):
        """Update the class-level configuration."""
        cls._config.update(config)

    @property
    def config(self):
        """Access the configuration."""
        return self._config

    def __init__(self, config=None):
        if config:
            self.update_config(config)

    @staticmethod
    @tool
    def get_reddit_news(
        curr_date: Annotated[str, "Date you want to get news for in yyyy-mm-dd format"],
    ) -> str:
        """
        Retrieve global news from Reddit within a specified time frame.
        Args:
            curr_date (str): Date you want to get news for in yyyy-mm-dd format
        Returns:
            str: A formatted dataframe containing the latest global news from Reddit in the specified time frame.
        """
        
        cfg = Toolkit._config
        force_online = bool(cfg.get("force_online", False))
        # 优先在线 Reddit（通过 OpenAI web_search 或 Pushshift 的在线方案，已在 interface 中实现 online 版本）
        if bool(cfg.get("online_tools", True)) or force_online:
            try:
                # 在线全局 Reddit 汇总
                from tradingagents.dataflows.interface import get_reddit_global_news_online
                online = get_reddit_global_news_online(curr_date)
                if isinstance(online, str) and online.strip():
                    return Toolkit._trim_tool_output(online)
            except Exception:
                pass
        if force_online:
            return Toolkit._trim_tool_output("")  # 严格在线模式下不回退
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive" and bool(cfg.get("use_multi_layer_window", False)):
            return Toolkit._trim_tool_output(interface.get_reddit_global_news_multi(curr_date))
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            return Toolkit._trim_tool_output(interface.get_reddit_global_news_auto(curr_date))
        global_news_result = interface.get_reddit_global_news(curr_date, 7, int(cfg.get("news_max_per_day", 5)))

        return Toolkit._trim_tool_output(global_news_result)

    @staticmethod
    @tool
    def get_finnhub_news(
        ticker: Annotated[
            str,
            "Search query of a company, e.g. 'AAPL, TSM, etc.",
        ],
        start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
        end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    ):
        """
        Retrieve the latest news about a given stock from Finnhub within a date range
        Args:
            ticker (str): Ticker of a company. e.g. AAPL, TSM
            start_date (str): Start date in yyyy-mm-dd format
            end_date (str): End date in yyyy-mm-dd format
        Returns:
            str: A formatted dataframe containing news about the company within the date range from start_date to end_date
        """

        end_date_str = end_date

        end_date = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = datetime.strptime(start_date, "%Y-%m-%d")
        look_back_days = (end_date - start_date).days

        cfg = Toolkit._config
        force_online = bool(cfg.get("force_online", False))
        if bool(cfg.get("online_tools", True)) or force_online:
            finnhub_news_result = interface.get_finnhub_news_online(
                ticker, end_date_str, look_back_days
            )
        else:
            finnhub_news_result = interface.get_finnhub_news(
                ticker, end_date_str, look_back_days
            )

        return finnhub_news_result

    @staticmethod
    @tool
    def get_reddit_stock_info(
        ticker: Annotated[
            str,
            "Ticker of a company. e.g. AAPL, TSM",
        ],
        curr_date: Annotated[str, "Current date you want to get news for"],
    ) -> str:
        """
        Retrieve the latest news about a given stock from Reddit, given the current date.
        Args:
            ticker (str): Ticker of a company. e.g. AAPL, TSM
            curr_date (str): current date in yyyy-mm-dd format to get news for
        Returns:
            str: A formatted dataframe containing the latest news about the company on the given date
        """

        cfg = Toolkit._config
        force_online = bool(cfg.get("force_online", False))
        if bool(cfg.get("online_tools", True)) or force_online:
            try:
                from tradingagents.dataflows.interface import get_reddit_company_news_online
                online = get_reddit_company_news_online(ticker, curr_date)
                if isinstance(online, str) and online.strip():
                    return Toolkit._trim_tool_output(online)
            except Exception:
                pass
        if force_online:
            return Toolkit._trim_tool_output("")
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive" and bool(cfg.get("use_multi_layer_window", False)):
            return Toolkit._trim_tool_output(interface.get_reddit_company_news_multi(ticker, curr_date))
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            return Toolkit._trim_tool_output(interface.get_reddit_company_news_auto(ticker, curr_date))
        stock_news_results = interface.get_reddit_company_news(ticker, curr_date, 7, int(cfg.get("news_max_per_day", 5)))

        return Toolkit._trim_tool_output(stock_news_results)

    @staticmethod
    @tool
    def get_YFin_data(
        symbol: Annotated[str, "ticker symbol of the company"],
        start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
        end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    ) -> str:
        """
        Retrieve the stock price data for a given ticker symbol from Yahoo Finance.
        Args:
            symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
            start_date (str): Start date in yyyy-mm-dd format
            end_date (str): End date in yyyy-mm-dd format
        Returns:
            str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
        """

        # Respect adaptive windowing when caller passes curr_date in end_date slot
        cfg = Toolkit._config
        if bool(cfg.get("force_online", False)):
            # 强制在线：改道到在线接口
            try:
                if start_date == "auto" and str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
                    if bool(cfg.get("use_multi_layer_window", False)):
                        return Toolkit._trim_tool_output(interface.get_YFin_data_online_multi(symbol, end_date))
                    return Toolkit._trim_tool_output(interface.get_YFin_data_online_auto(symbol, end_date))
            except Exception:
                pass
            return Toolkit._trim_tool_output(interface.get_YFin_data_online(symbol, start_date, end_date))
        try:
            if start_date == "auto" and str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
                if bool(cfg.get("use_multi_layer_window", False)):
                    return Toolkit._trim_tool_output(interface.get_YFin_data_multi(symbol, end_date))
                return Toolkit._trim_tool_output(interface.get_YFin_data_auto(symbol, end_date))
        except Exception:
            pass
        result_data = interface.get_YFin_data(symbol, start_date, end_date)

        return Toolkit._trim_tool_output(result_data)

    @staticmethod
    @tool
    def get_YFin_data_online(
        symbol: Annotated[str, "ticker symbol of the company"],
        start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
        end_date: Annotated[str, "End date in yyyy-mm-dd format"],
    ) -> str:
        """
        Retrieve the stock price data for a given ticker symbol from Yahoo Finance.
        Args:
            symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
            start_date (str): Start date in yyyy-mm-dd format
            end_date (str): End date in yyyy-mm-dd format
        Returns:
            str: A formatted dataframe containing the stock price data for the specified ticker symbol in the specified date range.
        """

        cfg = Toolkit._config
        try:
            if start_date == "auto" and str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
                if bool(cfg.get("use_multi_layer_window", False)):
                    return Toolkit._trim_tool_output(interface.get_YFin_data_online_multi(symbol, end_date))
                return Toolkit._trim_tool_output(interface.get_YFin_data_online_auto(symbol, end_date))
        except Exception:
            pass
        result_data = interface.get_YFin_data_online(symbol, start_date, end_date)

        return Toolkit._trim_tool_output(result_data)

    @staticmethod
    @tool
    def get_stockstats_indicators_report(
        symbol: Annotated[str, "ticker symbol of the company"],
        indicator: Annotated[
            str, "technical indicator to get the analysis and report of"
        ],
        curr_date: Annotated[
            str, "The current trading date you are trading on, YYYY-mm-dd"
        ],
        look_back_days: Annotated[int, "how many days to look back"] = 30,
    ) -> str:
        """
        Retrieve stock stats indicators for a given ticker symbol and indicator.
        Args:
            symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
            indicator (str): Technical indicator to get the analysis and report of
            curr_date (str): The current trading date you are trading on, YYYY-mm-dd
            look_back_days (int): How many days to look back, default is 30
        Returns:
            str: A formatted dataframe containing the stock stats indicators for the specified ticker symbol and indicator.
        """

        cfg = Toolkit._config
        if bool(cfg.get("force_online", False)):
            # 强制在线：直接走在线版本
            if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
                if bool(cfg.get("use_multi_layer_window", False)):
                    return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, True))
                return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, True))
            return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, True))
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            if bool(cfg.get("use_multi_layer_window", False)):
                return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, False))
            return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, False))
        result_stockstats = interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, False)

        return Toolkit._trim_tool_output(result_stockstats)

    @staticmethod
    @tool
    def get_stockstats_indicators_report_online(
        symbol: Annotated[str, "ticker symbol of the company"],
        indicator: Annotated[
            str, "technical indicator to get the analysis and report of"
        ],
        curr_date: Annotated[
            str, "The current trading date you are trading on, YYYY-mm-dd"
        ],
        look_back_days: Annotated[int, "how many days to look back"] = 30,
    ) -> str:
        """
        Retrieve stock stats indicators for a given ticker symbol and indicator.
        Args:
            symbol (str): Ticker symbol of the company, e.g. AAPL, TSM
            indicator (str): Technical indicator to get the analysis and report of
            curr_date (str): The current trading date you are trading on, YYYY-mm-dd
            look_back_days (int): How many days to look back, default is 30
        Returns:
            str: A formatted dataframe containing the stock stats indicators for the specified ticker symbol and indicator.
        """

        cfg = Toolkit._config
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            if bool(cfg.get("use_multi_layer_window", False)):
                return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, True))
            return Toolkit._trim_tool_output(interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, True))
        result_stockstats = interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, True)

        return Toolkit._trim_tool_output(result_stockstats)

    @staticmethod
    @tool
    def get_finnhub_company_insider_sentiment(
        ticker: Annotated[str, "ticker symbol for the company"],
        curr_date: Annotated[
            str,
            "current date of you are trading at, yyyy-mm-dd",
        ],
    ):
        """
        Retrieve insider sentiment information about a company (retrieved from public SEC information) for the past 30 days
        Args:
            ticker (str): ticker symbol of the company
            curr_date (str): current date you are trading at, yyyy-mm-dd
        Returns:
            str: a report of the sentiment in the past 30 days starting at curr_date
        """

        cfg = Toolkit._config
        if bool(cfg.get("online_tools", True)) or bool(cfg.get("force_online", False)):
            try:
                return Toolkit._trim_tool_output(interface.get_finnhub_company_insider_sentiment_online(
                    ticker, curr_date, 30
                ))
            except Exception:
                pass
        data_sentiment = interface.get_finnhub_company_insider_sentiment(ticker, curr_date, 30)

        return Toolkit._trim_tool_output(data_sentiment)

    @staticmethod
    @tool
    def get_finnhub_company_insider_transactions(
        ticker: Annotated[str, "ticker symbol"],
        curr_date: Annotated[
            str,
            "current date you are trading at, yyyy-mm-dd",
        ],
    ):
        """
        Retrieve insider transaction information about a company (retrieved from public SEC information) for the past 30 days
        Args:
            ticker (str): ticker symbol of the company
            curr_date (str): current date you are trading at, yyyy-mm-dd
        Returns:
            str: a report of the company's insider transactions/trading information in the past 30 days
        """

        cfg = Toolkit._config
        if bool(cfg.get("online_tools", True)) or bool(cfg.get("force_online", False)):
            try:
                return Toolkit._trim_tool_output(interface.get_finnhub_company_insider_transactions_online(
                    ticker, curr_date, 30
                ))
            except Exception:
                pass
        data_trans = interface.get_finnhub_company_insider_transactions(ticker, curr_date, 30)

        return Toolkit._trim_tool_output(data_trans)

    @staticmethod
    @tool
    def get_simfin_balance_sheet(
        ticker: Annotated[str, "ticker symbol"],
        freq: Annotated[
            str,
            "reporting frequency of the company's financial history: annual/quarterly",
        ],
        curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    ):
        """
        Retrieve the most recent balance sheet of a company
        Args:
            ticker (str): ticker symbol of the company
            freq (str): reporting frequency of the company's financial history: annual / quarterly
            curr_date (str): current date you are trading at, yyyy-mm-dd
        Returns:
            str: a report of the company's most recent balance sheet
        """

        cfg = Toolkit._config
        if bool(cfg.get("online_tools", True)):
            try:
                return Toolkit._trim_tool_output(interface.get_simfin_balance_sheet_online(ticker, freq, curr_date))
            except Exception:
                pass
        data_balance_sheet = interface.get_simfin_balance_sheet(ticker, freq, curr_date)

        return Toolkit._trim_tool_output(data_balance_sheet)

    @staticmethod
    @tool
    def get_simfin_cashflow(
        ticker: Annotated[str, "ticker symbol"],
        freq: Annotated[
            str,
            "reporting frequency of the company's financial history: annual/quarterly",
        ],
        curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    ):
        """
        Retrieve the most recent cash flow statement of a company
        Args:
            ticker (str): ticker symbol of the company
            freq (str): reporting frequency of the company's financial history: annual / quarterly
            curr_date (str): current date you are trading at, yyyy-mm-dd
        Returns:
                str: a report of the company's most recent cash flow statement
        """

        cfg = Toolkit._config
        if bool(cfg.get("online_tools", True)):
            try:
                return Toolkit._trim_tool_output(interface.get_simfin_cashflow_online(ticker, freq, curr_date))
            except Exception:
                pass
        data_cashflow = interface.get_simfin_cashflow(ticker, freq, curr_date)

        return Toolkit._trim_tool_output(data_cashflow)

    @staticmethod
    @tool
    def get_simfin_income_stmt(
        ticker: Annotated[str, "ticker symbol"],
        freq: Annotated[
            str,
            "reporting frequency of the company's financial history: annual/quarterly",
        ],
        curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    ):
        """
        Retrieve the most recent income statement of a company
        Args:
            ticker (str): ticker symbol of the company
            freq (str): reporting frequency of the company's financial history: annual / quarterly
            curr_date (str): current date you are trading at, yyyy-mm-dd
        Returns:
                str: a report of the company's most recent income statement
        """

        cfg = Toolkit._config
        if bool(cfg.get("online_tools", True)):
            try:
                return Toolkit._trim_tool_output(interface.get_simfin_income_statements_online(ticker, freq, curr_date))
            except Exception:
                pass
        data_income_stmt = interface.get_simfin_income_statements(ticker, freq, curr_date)

        return Toolkit._trim_tool_output(data_income_stmt)

    @staticmethod
    @tool
    def get_google_news(
        query: Annotated[str, "Query to search with"],
        curr_date: Annotated[str, "Curr date in yyyy-mm-dd format"],
    ):
        """
        Retrieve the latest news from Google News based on a query and date range.
        Args:
            query (str): Query to search with
            curr_date (str): Current date in yyyy-mm-dd format
            look_back_days (int): How many days to look back
        Returns:
            str: A formatted string containing the latest news from Google News based on the query and date range.
        """

        cfg = Toolkit._config
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            if bool(cfg.get("use_multi_layer_window", False)):
                return Toolkit._trim_tool_output(interface.get_google_news_multi(query, curr_date))
            return Toolkit._trim_tool_output(interface.get_google_news_auto(query, curr_date))
        google_news_results = interface.get_google_news(query, curr_date, 7)

        return Toolkit._trim_tool_output(google_news_results)

    @staticmethod
    @tool
    def get_stock_news_openai(
        ticker: Annotated[str, "the company's ticker"],
        curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    ):
        """
        Retrieve the latest news about a given stock by using OpenAI's news API.
        Args:
            ticker (str): Ticker of a company. e.g. AAPL, TSM
            curr_date (str): Current date in yyyy-mm-dd format
        Returns:
            str: A formatted string containing the latest news about the company on the given date.
        """

        openai_news_results = interface.get_stock_news_openai(ticker, curr_date)

        return Toolkit._trim_tool_output(openai_news_results)

    @staticmethod
    @tool
    def get_global_news_openai(
        curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    ):
        """
        Retrieve the latest macroeconomics news on a given date using OpenAI's macroeconomics news API.
        Args:
            curr_date (str): Current date in yyyy-mm-dd format
        Returns:
            str: A formatted string containing the latest macroeconomic news on the given date.
        """

        openai_news_results = interface.get_global_news_openai(curr_date)

        return Toolkit._trim_tool_output(openai_news_results)

    @staticmethod
    @tool
    def get_fundamentals_openai(
        ticker: Annotated[str, "the company's ticker"],
        curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    ):
        """
        Retrieve the latest fundamental information about a given stock on a given date by using OpenAI's news API.
        Args:
            ticker (str): Ticker of a company. e.g. AAPL, TSM
            curr_date (str): Current date in yyyy-mm-dd format
        Returns:
            str: A formatted string containing the latest fundamental information about the company on the given date.
        """

        openai_fundamentals_results = interface.get_fundamentals_openai(
            ticker, curr_date
        )

        return Toolkit._trim_tool_output(openai_fundamentals_results)
