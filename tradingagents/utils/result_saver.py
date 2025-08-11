from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _is_non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def compose_final_report_markdown(final_state: Dict[str, Any], ui_lang: str = "zh") -> str:
    """Compose a combined Markdown report from final_state.

    Only includes non-empty sections. Headings are localized by ui_lang.
    """
    zh = ui_lang.lower() == "zh"

    def t(en: str, cn: str) -> str:
        return cn if zh else en

    parts: list[str] = []

    # Analyst Team Reports
    any_analyst = any(
        _is_non_empty_text(final_state.get(k))
        for k in ("market_report", "sentiment_report", "news_report", "fundamentals_report")
    )
    if any_analyst:
        parts.append(t("## Analyst Team Reports", "## 分析师团队报告"))
        if _is_non_empty_text(final_state.get("market_report")):
            parts.append("### " + t("Market Analysis", "市场分析") + "\n" + str(final_state.get("market_report", "")))
        if _is_non_empty_text(final_state.get("sentiment_report")):
            parts.append("### " + t("Social Sentiment", "社交情绪") + "\n" + str(final_state.get("sentiment_report", "")))
        if _is_non_empty_text(final_state.get("news_report")):
            parts.append("### " + t("News Analysis", "新闻分析") + "\n" + str(final_state.get("news_report", "")))
        if _is_non_empty_text(final_state.get("fundamentals_report")):
            parts.append("### " + t("Fundamentals Analysis", "基本面分析") + "\n" + str(final_state.get("fundamentals_report", "")))

    # Research Team Decision
    if _is_non_empty_text(final_state.get("investment_plan")):
        parts.append(t("## Research Team Decision", "## 研究团队决策"))
        parts.append(str(final_state.get("investment_plan", "")))

    # Trading Team Plan
    if _is_non_empty_text(final_state.get("trader_investment_plan")):
        parts.append(t("## Trading Team Plan", "## 交易团队方案"))
        parts.append(str(final_state.get("trader_investment_plan", "")))

    # Portfolio Management Decision
    if _is_non_empty_text(final_state.get("final_trade_decision")):
        parts.append(t("## Portfolio Management Decision", "## 组合经理决策"))
        parts.append(str(final_state.get("final_trade_decision", "")))

    return "\n\n".join(parts)


def save_analysis_results(
    config: Dict[str, Any],
    ticker: str,
    analysis_date: str,
    final_state: Dict[str, Any],
    decision: str,
    ui_lang: str = "zh",
) -> Dict[str, str]:
    """Save analysis outputs to local disk under results/<ticker>/<date>/.

    Writes:
    - final_state.json: full final_state
    - final_report.md: combined Markdown report
    - decision.txt: BUY/SELL/HOLD decision
    - reports/<section>.md: each individual section (idempotent)
    Returns a dict of saved file paths.
    """
    base_results_dir = Path(config.get("results_dir", "./results"))
    out_dir = base_results_dir / str(ticker) / str(analysis_date)
    reports_dir = out_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Save full state
    final_state_path = out_dir / "final_state.json"
    with open(final_state_path, "w", encoding="utf-8") as f:
        json.dump(final_state, f, ensure_ascii=False, indent=2)

    # Save combined report
    combined_md = compose_final_report_markdown(final_state, ui_lang=ui_lang)
    final_report_path = out_dir / "final_report.md"
    if combined_md.strip():
        with open(final_report_path, "w", encoding="utf-8") as f:
            f.write(combined_md)
    else:
        # Still create an empty placeholder to indicate completion
        final_report_path.touch(exist_ok=True)

    # Save decision
    decision_path = out_dir / "decision.txt"
    with open(decision_path, "w", encoding="utf-8") as f:
        f.write(str(decision).strip())

    # Save individual sections for convenience (idempotent with CLI behavior)
    section_to_file = {
        "market_report": "market_report.md",
        "sentiment_report": "sentiment_report.md",
        "news_report": "news_report.md",
        "fundamentals_report": "fundamentals_report.md",
        "investment_plan": "investment_plan.md",
        "trader_investment_plan": "trader_investment_plan.md",
        "final_trade_decision": "final_trade_decision.md",
    }
    for key, fname in section_to_file.items():
        content = final_state.get(key)
        if _is_non_empty_text(content):
            with open(reports_dir / fname, "w", encoding="utf-8") as f:
                f.write(str(content))

    return {
        "base_dir": str(out_dir),
        "final_state": str(final_state_path),
        "final_report": str(final_report_path),
        "decision": str(decision_path),
        "reports_dir": str(reports_dir),
    }


