import os

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),
    # Prefer env var; fallback to a local cache folder to avoid OS-specific absolute paths
    "data_dir": os.getenv(
        "TRADINGAGENTS_DATA_DIR",
        os.path.join(
            os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
            "dataflows",
            "data_cache",
        ),
    ),
    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),
    # LLM settings
    "llm_provider": "openai",
    # Default models per user preference
    "deep_think_llm": "gpt-5",
    "quick_think_llm": "gpt-5-mini",
    "backend_url": "https://api.openai.com/v1",
    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    # Tool settings
    "online_tools": True,
    # 当为 True 时：严格在线模式，禁用一切离线/缓存回退与自动切换离线
    "force_online": True,
    # Reddit API 凭证（如未设置环境变量，将回退使用这里的值）
    "reddit_client_id": "eUGTyjovYfEixeFljZ2NaA",
    "reddit_client_secret": "H-w9zoj8CftAREJ4Bac0tth-hRcktg",
    "reddit_user_agent": "SilenceHan personal use script",
    # 是否使用 OpenAI Responses 的 web_search 工具流（需 OPENAI_API_KEY 且提供商为 openai）
    "use_openai_responses": os.getenv("TRADINGAGENTS_USE_OAI_RESPONSES", "false").lower() == "true",
    # 是否使用 Responses 流式接口以等待工具完成
    "oai_responses_streaming": True,
    # UI language: "zh" for Simplified Chinese, "en" for English
    "ui_language": os.getenv("TRADINGAGENTS_LANG", "zh"),
    # Windowing strategy
    # windowing_mode: "fixed" uses hardcoded windows (news/social=7d, technical=30d, fundamentals=30d)
    #                 "adaptive" computes windows from fund style / holding period
    "windowing_mode": os.getenv("TRADINGAGENTS_WINDOWING_MODE", "adaptive"),
    # fund_style: high_turnover | medium_turnover | low_turnover
    "fund_style": os.getenv("TRADINGAGENTS_FUND_STYLE", "medium_turnover"),
    # Target holding period H in trading days (used when computing 0.5*H~0.9*H bands)
    "hold_period_days": int(os.getenv("TRADINGAGENTS_HOLD_DAYS", "28")),
    # Max per-day items for reddit/news scrapers (where applicable)
    "news_max_per_day": int(os.getenv("TRADINGAGENTS_NEWS_MAX_PER_DAY", "5")),
    # Multi-layer/volatility scaling toggles (reserved for future)
    "use_multi_layer_window": os.getenv("TRADINGAGENTS_MULTI_LAYER", "false").lower() == "true",
    "volatility_scale_windows": os.getenv("TRADINGAGENTS_VOL_SCALE", "false").lower() == "true",
    # Multi-window and half-life defaults (fast/mid/slow)
    "multi_technical_windows": [3, 14, 60],
    "multi_news_windows": [3, 7, 21],
    "multi_social_windows": [3, 7, 21],
    "half_life_technical_days": [2, 7, 30],
    "half_life_news_days": [2, 4, 10],
    "half_life_social_days": [2, 4, 10],
}
