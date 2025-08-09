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
                    return online
            except Exception:
                pass
        if force_online:
            return ""  # 严格在线模式下不回退
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive" and bool(cfg.get("use_multi_layer_window", False)):
            return interface.get_reddit_global_news_multi(curr_date)
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            return interface.get_reddit_global_news_auto(curr_date)
        global_news_result = interface.get_reddit_global_news(curr_date, 7, int(cfg.get("news_max_per_day", 5)))

        return global_news_result

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
                    return online
            except Exception:
                pass
        if force_online:
            return ""
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive" and bool(cfg.get("use_multi_layer_window", False)):
            return interface.get_reddit_company_news_multi(ticker, curr_date)
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            return interface.get_reddit_company_news_auto(ticker, curr_date)
        stock_news_results = interface.get_reddit_company_news(ticker, curr_date, 7, int(cfg.get("news_max_per_day", 5)))

        return stock_news_results

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
                        return interface.get_YFin_data_online_multi(symbol, end_date)
                    return interface.get_YFin_data_online_auto(symbol, end_date)
            except Exception:
                pass
            return interface.get_YFin_data_online(symbol, start_date, end_date)
        try:
            if start_date == "auto" and str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
                if bool(cfg.get("use_multi_layer_window", False)):
                    return interface.get_YFin_data_multi(symbol, end_date)
                return interface.get_YFin_data_auto(symbol, end_date)
        except Exception:
            pass
        result_data = interface.get_YFin_data(symbol, start_date, end_date)

        return result_data

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
                    return interface.get_YFin_data_online_multi(symbol, end_date)
                return interface.get_YFin_data_online_auto(symbol, end_date)
        except Exception:
            pass
        result_data = interface.get_YFin_data_online(symbol, start_date, end_date)

        return result_data

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
                    return interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, True)
                return interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, True)
            return interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, True)
        if str(cfg.get("windowing_mode", "adaptive")).lower() == "adaptive":
            if bool(cfg.get("use_multi_layer_window", False)):
                return interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, False)
            return interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, False)
        result_stockstats = interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, False)

        return result_stockstats

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
                return interface.get_stock_stats_indicators_multi(symbol, indicator, curr_date, True)
            return interface.get_stock_stats_indicators_auto(symbol, indicator, curr_date, True)
        result_stockstats = interface.get_stock_stats_indicators_window(symbol, indicator, curr_date, look_back_days, True)

        return result_stockstats

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
                return interface.get_finnhub_company_insider_sentiment_online(
                    ticker, curr_date, 30
                )
            except Exception:
                pass
        data_sentiment = interface.get_finnhub_company_insider_sentiment(ticker, curr_date, 30)

        return data_sentiment

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
                return interface.get_finnhub_company_insider_transactions_online(
                    ticker, curr_date, 30
                )
            except Exception:
                pass
        data_trans = interface.get_finnhub_company_insider_transactions(ticker, curr_date, 30)

        return data_trans

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
                return interface.get_simfin_balance_sheet_online(ticker, freq, curr_date)
            except Exception:
                pass
        data_balance_sheet = interface.get_simfin_balance_sheet(ticker, freq, curr_date)

        return data_balance_sheet

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
                return interface.get_simfin_cashflow_online(ticker, freq, curr_date)
            except Exception:
                pass
        data_cashflow = interface.get_simfin_cashflow(ticker, freq, curr_date)

        return data_cashflow

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
                return interface.get_simfin_income_statements_online(ticker, freq, curr_date)
            except Exception:
                pass
        data_income_stmt = interface.get_simfin_income_statements(ticker, freq, curr_date)

        return data_income_stmt

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
                return interface.get_google_news_multi(query, curr_date)
            return interface.get_google_news_auto(query, curr_date)
        google_news_results = interface.get_google_news(query, curr_date, 7)

        return google_news_results

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

        return openai_news_results

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

        return openai_news_results

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

        return openai_fundamentals_results
