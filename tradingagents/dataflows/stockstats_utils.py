import pandas as pd
import yfinance as yf
from stockstats import wrap
from typing import Annotated
import os
import time
import glob
from .config import get_config


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
        data_dir: Annotated[
            str,
            "directory where the stock data is stored.",
        ],
        online: Annotated[
            bool,
            "whether to use online tools to fetch data or offline tools. If True, will use online tools.",
        ] = False,
    ):
        df = None
        data = None

        # 强制在线模式绕过离线读取
        try:
            from .config import get_config
            if bool(get_config().get("force_online", False)):
                online = True
        except Exception:
            pass

        if not online:
            try:
                data = pd.read_csv(
                    os.path.join(
                        data_dir,
                        f"{symbol}-YFin-data-2015-01-01-2025-03-25.csv",
                    )
                )
                df = wrap(data)
            except FileNotFoundError:
                raise Exception("Stockstats fail: Yahoo Finance data not fetched yet!")
        else:
            # Get today's date as YYYY-mm-dd to add to cache
            today_date = pd.Timestamp.today()
            curr_date = pd.to_datetime(curr_date)

            end_date = today_date
            start_date = today_date - pd.DateOffset(years=15)
            start_date = start_date.strftime("%Y-%m-%d")
            end_date = end_date.strftime("%Y-%m-%d")

            # Get config and ensure cache directory exists（强制在线模式禁用缓存回退写入）
            config = get_config()
            force_online = bool(config.get("force_online", False))
            os.makedirs(config["data_cache_dir"], exist_ok=True)

            data_file = os.path.join(
                config["data_cache_dir"],
                f"{symbol}-YFin-data-{start_date}-{end_date}.csv",
            )

            if os.path.exists(data_file) and not force_online:
                data = pd.read_csv(data_file)
                data["Date"] = pd.to_datetime(data["Date"])
            else:
                # 网络下载增加重试与指数退避，并在失败时回退到最近的缓存文件
                downloaded = None
                last_err: Exception | None = None
                for attempt in range(3):
                    try:
                        downloaded = yf.download(
                            symbol,
                            start=start_date,
                            end=end_date,
                            multi_level_index=False,
                            progress=False,
                            auto_adjust=True,
                        )
                        break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        # 退避等待：1.5s, 3s, 6s
                        time.sleep(1.5 * (2 ** attempt))

                if downloaded is None or downloaded.empty:
                    # 优先使用最近的缓存文件作为回退
                    pattern = os.path.join(
                        config["data_cache_dir"], f"{symbol}-YFin-data-*.csv"
                    )
                    candidates = sorted(glob.glob(pattern), reverse=True)
                    if candidates and not force_online:
                        data = pd.read_csv(candidates[0])
                        data["Date"] = pd.to_datetime(data["Date"])
                    else:
                        # 再尝试使用离线静态数据（如仓库内预置的 CSV）
                        offline_dir = os.path.join(
                            config["data_dir"], "market_data", "price_data"
                        )
                        offline_candidates = sorted(
                            glob.glob(os.path.join(offline_dir, f"{symbol}-YFin-data-*.csv")),
                            reverse=True,
                        )
                        if offline_candidates and not force_online:
                            data = pd.read_csv(offline_candidates[0])
                            # 兼容不同列名大小写或日期列类型
                            if "Date" in data.columns:
                                data["Date"] = pd.to_datetime(data["Date"])  
                            elif "date" in data.columns:
                                data.rename(columns={"date": "Date"}, inplace=True)
                                data["Date"] = pd.to_datetime(data["Date"])  
                        else:
                            # 强制在线：直接提示网络错误
                            return "N/A: force_online enabled and network fetch failed"
                else:
                    data = downloaded.reset_index()
                    if not force_online:
                        data.to_csv(data_file, index=False)

            df = wrap(data)
            df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
            curr_date = curr_date.strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
