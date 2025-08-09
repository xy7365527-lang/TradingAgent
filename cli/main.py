from typing import Optional
import os
import questionary
import datetime
import typer
from pathlib import Path
from functools import wraps
from rich.console import Console
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live
from rich.columns import Columns
from rich.markdown import Markdown
from rich.layout import Layout
from rich.text import Text
from rich.live import Live
from rich.table import Table
from collections import deque
import time
from rich.tree import Tree
from rich import box
from rich.align import Align
from rich.rule import Rule

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from cli.models import AnalystType
from cli.utils import *

console = Console()
# 界面语言：优先环境变量 TRADINGAGENTS_LANG，其次默认配置 ui_language
UI_LANG = os.getenv("TRADINGAGENTS_LANG", str(DEFAULT_CONFIG.get("ui_language", "zh"))).lower()

def _(en: str, zh: str) -> str:
    return zh if UI_LANG == "zh" else en

def _translate_to_zh(graph, text: str) -> str:
    try:
        if not isinstance(text, str) or not text.strip():
            return text
        prompt = "请将以下内容翻译为简体中文，保持金融术语准确，保留数字/单位/符号，不要添加解释：\n\n" + text
        res = graph.quick_thinking_llm.invoke(prompt)
        content = getattr(res, "content", res)
        if isinstance(content, list):
            parts = []
            for it in content:
                if isinstance(it, dict) and it.get("type") == "text":
                    parts.append(it.get("text", ""))
            content = "\n".join(parts)
        return str(content).strip() or text
    except Exception:
        return text

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


def _ensure_cert_bundle_cli() -> None:
    """在 CLI 启动时设置证书环境，避免在含非 ASCII 路径的打包目录下触发 curl (77)。"""
    try:
        import certifi  # type: ignore
        import shutil

        ca_src = certifi.where()

        def is_ascii_path(p: str) -> bool:
            try:
                p.encode("ascii")
                return True
            except Exception:
                return False

        candidates: list[str] = []
        if os.name == "nt":
            program_data = os.environ.get("ProgramData", r"C:\\ProgramData")
            public_dir = os.environ.get("PUBLIC", r"C:\\Users\\Public")
            candidates = [
                os.path.join(program_data, "TradingAgents", "certs"),
                os.path.join(public_dir, "TradingAgents", "certs"),
            ]
        candidates = [p for p in candidates if is_ascii_path(p)]

        ca_dst = ca_src
        for base in candidates:
            try:
                os.makedirs(base, exist_ok=True)
                dst = os.path.join(base, "cacert.pem")
                if not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(ca_src)):
                    shutil.copyfile(ca_src, dst)
                ca_dst = dst
                break
            except Exception:
                continue

        for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            os.environ[var] = ca_dst
    except Exception:
        pass

# 提前设置证书环境，避免后续导入/网络调用时触发 curl 证书错误
_ensure_cert_bundle_cli()


# Create a deque to store recent messages with a maximum length
class MessageBuffer:
    def __init__(self, max_length=100):
        self.messages = deque(maxlen=max_length)
        self.tool_calls = deque(maxlen=max_length)
        self.current_report = None
        self.final_report = None  # Store the complete final report
        self.agent_status = {
            # Analyst Team
            "Market Analyst": "pending",
            "Social Analyst": "pending",
            "News Analyst": "pending",
            "Fundamentals Analyst": "pending",
            # Research Team
            "Bull Researcher": "pending",
            "Bear Researcher": "pending",
            "Research Manager": "pending",
            # Trading Team
            "Trader": "pending",
            # Risk Management Team
            "Risky Analyst": "pending",
            "Neutral Analyst": "pending",
            "Safe Analyst": "pending",
            # Portfolio Management Team
            "Portfolio Manager": "pending",
        }
        self.current_agent = None
        self.report_sections = {
            "market_report": None,
            "sentiment_report": None,
            "news_report": None,
            "fundamentals_report": None,
            "investment_plan": None,
            "trader_investment_plan": None,
            "final_trade_decision": None,
        }

    def add_message(self, message_type, content):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.messages.append((timestamp, message_type, content))

    def add_tool_call(self, tool_name, args):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.tool_calls.append((timestamp, tool_name, args))

    def update_agent_status(self, agent, status):
        if agent in self.agent_status:
            self.agent_status[agent] = status
            self.current_agent = agent

    def update_report_section(self, section_name, content):
        if section_name in self.report_sections:
            self.report_sections[section_name] = content
            self._update_current_report()

    def _update_current_report(self):
        # For the panel display, only show the most recently updated section
        latest_section = None
        latest_content = None

        # Find the most recently updated section
        for section, content in self.report_sections.items():
            if content is not None:
                latest_section = section
                latest_content = content
               
        if latest_section and latest_content:
            # Format the current section for display
            section_titles = {
                "market_report": _("Market Analysis", "市场分析"),
                "sentiment_report": _("Social Sentiment", "社交情绪"),
                "news_report": _("News Analysis", "新闻分析"),
                "fundamentals_report": _("Fundamentals Analysis", "基本面分析"),
                "investment_plan": _("Research Team Decision", "研究团队决策"),
                "trader_investment_plan": _("Trading Team Plan", "交易团队方案"),
                "final_trade_decision": _("Portfolio Management Decision", "组合经理决策"),
            }
            self.current_report = (
                f"### {section_titles[latest_section]}\n{latest_content}"
            )

        # Update the final complete report
        self._update_final_report()

    def _update_final_report(self):
        report_parts = []

        # Analyst Team Reports
        if any(
            self.report_sections[section]
            for section in [
                "market_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
            ]
        ):
            report_parts.append(_("## Analyst Team Reports", "## 分析师团队报告"))
            if self.report_sections["market_report"]:
                title_market = _("Market Analysis", "市场分析")
                report_parts.append("### " + title_market + "\n" + str(self.report_sections['market_report']))
            if self.report_sections["sentiment_report"]:
                title_sent = _("Social Sentiment", "社交情绪")
                report_parts.append("### " + title_sent + "\n" + str(self.report_sections['sentiment_report']))
            if self.report_sections["news_report"]:
                title_news = _("News Analysis", "新闻分析")
                report_parts.append("### " + title_news + "\n" + str(self.report_sections['news_report']))
            if self.report_sections["fundamentals_report"]:
                title_fund = _("Fundamentals Analysis", "基本面分析")
                report_parts.append("### " + title_fund + "\n" + str(self.report_sections['fundamentals_report']))

        # Research Team Reports
        if self.report_sections["investment_plan"]:
            report_parts.append(_("## Research Team Decision", "## 研究团队决策"))
            report_parts.append(f"{self.report_sections['investment_plan']}")

        # Trading Team Reports
        if self.report_sections["trader_investment_plan"]:
            report_parts.append(_("## Trading Team Plan", "## 交易团队方案"))
            report_parts.append(f"{self.report_sections['trader_investment_plan']}")

        # Portfolio Management Decision
        if self.report_sections["final_trade_decision"]:
            report_parts.append(_("## Portfolio Management Decision", "## 组合经理决策"))
            report_parts.append(f"{self.report_sections['final_trade_decision']}")

        self.final_report = "\n\n".join(report_parts) if report_parts else None


message_buffer = MessageBuffer()


def create_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_column(
        Layout(name="upper", ratio=3), Layout(name="analysis", ratio=5)
    )
    layout["upper"].split_row(
        Layout(name="progress", ratio=2), Layout(name="messages", ratio=3)
    )
    return layout


def update_display(layout, spinner_text=None):
    # Header with welcome message
    layout["header"].update(
        Panel(
            _(
                "[bold green]Welcome to TradingAgents CLI[/bold green]\n[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
                "[bold green]欢迎使用 TradingAgents CLI[/bold green]\n[dim]© [Tauric Research](https://github.com/TauricResearch)[/dim]",
            ),
            title=_("Welcome to TradingAgents", "TradingAgents 欢迎页"),
            border_style="green",
            padding=(1, 2),
            expand=True,
        )
    )

    # Progress panel showing agent status
    progress_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        box=box.SIMPLE_HEAD,  # Use simple header with horizontal lines
        title=None,  # Remove the redundant Progress title
        padding=(0, 2),  # Add horizontal padding
        expand=True,  # Make table expand to fill available space
    )
    progress_table.add_column(_("Team", "团队"), style="cyan", justify="center", width=20)
    progress_table.add_column(_("Agent", "角色"), style="green", justify="center", width=20)
    progress_table.add_column(_("Status", "状态"), style="yellow", justify="center", width=20)

    # Agent/team labels (display only) while internal keys remain English
    AGENT_LABELS = {
        "Market Analyst": _("Market Analyst", "市场分析师"),
        "Social Analyst": _("Social Analyst", "社媒分析师"),
        "News Analyst": _("News Analyst", "新闻分析师"),
        "Fundamentals Analyst": _("Fundamentals Analyst", "基本面分析师"),
        "Bull Researcher": _("Bull Researcher", "多头研究员"),
        "Bear Researcher": _("Bear Researcher", "空头研究员"),
        "Research Manager": _("Research Manager", "研究经理"),
        "Trader": _("Trader", "交易员"),
        "Risky Analyst": _("Risky Analyst", "激进风控"),
        "Neutral Analyst": _("Neutral Analyst", "中性风控"),
        "Safe Analyst": _("Safe Analyst", "保守风控"),
        "Portfolio Manager": _("Portfolio Manager", "组合经理"),
    }
    TEAM_LABELS = {
        "Analyst Team": _("Analyst Team", "分析师团队"),
        "Research Team": _("Research Team", "研究团队"),
        "Trading Team": _("Trading Team", "交易团队"),
        "Risk Management": _("Risk Management", "风控团队"),
        "Portfolio Management": _("Portfolio Management", "投资组合管理"),
    }
    # Group agents by team (internal keys)
    teams = {
        "Analyst Team": [
            "Market Analyst",
            "Social Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ],
        "Research Team": ["Bull Researcher", "Bear Researcher", "Research Manager"],
        "Trading Team": ["Trader"],
        "Risk Management": ["Risky Analyst", "Neutral Analyst", "Safe Analyst"],
        "Portfolio Management": ["Portfolio Manager"],
    }

    for team, agents in teams.items():
        # Add first agent with team name
        first_agent = agents[0]
        status = message_buffer.agent_status[first_agent]
        if status == "in_progress":
            _inprog = _("in_progress", "进行中")
            spinner = Spinner(
                "dots", text="[blue]" + _inprog + "[/blue]", style="bold cyan"
            )
            status_cell = spinner
        else:
            status_color = {
                "pending": "yellow",
                "completed": "green",
                "error": "red",
            }.get(status, "white")
            status_map = {
                "pending": _("pending", "等待中"),
                "completed": _("completed", "已完成"),
                "error": _("error", "错误"),
            }
            status_label = status_map.get(status, status)
            status_cell = f"[{status_color}]{status_label}[/{status_color}]"
        progress_table.add_row(TEAM_LABELS.get(team, team), AGENT_LABELS.get(first_agent, first_agent), status_cell)

        # Add remaining agents in team
        for agent in agents[1:]:
            status = message_buffer.agent_status[agent]
            if status == "in_progress":
                _inprog2 = _("in_progress", "进行中")
                spinner = Spinner(
                    "dots", text="[blue]" + _inprog2 + "[/blue]", style="bold cyan"
                )
                status_cell = spinner
            else:
                status_color = {
                    "pending": "yellow",
                    "completed": "green",
                    "error": "red",
                }.get(status, "white")
                status_map = {
                    "pending": _("pending", "等待中"),
                    "completed": _("completed", "已完成"),
                    "error": _("error", "错误"),
                }
                status_label = status_map.get(status, status)
                status_cell = f"[{status_color}]{status_label}[/{status_color}]"
            progress_table.add_row("", AGENT_LABELS.get(agent, agent), status_cell)

        # Add horizontal line after each team
        progress_table.add_row("─" * 20, "─" * 20, "─" * 20, style="dim")

    layout["progress"].update(
        Panel(progress_table, title=_("Progress", "进度"), border_style="cyan", padding=(1, 2))
    )

    # Messages panel showing recent messages and tool calls
    messages_table = Table(
        show_header=True,
        header_style="bold magenta",
        show_footer=False,
        expand=True,  # Make table expand to fill available space
        box=box.MINIMAL,  # Use minimal box style for a lighter look
        show_lines=True,  # Keep horizontal lines
        padding=(0, 1),  # Add some padding between columns
    )
    messages_table.add_column(_("Time", "时间"), style="cyan", width=8, justify="center")
    messages_table.add_column(_("Type", "类型"), style="green", width=10, justify="center")
    messages_table.add_column(
        "Content", style="white", no_wrap=False, ratio=1
    )  # Make content column expand

    # Combine tool calls and messages
    all_messages = []

    # Add tool calls
    for timestamp, tool_name, args in message_buffer.tool_calls:
        # Truncate tool call args if too long
        if isinstance(args, str) and len(args) > 100:
            args = args[:97] + "..."
        all_messages.append((timestamp, "Tool", f"{tool_name}: {args}"))

    # Add regular messages
    for timestamp, msg_type, content in message_buffer.messages:
        # Convert content to string if it's not already
        content_str = content
        if isinstance(content, list):
            # Handle list of content blocks (Anthropic format)
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get('type') == 'text':
                        text_parts.append(item.get('text', ''))
                    elif item.get('type') == 'tool_use':
                        text_parts.append(f"[Tool: {item.get('name', 'unknown')}]")
                else:
                    text_parts.append(str(item))
            content_str = ' '.join(text_parts)
        elif not isinstance(content_str, str):
            content_str = str(content)
            
        # Truncate message content if too long
        if len(content_str) > 200:
            content_str = content_str[:197] + "..."
        all_messages.append((timestamp, msg_type, content_str))

    # Sort by timestamp
    all_messages.sort(key=lambda x: x[0])

    # Calculate how many messages we can show based on available space
    # Start with a reasonable number and adjust based on content length
    max_messages = 12  # Increased from 8 to better fill the space

    # Get the last N messages that will fit in the panel
    recent_messages = all_messages[-max_messages:]

    # Add messages to table
    for timestamp, msg_type, content in recent_messages:
        # Format content with word wrapping
        wrapped_content = Text(content, overflow="fold")
        messages_table.add_row(timestamp, msg_type, wrapped_content)

    if spinner_text:
        messages_table.add_row("", "Spinner", spinner_text)

    # Add a footer to indicate if messages were truncated
    if len(all_messages) > max_messages:
        messages_table.footer = (
            f"[dim]Showing last {max_messages} of {len(all_messages)} messages[/dim]"
        )

    layout["messages"].update(
        Panel(
            messages_table,
            title=_("Messages & Tools", "消息与工具"),
            border_style="blue",
            padding=(1, 2),
        )
    )

    # Analysis panel showing current report
    if message_buffer.current_report:
        layout["analysis"].update(
            Panel(
                Markdown(message_buffer.current_report),
                title=_("Current Report", "当前报告"),
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        layout["analysis"].update(
            Panel(
                _("[italic]Waiting for analysis report...[/italic]", "[italic]等待分析报告…[/italic]"),
                title=_("Current Report", "当前报告"),
                border_style="green",
                padding=(1, 2),
            )
        )

    # Footer with statistics
    tool_calls_count = len(message_buffer.tool_calls)
    llm_calls_count = sum(
        1 for _, msg_type, _ in message_buffer.messages if msg_type == "Reasoning"
    )
    reports_count = sum(
        1 for content in message_buffer.report_sections.values() if content is not None
    )

    stats_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    stats_table.add_column(_("Stats", "统计"), justify="center")
    stats_table.add_row(
        _(
            f"Tool Calls: {tool_calls_count} | LLM Calls: {llm_calls_count} | Generated Reports: {reports_count}",
            f"工具调用: {tool_calls_count} | LLM消息: {llm_calls_count} | 生成报告: {reports_count}",
        )
    )

    layout["footer"].update(Panel(stats_table, border_style="grey50"))


def get_user_selections():
    """Get all user selections before starting the analysis display."""
    # Display ASCII art welcome message（使用与文件同目录的绝对路径，兼容中文路径/打包运行）
    welcome_path = Path(__file__).parent / "static" / "welcome.txt"
    with open(welcome_path, "r", encoding="utf-8") as f:
        welcome_ascii = f.read()

    # Create welcome box content
    welcome_content = f"{welcome_ascii}\n"
    welcome_content += _(
        "[bold green]TradingAgents: Multi-Agents LLM Financial Trading Framework - CLI[/bold green]",
        "[bold green]TradingAgents：多智能体 LLM 量化交易框架 - CLI[/bold green]",
    ) + "\n\n"
    welcome_content += _("[bold]Workflow Steps:[/bold]", "[bold]工作流步骤：[/bold]") + "\n"
    welcome_content += _(
        "I. Analyst Team → II. Research Team → III. Trader → IV. Risk Management → V. Portfolio Management",
        "I. 分析师团队 → II. 研究团队 → III. 交易员 → IV. 风控团队 → V. 组合经理",
    ) + "\n\n"
    welcome_content += (
        "[dim]Built by [Tauric Research](https://github.com/TauricResearch)[/dim]"
    )

    # Create and center the welcome box
    welcome_box = Panel(
        welcome_content,
        border_style="green",
        padding=(1, 2),
        title=_("Welcome to TradingAgents", "欢迎使用 TradingAgents"),
        subtitle=_("Multi-Agents LLM Financial Trading Framework", "多智能体 LLM 量化交易框架"),
    )
    console.print(Align.center(welcome_box))
    console.print()  # Add a blank line after the welcome box

    # Create a boxed questionnaire for each step
    def create_question_box(title, prompt, default=None):
        box_content = f"[bold]{title}[/bold]\n"
        box_content += f"[dim]{prompt}[/dim]"
        if default:
            box_content += _(
                f"\n[dim]Default: {default}[/dim]",
                f"\n[dim]默认值：{default}[/dim]",
            )
        return Panel(box_content, border_style="blue", padding=(1, 2))

    # Step 1: Ticker symbol
    console.print(
        create_question_box(
            _("Step 1: Ticker Symbol", "步骤一：股票代码"),
            _("Enter the ticker symbol to analyze", "请输入要分析的股票代码"),
            "SPY",
        )
    )
    selected_ticker = get_ticker()

    # Step 2: Analysis date
    default_date = datetime.datetime.now().strftime("%Y-%m-%d")
    console.print(
        create_question_box(
            _("Step 2: Analysis Date", "步骤二：分析日期"),
            _("Enter the analysis date (YYYY-MM-DD)", "请输入分析日期 (YYYY-MM-DD)"),
            default_date,
        )
    )
    analysis_date = get_analysis_date()

    # Step 3: Select analysts
    console.print(
        create_question_box(
            _("Step 3: Analysts Team", "步骤三：分析师团队"),
            _("Select your LLM analyst agents for the analysis", "选择参与分析的 LLM 分析师")
        )
    )
    selected_analysts = select_analysts()
    console.print(
        _("[green]Selected analysts:[/green] ", "[green]已选择分析师：[/green] ")
        + ", ".join(analyst.value for analyst in selected_analysts)
    )

    # Step 4: Research depth
    console.print(
        create_question_box(
            _("Step 4: Research Depth", "步骤四：研究深度"),
            _("Select your research depth level", "选择研究深度等级")
        )
    )
    selected_research_depth = select_research_depth()

    # Step 5: OpenAI backend
    console.print(
        create_question_box(
            _("Step 5: OpenAI backend", "步骤五：LLM 提供商"),
            _("Select which service to talk to", "选择要连接的服务")
        )
    )
    selected_llm_provider, backend_url = select_llm_provider()
    
    # Step 6: Thinking agents
    console.print(
        create_question_box(
            _("Step 6: Thinking Agents", "步骤六：思考模型"),
            _("Select your thinking agents for analysis", "选择用于推理的模型")
        )
    )
    selected_shallow_thinker = select_shallow_thinking_agent(selected_llm_provider)
    selected_deep_thinker = select_deep_thinking_agent(selected_llm_provider)

    return {
        "ticker": selected_ticker,
        "analysis_date": analysis_date,
        "analysts": selected_analysts,
        "research_depth": selected_research_depth,
        "llm_provider": selected_llm_provider.lower(),
        "backend_url": backend_url,
        "shallow_thinker": selected_shallow_thinker,
        "deep_thinker": selected_deep_thinker,
    }


def get_ticker():
    """Get ticker symbol from user input."""
    return typer.prompt("", default="SPY")


def get_analysis_date():
    """Get the analysis date from user input."""
    while True:
        date_str = typer.prompt(
            "", default=datetime.datetime.now().strftime("%Y-%m-%d")
        )
        try:
            # Validate date format and ensure it's not in the future
            analysis_date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            if analysis_date.date() > datetime.datetime.now().date():
                console.print("[red]Error: Analysis date cannot be in the future[/red]")
                continue
            return date_str
        except ValueError:
            console.print(
                "[red]Error: Invalid date format. Please use YYYY-MM-DD[/red]"
            )


def display_complete_report(final_state):
    """Display the complete analysis report with team-based panels."""
    console.print("\n[bold green]Complete Analysis Report[/bold green]\n")

    # I. Analyst Team Reports
    analyst_reports = []

    # Market Analyst Report
    if final_state.get("market_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["market_report"]),
                title="Market Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Social Analyst Report
    if final_state.get("sentiment_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["sentiment_report"]),
                title="Social Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # News Analyst Report
    if final_state.get("news_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["news_report"]),
                title="News Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    # Fundamentals Analyst Report
    if final_state.get("fundamentals_report"):
        analyst_reports.append(
            Panel(
                Markdown(final_state["fundamentals_report"]),
                title="Fundamentals Analyst",
                border_style="blue",
                padding=(1, 2),
            )
        )

    if analyst_reports:
        console.print(
            Panel(
                Columns(analyst_reports, equal=True, expand=True),
                title="I. Analyst Team Reports",
                border_style="cyan",
                padding=(1, 2),
            )
        )

    # II. Research Team Reports
    if final_state.get("investment_debate_state"):
        research_reports = []
        debate_state = final_state["investment_debate_state"]

        # Bull Researcher Analysis
        if debate_state.get("bull_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bull_history"]),
                    title="Bull Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Bear Researcher Analysis
        if debate_state.get("bear_history"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["bear_history"]),
                    title="Bear Researcher",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Research Manager Decision
        if debate_state.get("judge_decision"):
            research_reports.append(
                Panel(
                    Markdown(debate_state["judge_decision"]),
                    title="Research Manager",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if research_reports:
            console.print(
                Panel(
                    Columns(research_reports, equal=True, expand=True),
                    title="II. Research Team Decision",
                    border_style="magenta",
                    padding=(1, 2),
                )
            )

    # III. Trading Team Reports
    if final_state.get("trader_investment_plan"):
        console.print(
            Panel(
                Panel(
                    Markdown(final_state["trader_investment_plan"]),
                    title="Trader",
                    border_style="blue",
                    padding=(1, 2),
                ),
                title="III. Trading Team Plan",
                border_style="yellow",
                padding=(1, 2),
            )
        )

    # IV. Risk Management Team Reports
    if final_state.get("risk_debate_state"):
        risk_reports = []
        risk_state = final_state["risk_debate_state"]

        # Aggressive (Risky) Analyst Analysis
        if risk_state.get("risky_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["risky_history"]),
                    title="Aggressive Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Conservative (Safe) Analyst Analysis
        if risk_state.get("safe_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["safe_history"]),
                    title="Conservative Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        # Neutral Analyst Analysis
        if risk_state.get("neutral_history"):
            risk_reports.append(
                Panel(
                    Markdown(risk_state["neutral_history"]),
                    title="Neutral Analyst",
                    border_style="blue",
                    padding=(1, 2),
                )
            )

        if risk_reports:
            console.print(
                Panel(
                    Columns(risk_reports, equal=True, expand=True),
                    title="IV. Risk Management Team Decision",
                    border_style="red",
                    padding=(1, 2),
                )
            )

        # V. Portfolio Manager Decision
        if risk_state.get("judge_decision"):
            console.print(
                Panel(
                    Panel(
                        Markdown(risk_state["judge_decision"]),
                        title="Portfolio Manager",
                        border_style="blue",
                        padding=(1, 2),
                    ),
                    title="V. Portfolio Manager Decision",
                    border_style="green",
                    padding=(1, 2),
                )
            )


def update_research_team_status(status):
    """Update status for all research team members and trader."""
    research_team = ["Bull Researcher", "Bear Researcher", "Research Manager", "Trader"]
    for agent in research_team:
        message_buffer.update_agent_status(agent, status)

def extract_content_string(content):
    """Extract string content from various message formats."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # Handle Anthropic's list format
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    text_parts.append(item.get('text', ''))
                elif item.get('type') == 'tool_use':
                    text_parts.append(f"[Tool: {item.get('name', 'unknown')}]")
            else:
                text_parts.append(str(item))
        return ' '.join(text_parts)
    else:
        return str(content)

def run_analysis():
    # First get all user selections
    selections = get_user_selections()

    # Create config with selected research depth
    config = DEFAULT_CONFIG.copy()
    config["max_debate_rounds"] = selections["research_depth"]
    config["max_risk_discuss_rounds"] = selections["research_depth"]
    config["quick_think_llm"] = selections["shallow_thinker"]
    config["deep_think_llm"] = selections["deep_thinker"]
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()

    # Ensure required API key exists or prompt user
    provider = config["llm_provider"].lower()
    required_env = None
    if provider == "openai":
        required_env = "OPENAI_API_KEY"
    elif provider == "anthropic":
        required_env = "ANTHROPIC_API_KEY"
    elif provider == "google":
        required_env = "GOOGLE_API_KEY"
    elif provider == "openrouter":
        required_env = "OPENROUTER_API_KEY"
    elif provider == "ollama":
        required_env = None

    if required_env and not os.getenv(required_env):
        key_val = questionary.password(
            f"未检测到 {required_env}，请输入你的 {selections['llm_provider']} API Key:") .ask()
        if not key_val:
            console.print(f"[red]未提供 {required_env}，已取消[/red]")
            raise typer.Exit(code=1)
        os.environ[required_env] = key_val

    # If using OpenRouter, also mirror to OPENAI_API_KEY for OpenAI-compatible SDKs
    if provider == "openrouter" and os.getenv("OPENAI_API_KEY") is None:
        key = os.getenv("OPENROUTER_API_KEY")
        if key:
            os.environ["OPENAI_API_KEY"] = key

    # Initialize the graph
    graph = TradingAgentsGraph(
        [analyst.value for analyst in selections["analysts"]], config=config, debug=True
    )

    # Create result directory
    results_dir = Path(config["results_dir"]) / selections["ticker"] / selections["analysis_date"]
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper
    
    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)
        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(content)
        return wrapper

    message_buffer.add_message = save_message_decorator(message_buffer, "add_message")
    message_buffer.add_tool_call = save_tool_call_decorator(message_buffer, "add_tool_call")
    message_buffer.update_report_section = save_report_section_decorator(message_buffer, "update_report_section")

    # Now start the display layout
    layout = create_layout()

    with Live(layout, refresh_per_second=4) as live:
        # Initial display
        update_display(layout)

        # Add initial messages
        message_buffer.add_message("System", _("Selected ticker: ", "已选择标的：") + f"{selections['ticker']}")
        message_buffer.add_message(
            "System", _("Analysis date: ", "分析日期：") + f"{selections['analysis_date']}"
        )
        message_buffer.add_message(
            "System",
            _("Selected analysts: ", "分析师：") + ", ".join(analyst.value for analyst in selections['analysts']),
        )
        update_display(layout)

        # Reset agent statuses
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "pending")

        # Reset report sections
        for section in message_buffer.report_sections:
            message_buffer.report_sections[section] = None
        message_buffer.current_report = None
        message_buffer.final_report = None

        # Update agent status to in_progress for the first analyst
        first_analyst = f"{selections['analysts'][0].value.capitalize()} Analyst"
        # internal status keys are English; map known values
        internal_first = {
            "Market Analyst": "Market Analyst",
            "Social Analyst": "Social Analyst",
            "News Analyst": "News Analyst",
            "Fundamentals Analyst": "Fundamentals Analyst",
        }.get(first_analyst, first_analyst)
        message_buffer.update_agent_status(internal_first, "in_progress")
        update_display(layout)

        # Create spinner text
        spinner_text = _(
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}...",
            f"正在分析 {selections['ticker']} @ {selections['analysis_date']}…",
        )
        update_display(layout, spinner_text)

        # Initialize state and get graph args
        init_agent_state = graph.propagator.create_initial_state(
            selections["ticker"], selections["analysis_date"]
        )
        args = graph.propagator.get_graph_args()

        # Stream the analysis
        trace = []
        for chunk in graph.graph.stream(init_agent_state, **args):
            if len(chunk["messages"]) > 0:
                # Get the last message from the chunk
                last_message = chunk["messages"][-1]

                # Extract message content and type
                if hasattr(last_message, "content"):
                    content = extract_content_string(last_message.content)  # Use the helper function
                    msg_type = "Reasoning"
                else:
                    content = str(last_message)
                    msg_type = "System"

                # Add message to buffer
                message_buffer.add_message(msg_type, content)                

                # If it's a tool call, add it to tool calls
                if hasattr(last_message, "tool_calls"):
                    for tool_call in last_message.tool_calls:
                        # Handle both dictionary and object tool calls
                        if isinstance(tool_call, dict):
                            message_buffer.add_tool_call(
                                tool_call["name"], tool_call["args"]
                            )
                        else:
                            message_buffer.add_tool_call(tool_call.name, tool_call.args)

                # Update reports and agent status based on chunk content
                # Analyst Team Reports
                if "market_report" in chunk and chunk["market_report"]:
                    message_buffer.update_report_section(
                        "market_report", chunk["market_report"]
                    )
                    message_buffer.update_agent_status("Market Analyst", "completed")
                    # Set next analyst to in_progress
                    if "social" in selections["analysts"]:
                        message_buffer.update_agent_status("Social Analyst", "in_progress")

                if "sentiment_report" in chunk and chunk["sentiment_report"]:
                    message_buffer.update_report_section(
                        "sentiment_report", chunk["sentiment_report"]
                    )
                    message_buffer.update_agent_status("Social Analyst", "completed")
                    # Set next analyst to in_progress
                    if "news" in selections["analysts"]:
                        message_buffer.update_agent_status("News Analyst", "in_progress")

                if "news_report" in chunk and chunk["news_report"]:
                    message_buffer.update_report_section(
                        "news_report", chunk["news_report"]
                    )
                    message_buffer.update_agent_status("News Analyst", "completed")
                    # Set next analyst to in_progress
                    if "fundamentals" in selections["analysts"]:
                        message_buffer.update_agent_status("Fundamentals Analyst", "in_progress")

                if "fundamentals_report" in chunk and chunk["fundamentals_report"]:
                    message_buffer.update_report_section(
                        "fundamentals_report", chunk["fundamentals_report"]
                    )
                    message_buffer.update_agent_status("Fundamentals Analyst", "completed")
                    # Set all research team members to in_progress
                    update_research_team_status("in_progress")

                # Research Team - Handle Investment Debate State
                if (
                    "investment_debate_state" in chunk
                    and chunk["investment_debate_state"]
                ):
                    debate_state = chunk["investment_debate_state"]

                    # Update Bull Researcher status and report
                    if "bull_history" in debate_state and debate_state["bull_history"]:
                        # Keep all research team members in progress
                        update_research_team_status("in_progress")
                        # Extract latest bull response
                        bull_responses = debate_state["bull_history"].split("\n")
                        latest_bull = bull_responses[-1] if bull_responses else ""
                        if latest_bull:
                            message_buffer.add_message("Reasoning", latest_bull)
                            # Update research report with bull's latest analysis
                            message_buffer.update_report_section(
                                "investment_plan",
                                f"### Bull Researcher Analysis\n{latest_bull}",
                            )

                    # Update Bear Researcher status and report
                    if "bear_history" in debate_state and debate_state["bear_history"]:
                        # Keep all research team members in progress
                        update_research_team_status("in_progress")
                        # Extract latest bear response
                        bear_responses = debate_state["bear_history"].split("\n")
                        latest_bear = bear_responses[-1] if bear_responses else ""
                        if latest_bear:
                            message_buffer.add_message("Reasoning", latest_bear)
                            # Update research report with bear's latest analysis
                            message_buffer.update_report_section(
                                "investment_plan",
                                f"{message_buffer.report_sections['investment_plan']}\n\n### Bear Researcher Analysis\n{latest_bear}",
                            )

                    # Update Research Manager status and final decision
                    if (
                        "judge_decision" in debate_state
                        and debate_state["judge_decision"]
                    ):
                        # Keep all research team members in progress until final decision
                        update_research_team_status("in_progress")
                        message_buffer.add_message(
                            "Reasoning",
                            _("Research Manager: ", "研究经理：") + f"{debate_state['judge_decision']}",
                        )
                        # Update research report with final decision
                        title_rm = _("Research Manager Decision", "研究经理决策")
                        message_buffer.update_report_section(
                            "investment_plan",
                            (str(message_buffer.report_sections['investment_plan']) if message_buffer.report_sections.get('investment_plan') else "")
                            + "\n\n### " + title_rm + "\n" + str(debate_state['judge_decision']),
                        )
                        # Mark all research team members as completed
                        update_research_team_status("completed")
                        # Set first risk analyst to in_progress
                        message_buffer.update_agent_status(
                            "Risky Analyst", "in_progress"
                        )

                # Trading Team
                if (
                    "trader_investment_plan" in chunk
                    and chunk["trader_investment_plan"]
                ):
                    message_buffer.update_report_section(
                        "trader_investment_plan", chunk["trader_investment_plan"]
                    )
                    # Set first risk analyst to in_progress
                    message_buffer.update_agent_status("Risky Analyst", "in_progress")

                # Risk Management Team - Handle Risk Debate State
                if "risk_debate_state" in chunk and chunk["risk_debate_state"]:
                    risk_state = chunk["risk_debate_state"]

                    # Update Risky Analyst status and report
                    if (
                        "current_risky_response" in risk_state
                        and risk_state["current_risky_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Risky Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            _("Risky Analyst: ", "激进风控：") + f"{risk_state['current_risky_response']}",
                        )
                        # Update risk report with risky analyst's latest analysis only
                        title_risky = _("Risky Analyst Analysis", "激进风控分析")
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            "### " + title_risky + "\n" + str(risk_state['current_risky_response']),
                        )

                    # Update Safe Analyst status and report
                    if (
                        "current_safe_response" in risk_state
                        and risk_state["current_safe_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Safe Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            _("Safe Analyst: ", "保守风控：") + f"{risk_state['current_safe_response']}",
                        )
                        # Update risk report with safe analyst's latest analysis only
                        title_safe = _("Safe Analyst Analysis", "保守风控分析")
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            "### " + title_safe + "\n" + str(risk_state['current_safe_response']),
                        )

                    # Update Neutral Analyst status and report
                    if (
                        "current_neutral_response" in risk_state
                        and risk_state["current_neutral_response"]
                    ):
                        message_buffer.update_agent_status(
                            "Neutral Analyst", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            _("Neutral Analyst: ", "中性风控：") + f"{risk_state['current_neutral_response']}",
                        )
                        # Update risk report with neutral analyst's latest analysis only
                        title_neutral = _("Neutral Analyst Analysis", "中性风控分析")
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            "### " + title_neutral + "\n" + str(risk_state['current_neutral_response']),
                        )

                    # Update Portfolio Manager status and final decision
                    if "judge_decision" in risk_state and risk_state["judge_decision"]:
                        message_buffer.update_agent_status(
                            "Portfolio Manager", "in_progress"
                        )
                        message_buffer.add_message(
                            "Reasoning",
                            _("Portfolio Manager: ", "组合经理：") + f"{risk_state['judge_decision']}",
                        )
                        # Update risk report with final decision only
                        title_pm = _("Portfolio Manager Decision", "组合经理决策")
                        message_buffer.update_report_section(
                            "final_trade_decision",
                            "### " + title_pm + "\n" + str(risk_state['judge_decision']),
                        )
                        # Mark risk analysts as completed
                        message_buffer.update_agent_status("Risky Analyst", "completed")
                        message_buffer.update_agent_status("Safe Analyst", "completed")
                        message_buffer.update_agent_status(
                            "Neutral Analyst", "completed"
                        )
                        message_buffer.update_agent_status(
                            "Portfolio Manager", "completed"
                        )

                # Update the display
                update_display(layout)

            trace.append(chunk)

        # Get final state and decision
        final_state = trace[-1]
        decision = graph.process_signal(final_state["final_trade_decision"])

        # Update all agent statuses to completed
        for agent in message_buffer.agent_status:
            message_buffer.update_agent_status(agent, "completed")

        message_buffer.add_message(
            "Analysis", f"Completed analysis for {selections['analysis_date']}"
        )

        # Update final report sections
        for section in message_buffer.report_sections.keys():
            if section in final_state:
                message_buffer.update_report_section(section, final_state[section])

        # Display the complete final report; auto-translate when UI is zh
        if UI_LANG == "zh":
            # Try to translate section contents while preserving keys
            translated = dict(final_state)
            for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report", "trader_investment_plan", "final_trade_decision"):
                if isinstance(translated.get(key), str):
                    translated[key] = _translate_to_zh(graph, translated[key])
            # investment_plan may contain headings; translate as a whole
            if isinstance(translated.get("investment_plan"), str):
                translated["investment_plan"] = _translate_to_zh(graph, translated["investment_plan"])
            display_complete_report(translated)
        else:
            display_complete_report(final_state)

        update_display(layout)


@app.command()
def analyze():
    run_analysis()


if __name__ == "__main__":
    import sys
    # 双击或无参数启动时，直接进入交互式分析界面；有参数时保持 Typer CLI 行为
    if len(sys.argv) == 1:
        analyze()
    else:
        app()
