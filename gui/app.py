import os
import sys
import json
import time
import threading
import datetime
import io
from contextlib import redirect_stdout
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter import simpledialog
import tkinter.font as tkfont
from typing import Dict, Any, List
import requests

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


class _GuiTextWriter:
    """简单的 file-like 写入器：把 stdout/stderr 写入 Tk 文本控件。

    - 跨线程安全：write 在任意线程调用，实际 UI 更新通过 after 投递到主线程。
    - 按行缓冲：尽量按换行分段输出，避免半行碎片频繁刷新。
    """

    def __init__(self, app: "TradingAgentsGUI", is_error: bool = False) -> None:
        self._app = app
        self._is_error = is_error
        self._buffer: str = ""
        self._lock = threading.Lock()
        # 一些库会访问 .encoding 判断输出流编码
        self.encoding = "utf-8"

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            s = str(s)
        with self._lock:
            self._buffer += s
            # 尽量成行输出
            while True:
                idx = self._buffer.find("\n")
                if idx == -1:
                    break
                line = self._buffer[: idx + 1]
                self._buffer = self._buffer[idx + 1 :]
                self._emit(line)
        return len(s)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                self._emit(self._buffer)
                self._buffer = ""

    def isatty(self) -> bool:  # 一些库会根据此分支格式化
        return False

    def _emit(self, text: str) -> None:
        try:
            self._app.after(0, lambda t=text: self._app._append_from_io(t, self._is_error))
        except Exception:
            # 主线程可能已结束，回退到原控制台
            try:
                target = self._app._orig_stderr if self._is_error else self._app._orig_stdout
                target.write(text)
            except Exception:
                pass


# 分析师短名到显示名映射
ANALYST_DISPLAY_NAMES: Dict[str, str] = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}

class TradingAgentsGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self._ensure_cert_bundle()
        # 界面语言：优先环境变量 TRADINGAGENTS_LANG，其次默认配置 ui_language
        self.ui_lang: str = os.getenv("TRADINGAGENTS_LANG", str(DEFAULT_CONFIG.get("ui_language", "zh"))).lower()
        self._var_lang = tk.StringVar(value=self.ui_lang)
        self.title(self._t("TradingAgents - GUI Launcher", "TradingAgents - 可视化启动器"))
        # DPI 感知与缩放
        self._init_dpi_awareness()
        # 可调整大小，设置更宽敞的默认尺寸与最小尺寸
        self.geometry("1200x800")
        self.minsize(1000, 700)
        self.resizable(True, True)
        self._center_window()
        # 记录基础字体，支持后续缩放
        self._base_fonts: Dict[str, int] = self._snapshot_base_fonts()
        self._build_menu()
        # 初始化选项映射（根据当前语言构建中文/英文标签，并设置当前值）
        try:
            self._init_option_mappings()
        except Exception:
            pass
        # 翻译缓存（避免重复调用 LLM）
        self._translation_cache: Dict[str, str] = {}

        # 输入区域
        form = ttk.Frame(self, padding=12)
        form.pack(fill=tk.X)

        # Ticker（带下拉与刷新美股列表）
        self.lbl_ticker = ttk.Label(form, text=self._t("Ticker (US):", "标的 (美股):"))
        self.lbl_ticker.grid(row=0, column=0, sticky=tk.W)
        self.var_ticker = tk.StringVar(value="SPY")
        ticker_box = ttk.Frame(form)
        ticker_box.grid(row=0, column=1, sticky="nsew")
        self.cmb_ticker = ttk.Combobox(ticker_box, textvariable=self.var_ticker, width=20, state="normal")
        self.cmb_ticker.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cmb_ticker.bind("<KeyRelease>", self._on_ticker_typing)
        self.btn_refresh_tickers = ttk.Button(ticker_box, text=self._t("Refresh", "刷新股票"), width=10, command=lambda: self.refresh_tickers(async_mode=True))
        self.btn_refresh_tickers.pack(side=tk.LEFT, padx=(6, 0))
        self.lbl_tickers_time = ttk.Label(form, text="")
        self.lbl_tickers_time.grid(row=0, column=2, sticky=tk.W, padx=(12, 0))

        # 日期
        self.lbl_date = ttk.Label(form, text=self._t("Date (YYYY-MM-DD):", "日期 (YYYY-MM-DD):"))
        self.lbl_date.grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self.var_date = tk.StringVar(value=datetime.datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(form, textvariable=self.var_date, width=20).grid(row=0, column=3, sticky="nsew")

        # 研究深度
        self.lbl_depth = ttk.Label(form, text=self._t("Research Depth:", "研究深度:"))
        self.lbl_depth.grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.var_depth = tk.IntVar(value=1)
        depth_box = ttk.Frame(form)
        depth_box.grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.rb_depth_shallow = ttk.Radiobutton(depth_box, text=self._t("Shallow (1)", "浅(1)"), value=1, variable=self.var_depth)
        self.rb_depth_medium = ttk.Radiobutton(depth_box, text=self._t("Medium (3)", "中(3)"), value=3, variable=self.var_depth)
        self.rb_depth_deep = ttk.Radiobutton(depth_box, text=self._t("Deep (5)", "深(5)"), value=5, variable=self.var_depth)
        for rb in (self.rb_depth_shallow, self.rb_depth_medium, self.rb_depth_deep):
            rb.pack(side=tk.LEFT, padx=(0, 8))

        # 分析师选择
        self.lbl_analysts = ttk.Label(form, text=self._t("Analysts:", "分析师:"))
        self.lbl_analysts.grid(row=1, column=2, sticky=tk.W, padx=(12, 0), pady=(8, 0))
        self.var_market = tk.BooleanVar(value=True)
        self.var_social = tk.BooleanVar(value=True)
        self.var_news = tk.BooleanVar(value=True)
        self.var_fund = tk.BooleanVar(value=True)
        analysts_box = ttk.Frame(form)
        analysts_box.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self.chk_market = ttk.Checkbutton(analysts_box, text=self._t("Market", "市场"), variable=self.var_market)
        self.chk_social = ttk.Checkbutton(analysts_box, text=self._t("Social", "社交"), variable=self.var_social)
        self.chk_news = ttk.Checkbutton(analysts_box, text=self._t("News", "新闻"), variable=self.var_news)
        self.chk_fund = ttk.Checkbutton(analysts_box, text=self._t("Fundamentals", "基本面"), variable=self.var_fund)
        for cb in (self.chk_market, self.chk_social, self.chk_news, self.chk_fund):
            cb.pack(side=tk.LEFT)

        # 窗口策略（自适应/固定）
        self.lbl_win = ttk.Label(form, text=self._t("Windowing:", "窗口策略:"))
        self.lbl_win.grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        self.var_windowing = tk.StringVar(value=str(DEFAULT_CONFIG.get("windowing_mode", "adaptive")))
        self.cmb_win = ttk.Combobox(form, textvariable=self.var_windowing, state="readonly", width=18,
                                    values=["adaptive", "fixed"])
        self.cmb_win.grid(row=2, column=1, sticky="w", pady=(8, 0))

        # 基金风格与持仓期
        self.lbl_style = ttk.Label(form, text=self._t("Fund Style:", "基金风格:"))
        self.lbl_style.grid(row=2, column=2, sticky=tk.W, padx=(12, 0), pady=(8, 0))
        self.var_style = tk.StringVar(value=str(DEFAULT_CONFIG.get("fund_style", "medium_turnover")))
        self.cmb_style = ttk.Combobox(form, textvariable=self.var_style, state="readonly", width=18,
                                      values=["high_turnover", "medium_turnover", "low_turnover"])
        self.cmb_style.grid(row=2, column=3, sticky="w", pady=(8, 0))

        self.lbl_hold = ttk.Label(form, text=self._t("Hold Days:", "持仓期(交易日):"))
        self.lbl_hold.grid(row=3, column=0, sticky=tk.W)
        self.var_hold = tk.IntVar(value=int(DEFAULT_CONFIG.get("hold_period_days", 28)))
        self.ent_hold = ttk.Spinbox(form, from_=1, to=365, textvariable=self.var_hold, width=10)
        self.ent_hold.grid(row=3, column=1, sticky="w")

        # 多层窗口与波动缩放
        self.var_multi = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get("use_multi_layer_window", False)))
        self.chk_multi = ttk.Checkbutton(form, text=self._t("Multi-layer Windows", "三层窗口聚合"), variable=self.var_multi)
        self.chk_multi.grid(row=3, column=2, sticky=tk.W, padx=(12, 0))

        self.var_volscale = tk.BooleanVar(value=bool(DEFAULT_CONFIG.get("volatility_scale_windows", False)))
        self.chk_vol = ttk.Checkbutton(form, text=self._t("Volatility Scaling", "波动自适应(预留)"), variable=self.var_volscale)
        self.chk_vol.grid(row=3, column=3, sticky=tk.W)

        # 新闻/社媒每日抓取限制
        self.lbl_newsmax = ttk.Label(form, text=self._t("News/SM Max/day:", "新闻/社媒日上限:"))
        self.lbl_newsmax.grid(row=4, column=0, sticky=tk.W, pady=(8,0))
        self.var_newsmax = tk.IntVar(value=int(DEFAULT_CONFIG.get("news_max_per_day", 5)))
        self.ent_newsmax = ttk.Spinbox(form, from_=1, to=50, textvariable=self.var_newsmax, width=10)
        self.ent_newsmax.grid(row=4, column=1, sticky="w", pady=(8,0))

        # 模式选择（回测/纸上/实盘）
        self.lbl_mode = ttk.Label(form, text=self._t("Mode:", "模式:"))
        self.lbl_mode.grid(row=5, column=0, sticky=tk.W, pady=(8, 0))
        self.var_mode = tk.StringVar(value="backtest")
        mode_box = ttk.Frame(form)
        mode_box.grid(row=5, column=1, sticky="w", pady=(8, 0))
        self.rb_mode_backtest = ttk.Radiobutton(mode_box, text=self._t("Backtest", "回测"), value="backtest", variable=self.var_mode)
        self.rb_mode_paper = ttk.Radiobutton(mode_box, text=self._t("Paper", "纸上"), value="paper", variable=self.var_mode)
        self.rb_mode_live = ttk.Radiobutton(mode_box, text=self._t("Live", "实盘"), value="live", variable=self.var_mode)
        for rb in (self.rb_mode_backtest, self.rb_mode_paper, self.rb_mode_live):
            rb.pack(side=tk.LEFT, padx=(0, 8))

        # LLM Provider 与 URL、模型
        provider_row = ttk.Frame(self, padding=(12, 0))
        provider_row.pack(fill=tk.X)

        self.lbl_provider = ttk.Label(provider_row, text=self._t("LLM Provider:", "LLM提供商:"))
        self.lbl_provider.grid(row=0, column=0, sticky=tk.W)
        self.providers = {
            "OpenAI": "https://api.openai.com/v1",
            "Anthropic": "https://api.anthropic.com/",
            "Google": "https://generativelanguage.googleapis.com/v1",
            "Openrouter": "https://openrouter.ai/api/v1",
            "Ollama": "http://localhost:11434/v1",
        }
        self.var_provider = tk.StringVar(value="OpenAI")
        self.cmb_provider = ttk.Combobox(
            provider_row,
            textvariable=self.var_provider,
            values=list(self.providers.keys()),
            state="readonly",
            width=18,
        )
        self.cmb_provider.grid(row=0, column=1, sticky=tk.W)
        self.cmb_provider.bind("<<ComboboxSelected>>", lambda e: self.refresh_models(async_mode=True))

        self.lbl_quick_model = ttk.Label(provider_row, text=self._t("Quick Model:", "快速模型:"))
        self.lbl_quick_model.grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        # 使用可选下拉 + 稳定默认
        self.var_quick = tk.StringVar(value="gpt-5-mini")
        self.cmb_quick = ttk.Combobox(provider_row, textvariable=self.var_quick, width=28, state="normal")
        self.cmb_quick.grid(row=0, column=3, sticky=tk.W)

        self.lbl_deep_model = ttk.Label(provider_row, text=self._t("Deep Model:", "深度模型:"))
        self.lbl_deep_model.grid(row=0, column=4, sticky=tk.W, padx=(12, 0))
        self.var_deep = tk.StringVar(value="gpt-5")
        self.cmb_deep = ttk.Combobox(provider_row, textvariable=self.var_deep, width=28, state="normal")
        self.cmb_deep.grid(row=0, column=5, sticky=tk.W)

        # 刷新模型按钮与时间
        self.btn_refresh_models = ttk.Button(provider_row, text=self._t("Refresh Models", "刷新模型"), command=lambda: self.refresh_models(async_mode=True))
        self.btn_refresh_models.grid(row=0, column=6, padx=(12, 0))
        self.lbl_refresh_time = ttk.Label(provider_row, text="")
        self.lbl_refresh_time.grid(row=0, column=7, padx=(8, 0))

        # 操作按钮
        btns = ttk.Frame(self, padding=12)
        btns.pack(fill=tk.X)
        self.btn_start = ttk.Button(btns, text=self._t("Start", "开始分析"), command=self.on_start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_quick_template = ttk.Button(btns, text=self._t("Quick Template (SPY/Today)", "一键模板(SPY/今日)"), command=self.quick_template)
        self.btn_quick_template.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_save_preset = ttk.Button(btns, text=self._t("Save Preset", "保存预设"), command=self.save_preset)
        self.btn_save_preset.pack(side=tk.LEFT, padx=(8, 0))
        self.btn_load_preset = ttk.Button(btns, text=self._t("Load Preset", "加载预设"), command=self.load_preset)
        self.btn_load_preset.pack(side=tk.LEFT, padx=(8, 0))

        # 选项卡：输出 / 进度 / 指标
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        # 输出区域
        out_frame = ttk.Frame(notebook)
        self.txt = tk.Text(out_frame, height=18, wrap=tk.WORD)
        self.txt.pack(fill=tk.BOTH, expand=True)
        # 为错误输出配置红色样式
        try:
            self.txt.tag_configure("stderr", foreground="red")
        except Exception:
            pass
        notebook.add(out_frame, text=self._t("Output", "输出"))

        # 角色输出（按角色分类展示）
        roles_frame = ttk.Frame(notebook)
        self._build_roles_tab(roles_frame)
        notebook.add(roles_frame, text=self._t("Roles Output", "角色输出"))
        # 保存引用，便于自动切换与语言切换
        self.roles_frame = roles_frame
        self._has_shown_roles = False

        # 进度树
        progress_frame = ttk.Frame(notebook)
        self.tree = ttk.Treeview(
            progress_frame,
            columns=("status", "progress", "eta"),
            show="tree headings",
            height=14,
        )
        self.tree.heading("status", text=self._t("Status", "状态"))
        self.tree.column("status", width=140, anchor="center")
        self.tree.heading("progress", text=self._t("Progress", "进度"))
        self.tree.column("progress", width=120, anchor="center")
        self.tree.heading("eta", text=self._t("ETA", "预估剩余"))
        self.tree.column("eta", width=140, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True)
        notebook.add(progress_frame, text=self._t("Progress", "进度"))

        # 指标卡
        metrics_frame = ttk.Frame(notebook, padding=8)
        self.lbl_elapsed = ttk.Label(metrics_frame, text=self._t("Elapsed: 0.0s", "耗时: 0.0s"))
        self.lbl_messages = ttk.Label(metrics_frame, text=self._t("LLM Messages: 0", "LLM消息: 0"))
        self.lbl_reports = ttk.Label(metrics_frame, text=self._t("Reports: 0", "生成报告: 0"))
        self.lbl_decision = ttk.Label(metrics_frame, text=self._t("Final Decision: -", "最终决策: -"))
        for w in (self.lbl_elapsed, self.lbl_messages, self.lbl_reports, self.lbl_decision):
            w.pack(anchor="w", pady=4)
        notebook.add(metrics_frame, text=self._t("Metrics", "指标"))
        # 保存标签页引用以便语言切换时更新
        self.notebook = notebook
        self.out_frame = out_frame
        self.progress_frame = progress_frame
        self.metrics_frame = metrics_frame

        self._worker: threading.Thread | None = None
        self._init_progress_tree()
        self._metrics: Dict[str, Any] = {"start": None, "msg": 0, "reports": 0}
        # 进度跟踪
        self._node_progress: Dict[str, Dict[str, Any]] = {}
        self._selected_analysts_set: set[str] = set()
        # 股票列表缓存
        self._tickers_all: list[str] = []
        self._ticker_pairs: list[tuple[str, str]] = []  # (symbol, description)
        self.after(300, lambda: self.refresh_tickers(async_mode=True))
        self._models_cache: Dict[str, List[str]] = {}
        # 首次异步刷新模型列表
        self.after(200, lambda: self.refresh_models(async_mode=True))

        # 让网格在窗口大小变化时自适应
        for i in range(4):
            form.grid_columnconfigure(i, weight=1)
        # 初始缩放（可选 1.0/1.25/1.5 ...）
        self.set_scale(1.25)

        # 将控制台输出重定向到前端文本框
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._stdout_writer = _GuiTextWriter(self, is_error=False)
        self._stderr_writer = _GuiTextWriter(self, is_error=True)
        sys.stdout = self._stdout_writer
        sys.stderr = self._stderr_writer

        # 关闭时恢复控制台
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _ensure_cert_bundle(self) -> None:
        """修复打包环境下部分库（requests/curl_cffi）找不到 CA 证书导致的 SSL 错误。
        将 certifi 的 cacert.pem 拷贝到仅含 ASCII 的公共目录，并设置相关环境变量。
        优先顺序：C:\\ProgramData → C:\\Users\\Public → 回退为源路径。
        """
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
            # 过滤出 ASCII 路径
            candidates = [p for p in candidates if is_ascii_path(p)]

            ca_dst = ca_src
            for base in candidates:
                try:
                    os.makedirs(base, exist_ok=True)
                    dst = os.path.join(base, "cacert.pem")
                    # 仅在大小不同或不存在时复制
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

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        view = tk.Menu(menubar, tearoff=0)
        for label, factor in [("100%", 1.0), ("125%", 1.25), ("150%", 1.5), ("175%", 1.75), ("200%", 2.0)]:
            view.add_radiobutton(label=label, command=lambda f=factor: self.set_scale(f))
        menubar.add_cascade(label=self._t("View", "视图"), menu=view)
        # 语言菜单
        lang_menu = tk.Menu(menubar, tearoff=0)
        lang_menu.add_radiobutton(label="English", variable=self._var_lang, value="en", command=lambda: self._on_switch_lang("en"))
        lang_menu.add_radiobutton(label="简体中文", variable=self._var_lang, value="zh", command=lambda: self._on_switch_lang("zh"))
        menubar.add_cascade(label=self._t("Language", "语言"), menu=lang_menu)
        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=self._t("Parameters Help", "参数说明"), command=self._show_params_help)
        menubar.add_cascade(label=self._t("Help", "帮助"), menu=help_menu)
        self.config(menu=menubar)
        self._menubar = menubar

    def _snapshot_base_fonts(self) -> Dict[str, int]:
        names = [
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ]
        base: Dict[str, int] = {}
        for n in names:
            try:
                f = tkfont.nametofont(n)
                base[n] = f.cget("size")
            except Exception:
                pass
        return base

    def set_scale(self, factor: float) -> None:
        # 缩放 Tk 渲染比例
        try:
            self.tk.call("tk", "scaling", factor)
        except Exception:
            pass
        # 按比例调整常用字体
        for name, base_size in self._base_fonts.items():
            try:
                f = tkfont.nametofont(name)
                f.configure(size=max(8, int(round(base_size * factor))))
            except Exception:
                continue
        # 调整 ttk 组件样式以匹配缩放，尤其是 Treeview 的行高与表头字体
        try:
            style = getattr(self, "_style", None)
            if style is None:
                style = ttk.Style(self)
                self._style = style
            default_font = tkfont.nametofont("TkTextFont")
            heading_font = tkfont.nametofont("TkHeadingFont")
            # Tk 字体 size 可能为负值（像素高度），统一按绝对值计算像素量级
            item_px = abs(int(default_font.cget("size")))
            head_px = abs(int(heading_font.cget("size")))
            # 行高按字体大小取 1.8 倍，避免文字拥挤或被裁剪
            row_height = max(22, int(round(item_px * 1.8)))
            style.configure("Treeview", rowheight=row_height)
            # 表头字体略大于正文，保证对比清晰
            style.configure("Treeview.Heading", font=(heading_font.cget("family"), max(head_px, item_px + 1)))
            # 标签页适度增大内边距
            style.configure("TNotebook.Tab", padding=(12, max(4, int(round(item_px * 0.35)))))
        except Exception:
            pass
        # 触发布局更新
        self.update_idletasks()

    def _center_window(self) -> None:
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = max(0, int((sw - w) / 2))
        y = max(0, int((sh - h) / 2))
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _init_dpi_awareness(self) -> None:
        # 尝试让进程对高 DPI 友好（Windows）
        try:
            from ctypes import windll
            try:
                windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
            except Exception:
                windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    def _init_progress_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.nodes: Dict[str, str] = {}
        # 顶层
        self.nodes["Analyst"] = self.tree.insert(
            "", tk.END, text=self._t("Analyst Team", "分析师团队"), values=("pending", "0%", "—")
        )
        self.nodes["Research"] = self.tree.insert(
            "", tk.END, text=self._t("Research Team", "研究团队"), values=("pending", "0%", "—")
        )
        self.nodes["Trading"] = self.tree.insert(
            "", tk.END, text=self._t("Trading Team", "交易团队"), values=("pending", "0%", "—")
        )
        self.nodes["Risk"] = self.tree.insert(
            "", tk.END, text=self._t("Risk Management", "风控团队"), values=("pending", "0%", "—")
        )
        self.nodes["PM"] = self.tree.insert(
            "", tk.END, text=self._t("Portfolio Manager", "组合经理"), values=("pending", "0%", "—")
        )
        # Analyst 子节点
        for a_en, a_zh in (("Market Analyst", "市场分析师"), ("Social Analyst", "社媒分析师"), ("News Analyst", "新闻分析师"), ("Fundamentals Analyst", "基本面分析师")):
            a_text = self._t(a_en, a_zh)
            self.nodes[a_en] = self.tree.insert(
                self.nodes["Analyst"], tk.END, text=a_text, values=("pending", "0%", "—")
            )
        # 初始化/重置进度结构
        self._node_progress = {
            "Market Analyst": {"total": 1, "done": 0, "start": None},
            "Social Analyst": {"total": 1, "done": 0, "start": None},
            "News Analyst": {"total": 1, "done": 0, "start": None},
            "Fundamentals Analyst": {"total": 1, "done": 0, "start": None},
            "Analyst": {"total": 4, "done": 0, "start": None},
            # 研究团队：牛熊+判官 3 个阶段，按轮数估算
            "Research": {"total": max(1, self.var_depth.get() * 3), "done": 0, "start": None},
            # 交易一次性
            "Trading": {"total": 1, "done": 0, "start": None},
            # 风控团队：三位分析师 + PM 判决，按轮数估算（3位各1步/轮 + 1 判决/轮）
            "Risk": {"total": max(1, self.var_depth.get() * 4), "done": 0, "start": None},
            # PM 最终一次性
            "PM": {"total": 1, "done": 0, "start": None},
        }

    def _reset_progress_tree(self) -> None:
        self._init_progress_tree()

    def _set_status(self, key: str, status: str) -> None:
        node = self.nodes.get(key)
        if node:
            self.tree.set(node, "status", status)
            if status == "running":
                info = self._node_progress.get(key)
                if info is not None and info.get("start") is None:
                    info["start"] = time.time()

    def _setup_node_progress(self, analysts: list[str]) -> None:
        # 根据用户选择的分析师调整总量
        selected = {ANALYST_DISPLAY_NAMES.get(a, a) for a in analysts}
        # 分别初始化分析师子节点
        for short, display in ANALYST_DISPLAY_NAMES.items():
            if display in self._node_progress:
                self._node_progress[display]["done"] = 0
                self._node_progress[display]["start"] = None
                # 未选择的分析师视为 total=0，使其在总体计算中不占比
                self._node_progress[display]["total"] = 1 if display in selected else 0
        # Analyst 顶层 total = 已选分析师数量
        self._node_progress["Analyst"]["done"] = 0
        self._node_progress["Analyst"]["start"] = None
        self._node_progress["Analyst"]["total"] = len(selected)
        # 其余节点复位进度与起始时间
        for k in ("Research", "Trading", "Risk", "PM"):
            if k in self._node_progress:
                self._node_progress[k]["done"] = 0
                self._node_progress[k]["start"] = None

        # 刷新树显示
        for key in self._node_progress.keys():
            self._update_node_row(key)

    def _format_eta(self, seconds: float | None) -> str:
        if not seconds or seconds <= 0:
            return "—"
        # 限制最大显示为 99:59:59
        sec = int(seconds)
        h = min(sec // 3600, 99)
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _update_node_row(self, key: str) -> None:
        node = self.nodes.get(key)
        info = self._node_progress.get(key)
        if not node or not info:
            return
        total = max(0, int(info.get("total", 0)))
        done = max(0, int(info.get("done", 0)))
        done = min(done, total) if total > 0 else done
        progress_pct = 0 if total == 0 else int(round(100 * done / total))
        # ETA 估算：基于平均每步耗时 * 剩余步数
        eta = "—"
        start = info.get("start")
        if start and total > 0 and done > 0 and done < total:
            elapsed = time.time() - float(start)
            avg_per_step = elapsed / float(done)
            remaining = (total - done) * avg_per_step
            eta = self._format_eta(remaining)
        self.tree.set(node, "progress", f"{progress_pct}%")
        self.tree.set(node, "eta", eta)

    def _bump(self, key: str, inc: int = 1) -> None:
        if key not in self._node_progress:
            return
        info = self._node_progress[key]
        info["done"] = max(0, int(info.get("done", 0))) + inc
        # 首次进度更新时标记开始时间（若未标记）
        if info.get("start") is None:
            info["start"] = time.time()
        self._update_node_row(key)

    def _update_progress_numbers(self, chunk: Dict[str, Any], analyst_done_delta: int) -> None:
        # 分析师子节点已在 _update_progress_from_chunk 标记；这里根据 delta 汇总至 Analyst 顶层
        if analyst_done_delta:
            self._bump("Analyst", analyst_done_delta)
        # Research：根据 investment_debate_state 的 history 长度估算
        inv = chunk.get("investment_debate_state") or {}
        if isinstance(inv, dict):
            hist = inv.get("history")
            if isinstance(hist, str):
                # 简单按换行统计步数
                steps = max(0, len([l for l in hist.splitlines() if l.strip()]))
                # 将 done 对齐到 steps，但不超过 total
                info = self._node_progress.get("Research")
                if info:
                    prev_done = int(info.get("done", 0))
                    if steps > prev_done:
                        inc = min(steps - prev_done, max(0, info.get("total", 0) - prev_done))
                        if inc > 0:
                            self._bump("Research", inc)
        # Trading：一旦出现 trader_investment_plan 即完成
        if chunk.get("trader_investment_plan"):
            info = self._node_progress.get("Trading")
            if info and int(info.get("done", 0)) < int(info.get("total", 0)):
                self._bump("Trading", 1)
        # Risk：根据 risk_debate_state 的 history 或当前响应粗略估算
        risk = chunk.get("risk_debate_state") or {}
        if isinstance(risk, dict):
            hist = risk.get("history")
            step_count = 0
            if isinstance(hist, str):
                step_count = max(0, len([l for l in hist.splitlines() if l.strip()]))
            else:
                # 回退：如果没有历史，按当前三位响应非空计步
                for k in ("current_risky_response", "current_safe_response", "current_neutral_response", "judge_decision"):
                    if risk.get(k):
                        step_count += 1
            info = self._node_progress.get("Risk")
            if info:
                prev_done = int(info.get("done", 0))
                if step_count > prev_done:
                    inc = min(step_count - prev_done, max(0, info.get("total", 0) - prev_done))
                    if inc > 0:
                        self._bump("Risk", inc)
        # PM：一旦出现最终决策即完成
        if chunk.get("final_trade_decision"):
            info = self._node_progress.get("PM")
            if info and int(info.get("done", 0)) < int(info.get("total", 0)):
                self._bump("PM", 1)

    def _update_metrics(self) -> None:
        elapsed = 0.0
        if self._metrics.get("start"):
            elapsed = time.time() - self._metrics["start"]
        self.lbl_elapsed.config(text=f"耗时: {elapsed:.1f}s")
        self.lbl_messages.config(text=f"LLM消息: {self._metrics.get('msg',0)}")
        self.lbl_reports.config(text=f"生成报告: {self._metrics.get('reports',0)}")

    def _update_progress_from_chunk(self, chunk: Dict[str, Any]) -> None:
        # 根据 chunk 中的键更新树与指标
        updated = False
        # 维护分析师完成数
        analyst_done_delta = 0
        if chunk.get("market_report"):
            self._set_status("Market Analyst", "done"); updated = True; self._metrics["reports"] += 1; analyst_done_delta += 1
        if chunk.get("sentiment_report"):
            self._set_status("Social Analyst", "done"); updated = True; self._metrics["reports"] += 1; analyst_done_delta += 1
        if chunk.get("news_report"):
            self._set_status("News Analyst", "done"); updated = True; self._metrics["reports"] += 1; analyst_done_delta += 1
        if chunk.get("fundamentals_report"):
            self._set_status("Fundamentals Analyst", "done"); updated = True; self._metrics["reports"] += 1; analyst_done_delta += 1
        if chunk.get("investment_debate_state"):
            self._set_status("Research", "running"); updated = True
        if chunk.get("trader_investment_plan"):
            self._set_status("Trading", "done"); updated = True; self._metrics["reports"] += 1
        if chunk.get("risk_debate_state"):
            self._set_status("Risk", "running"); updated = True
        if chunk.get("final_trade_decision"):
            self._set_status("PM", "done"); updated = True
        if updated:
            self._update_metrics()
        # 更新进度条与 ETA
        self._update_progress_numbers(chunk, analyst_done_delta)

    # ---------- 语言辅助与切换 ----------
    def _t(self, en: str, zh: str) -> str:
        return zh if self.ui_lang == "zh" else en

    def _on_switch_lang(self, lang: str) -> None:
        try:
            self.ui_lang = lang.lower()
            os.environ["TRADINGAGENTS_LANG"] = self.ui_lang
        except Exception:
            pass
        # 更新所有可见文本
        try:
            self.title(self._t("TradingAgents - GUI Launcher", "TradingAgents - 可视化启动器"))
            self.lbl_ticker.config(text=self._t("Ticker (US):", "标的 (美股):"))
            self.btn_refresh_tickers.config(text=self._t("Refresh", "刷新股票"))
            self.lbl_date.config(text=self._t("Date (YYYY-MM-DD):", "日期 (YYYY-MM-DD):"))
            self.lbl_depth.config(text=self._t("Research Depth:", "研究深度:"))
            self.rb_depth_shallow.config(text=self._t("Shallow (1)", "浅(1)"))
            self.rb_depth_medium.config(text=self._t("Medium (3)", "中(3)"))
            self.rb_depth_deep.config(text=self._t("Deep (5)", "深(5)"))
            self.lbl_analysts.config(text=self._t("Analysts:", "分析师:"))
            self.chk_market.config(text=self._t("Market", "市场"))
            self.chk_social.config(text=self._t("Social", "社交"))
            self.chk_news.config(text=self._t("News", "新闻"))
            self.chk_fund.config(text=self._t("Fundamentals", "基本面"))
            self.lbl_mode.config(text=self._t("Mode:", "模式:"))
            self.rb_mode_backtest.config(text=self._t("Backtest", "回测"))
            self.rb_mode_paper.config(text=self._t("Paper", "纸上"))
            self.rb_mode_live.config(text=self._t("Live", "实盘"))
            self.lbl_provider.config(text=self._t("LLM Provider:", "LLM提供商:"))
            self.lbl_quick_model.config(text=self._t("Quick Model:", "快速模型:"))
            self.lbl_deep_model.config(text=self._t("Deep Model:", "深度模型:"))
            self.btn_refresh_models.config(text=self._t("Refresh Models", "刷新模型"))
            self.btn_start.config(text=self._t("Start", "开始分析"))
            self.btn_quick_template.config(text=self._t("Quick Template (SPY/Today)", "一键模板(SPY/今日)"))
            self.btn_save_preset.config(text=self._t("Save Preset", "保存预设"))
            self.btn_load_preset.config(text=self._t("Load Preset", "加载预设"))
            # 选项卡标题
            idx_out = self.notebook.index(self.out_frame)
            self.notebook.tab(idx_out, text=self._t("Output", "输出"))
            idx_prog = self.notebook.index(self.progress_frame)
            self.notebook.tab(idx_prog, text=self._t("Progress", "进度"))
            idx_metric = self.notebook.index(self.metrics_frame)
            self.notebook.tab(idx_metric, text=self._t("Metrics", "指标"))
            # 进度表头
            self.tree.heading("status", text=self._t("Status", "状态"))
            self.tree.heading("progress", text=self._t("Progress", "进度"))
            self.tree.heading("eta", text=self._t("ETA", "预估剩余"))
            # 重新初始化树的显示文本
            self._reset_progress_tree()
            # 指标标签
            self.lbl_elapsed.config(text=self._t("Elapsed: 0.0s", "耗时: 0.0s"))
            self.lbl_messages.config(text=self._t("LLM Messages: 0", "LLM消息: 0"))
            self.lbl_reports.config(text=self._t("Reports: 0", "生成报告: 0"))
            self.lbl_decision.config(text=self._t("Final Decision: -", "最终决策: -"))
            # 菜单栏重新构建以更新文案
            self._build_menu()
            # 选项映射/下拉选项切换语言
            self._init_option_mappings(preserve_selection=True)
        except Exception:
            pass

    # ---------- 参数说明 ----------
    def _show_params_help(self) -> None:
        try:
            zh = (
                "窗口策略: 自适应(基于基金风格与持仓期自动确定抓取窗口) / 固定(新闻/社媒7天, 技术30天, 基本面30天)。\n"
                "基金风格: 高换手(数日–2周) / 中等换手(2–8周) / 低换手(1–6个月)。\n"
                "持仓期(交易日): 你的目标持仓时长，用于计算0.5×H~0.9×H的窗口范围。\n"
                "三层窗口聚合: 同时抓取快/中/慢三段(默认3/14/60天)并并列展示，便于对比与投票。\n"
                "波动自适应: 预留开关（未来用于随波动伸缩窗口）。\n"
                "新闻/社媒日上限: 每日抓取的最大条数(默认5)。\n"
            )
            en = (
                "Windowing: Adaptive (auto windows by fund style & holding) / Fixed (news/social 7d, technical 30d, fundamentals 30d).\n"
                "Fund Style: High/Medium/Low turnover.\n"
                "Hold Days: Target holding period in trading days for window selection.\n"
                "Multi-layer Windows: Fetch FAST/MID/SLOW windows in parallel (default 3/14/60d).\n"
                "Volatility Scaling: Reserved toggle for window stretch/shrink by volatility.\n"
                "News/SM Max/day: Max items per day (default 5).\n"
            )
            message = zh if self.ui_lang == "zh" else en
            messagebox.showinfo(self._t("Parameters Help", "参数说明"), message)
        except Exception:
            pass

    # ---------- 选项映射与本地化 ----------
    def _init_option_mappings(self, preserve_selection: bool = False) -> None:
        # 读取当前选择（用于切换语言时保留）
        curr_win_code = None
        curr_style_code = None
        try:
            if preserve_selection and hasattr(self, "_win_label_to_code"):
                curr_win_code = self._win_label_to_code.get(self.var_windowing.get().strip())
            if preserve_selection and hasattr(self, "_style_label_to_code"):
                curr_style_code = self._style_label_to_code.get(self.var_style.get().strip())
        except Exception:
            pass

        self._win_code_to_label = {
            "adaptive": self._t("adaptive", "自适应"),
            "fixed": self._t("fixed", "固定"),
        }
        self._win_label_to_code = {v: k for k, v in self._win_code_to_label.items()}

        self._style_code_to_label = {
            "high_turnover": self._t("high_turnover", "高换手"),
            "medium_turnover": self._t("medium_turnover", "中等换手"),
            "low_turnover": self._t("low_turnover", "低换手"),
        }
        self._style_label_to_code = {v: k for k, v in self._style_code_to_label.items()}

        # 重建下拉 options（按当前语言）
        try:
            # 窗口策略
            win_vals = list(self._win_code_to_label.values())
            # 使用当前配置代码决定默认
            win_code = curr_win_code or str(DEFAULT_CONFIG.get("windowing_mode", "adaptive"))
            self.nametowidget(self.children['!frame']).children['!combobox'].configure(values=win_vals)
            self.var_windowing.set(self._win_code_to_label.get(win_code, win_vals[0]))
        except Exception:
            pass

        try:
            # 基金风格
            style_vals = list(self._style_code_to_label.values())
            style_code = curr_style_code or str(DEFAULT_CONFIG.get("fund_style", "medium_turnover"))
            self.cmb_style.configure(values=style_vals)
            self.var_style.set(self._style_code_to_label.get(style_code, style_vals[1] if len(style_vals) > 1 else style_vals[0]))
        except Exception:
            pass

    def _build_roles_tab(self, parent: ttk.Frame) -> None:
        # 各角色标签页
        self.role_tabs = ttk.Notebook(parent)
        self.role_tabs.pack(fill=tk.BOTH, expand=True)

        self._role_texts: Dict[str, tk.Text] = {}

        # 固定的英文角色键，用于路由；标签展示根据语言切换
        roles: list[tuple[str, str]] = [
            ("Market Analyst", "市场分析师"),
            ("Social Analyst", "社媒分析师"),
            ("News Analyst", "新闻分析师"),
            ("Fundamentals Analyst", "基本面分析师"),
            ("Bull Researcher", "多头研究员"),
            ("Bear Researcher", "空头研究员"),
            ("Research Manager", "研究经理"),
            ("Trader", "交易员"),
            ("Risky Analyst", "激进风控分析师"),
            ("Neutral Analyst", "中性风控分析师"),
            ("Safe Analyst", "保守风控分析师"),
            ("Portfolio Manager", "组合经理"),
        ]

        for role_en, role_zh in roles:
            frame = ttk.Frame(self.role_tabs)
            # 带滚动条的文本框
            scr = ttk.Scrollbar(frame, orient=tk.VERTICAL)
            txt = tk.Text(frame, wrap=tk.WORD, yscrollcommand=scr.set)
            scr.config(command=txt.yview)
            txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scr.pack(side=tk.RIGHT, fill=tk.Y)
            self.role_tabs.add(frame, text=self._t(role_en, role_zh))
            # 用英文键保存映射，便于路由
            self._role_texts[role_en] = txt

    def _set_role_text(self, role: str, content: str) -> None:
        try:
            widget = self._role_texts.get(role)
            if not widget:
                return
            widget.delete("1.0", tk.END)
            widget.insert(tk.END, str(content))
            widget.see(tk.END)
        except Exception:
            pass

    def _append_role_text(self, role: str, content: str) -> None:
        try:
            widget = self._role_texts.get(role)
            if not widget:
                return
            widget.insert(tk.END, str(content) + "\n")
            widget.see(tk.END)
        except Exception:
            pass

    def _update_role_outputs(self, chunk: Dict[str, Any]) -> None:
        updated_any = False
        # 分析师四份报告（整段替换）
        if chunk.get("market_report"):
            self._set_role_text("Market Analyst", chunk.get("market_report", ""))
            updated_any = True
        if chunk.get("sentiment_report"):
            self._set_role_text("Social Analyst", chunk.get("sentiment_report", ""))
            updated_any = True
        if chunk.get("news_report"):
            self._set_role_text("News Analyst", chunk.get("news_report", ""))
            updated_any = True
        if chunk.get("fundamentals_report"):
            self._set_role_text("Fundamentals Analyst", chunk.get("fundamentals_report", ""))
            updated_any = True

        # 研究团队：牛/熊历史与经理判决/方案
        inv = chunk.get("investment_debate_state")
        if isinstance(inv, dict):
            if inv.get("bull_history"):
                self._set_role_text("Bull Researcher", inv.get("bull_history", ""))
                updated_any = True
            if inv.get("bear_history"):
                self._set_role_text("Bear Researcher", inv.get("bear_history", ""))
                updated_any = True
            # 研究经理可能写在 judge_decision
            if inv.get("judge_decision"):
                self._set_role_text("Research Manager", inv.get("judge_decision", ""))
                updated_any = True
        if chunk.get("investment_plan"):
            self._set_role_text("Research Manager", chunk.get("investment_plan", ""))
            updated_any = True

        # 交易员方案
        if chunk.get("trader_investment_plan"):
            self._set_role_text("Trader", chunk.get("trader_investment_plan", ""))
            updated_any = True

        # 风控团队：三位历史与 PM/裁决
        risk = chunk.get("risk_debate_state")
        if isinstance(risk, dict):
            if risk.get("risky_history"):
                self._set_role_text("Risky Analyst", risk.get("risky_history", ""))
                updated_any = True
            if risk.get("neutral_history"):
                self._set_role_text("Neutral Analyst", risk.get("neutral_history", ""))
                updated_any = True
            if risk.get("safe_history"):
                self._set_role_text("Safe Analyst", risk.get("safe_history", ""))
                updated_any = True
            if risk.get("judge_decision"):
                self._set_role_text("Portfolio Manager", risk.get("judge_decision", ""))
                updated_any = True

        if chunk.get("final_trade_decision"):
            self._set_role_text("Portfolio Manager", chunk.get("final_trade_decision", ""))
            updated_any = True

        # 首次有角色输出时，自动切换到“角色输出”标签页，避免用户漏看
        try:
            if updated_any and not self._has_shown_roles:
                self._has_shown_roles = True
                if hasattr(self, "notebook") and hasattr(self, "roles_frame"):
                    self.notebook.select(self.roles_frame)
        except Exception:
            pass

    def append(self, text: str) -> None:
        self.txt.insert(tk.END, text + "\n")
        self.txt.see(tk.END)

    def _append_from_io(self, text: str, is_error: bool = False) -> None:
        tag = "stderr" if is_error else None
        try:
            if tag:
                self.txt.insert(tk.END, text, tag)
            else:
                self.txt.insert(tk.END, text)
            self.txt.see(tk.END)
        except Exception:
            # 回退：若 GUI 已关闭或控件不可用，打印回原控制台
            try:
                (self._orig_stderr if is_error else self._orig_stdout).write(text)
            except Exception:
                pass

    def _on_close(self) -> None:
        try:
            sys.stdout = self._orig_stdout
            sys.stderr = self._orig_stderr
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def quick_template(self) -> None:
        self.var_ticker.set("SPY")
        self.var_date.set(datetime.datetime.now().strftime("%Y-%m-%d"))
        self.var_depth.set(1)
        self.var_mode.set("backtest")
        self.append("已应用模板：SPY / 今日 / 深度1 / 回测")

    def current_preset(self) -> Dict[str, Any]:
        return {
            "ticker": self.var_ticker.get(),
            "date": self.var_date.get(),
            "depth": self.var_depth.get(),
            "analysts": {
                "market": self.var_market.get(),
                "social": self.var_social.get(),
                "news": self.var_news.get(),
                "fundamentals": self.var_fund.get(),
            },
            "provider": self.var_provider.get(),
            "quick": self.var_quick.get(),
            "deep": self.var_deep.get(),
            "mode": self.var_mode.get(),
        }

    def apply_preset(self, data: Dict[str, Any]) -> None:
        try:
            self.var_ticker.set(data.get("ticker", self.var_ticker.get()))
            self.var_date.set(data.get("date", self.var_date.get()))
            self.var_depth.set(int(data.get("depth", self.var_depth.get())))
            a = data.get("analysts", {})
            self.var_market.set(bool(a.get("market", self.var_market.get())))
            self.var_social.set(bool(a.get("social", self.var_social.get())))
            self.var_news.set(bool(a.get("news", self.var_news.get())))
            self.var_fund.set(bool(a.get("fundamentals", self.var_fund.get())))
            self.var_provider.set(data.get("provider", self.var_provider.get()))
            self.var_quick.set(data.get("quick", self.var_quick.get()))
            self.var_deep.set(data.get("deep", self.var_deep.get()))
            self.var_mode.set(data.get("mode", self.var_mode.get()))
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("错误", f"预设应用失败: {e}")

    def save_preset(self) -> None:
        try:
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.current_preset(), f, ensure_ascii=False, indent=2)
            self.append(f"已保存预设: {path}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("错误", f"保存预设失败: {e}")

    def load_preset(self) -> None:
        try:
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if not path:
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.apply_preset(data)
            self.append(f"已加载预设: {path}")
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("错误", f"加载预设失败: {e}")

    def on_start(self) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("提示", "任务仍在进行中，请稍候…")
            return

        # 解析下拉选择，支持 "AAPL - Apple Inc" 或手动输入
        ticker_raw = self.var_ticker.get().strip()
        ticker = ticker_raw.split("-")[0].strip().split(" ")[0].strip().upper()
        date_str = self.var_date.get().strip()
        if not ticker:
            messagebox.showerror("错误", "请填写标的代码")
            return
        try:
            datetime.datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("错误", "日期格式需为 YYYY-MM-DD")
            return

        analysts: list[str] = []
        if self.var_market.get():
            analysts.append("market")
        if self.var_social.get():
            analysts.append("social")
        if self.var_news.get():
            analysts.append("news")
        if self.var_fund.get():
            analysts.append("fundamentals")
        if not analysts:
            messagebox.showerror("错误", "至少选择一位分析师")
            return

        provider_name = self.var_provider.get()
        backend_url = self.providers.get(provider_name, "https://generativelanguage.googleapis.com/v1")
        quick_model = self.var_quick.get().strip() or "gemini-2.0-flash"
        deep_model = self.var_deep.get().strip() or quick_model

        config = DEFAULT_CONFIG.copy()
        # 强制在线模式：始终在线，不自动降级
        config["force_online"] = True
        config["online_tools"] = True
        config["max_debate_rounds"] = self.var_depth.get()
        config["max_risk_discuss_rounds"] = self.var_depth.get()
        config["quick_think_llm"] = quick_model
        config["deep_think_llm"] = deep_model
        config["backend_url"] = backend_url
        config["llm_provider"] = provider_name.lower()
        # 显式启用在线数据端口
        config["online_tools"] = True
        # 覆盖窗口配置（让用户界面可控并作用于当前会话）
        try:
            config["windowing_mode"] = self.var_windowing.get().strip().lower()
            config["fund_style"] = self.var_style.get().strip().lower()
            config["hold_period_days"] = int(self.var_hold.get())
            config["use_multi_layer_window"] = bool(self.var_multi.get())
            config["volatility_scale_windows"] = bool(self.var_volscale.get())
            config["news_max_per_day"] = int(self.var_newsmax.get())
        except Exception:
            pass
        # 确保 GUI 总是使用最新的 OpenAI Responses 行为（当提供商为 OpenAI 时默认开启，也可用环境变量覆盖）
        # 先计算 provider_lower，供后续判断
        provider_lower = provider_name.lower()
        try:
            use_resp_env = os.getenv("TRADINGAGENTS_USE_OAI_RESPONSES")
            if use_resp_env is not None:
                config["use_openai_responses"] = use_resp_env.lower() == "true"
            else:
                config["use_openai_responses"] = (provider_lower == "openai")
            # 默认开启流式消费，自动等待工具完成
            config["oai_responses_streaming"] = True
        except Exception:
            pass

        # 确保所需 API Key 存在；若缺失则弹窗要求输入
        required_env: str | None = None
        if provider_lower == "google":
            required_env = "GOOGLE_API_KEY"
        elif provider_lower == "openai":
            required_env = "OPENAI_API_KEY"
        elif provider_lower == "anthropic":
            required_env = "ANTHROPIC_API_KEY"
        elif provider_lower == "openrouter":
            required_env = "OPENROUTER_API_KEY"
        elif provider_lower == "ollama":
            required_env = None  # 本地模型，无需云端 Key

        # 兼容 OpenRouter/Ollama：这些不使用 OPENAI_API_KEY，但如后端走 OpenAI SDK，仍需基于 base_url 的 key 名
        if required_env and not os.getenv(required_env):
            key_val = simpledialog.askstring(
                "缺少凭证",
                f"未检测到 {required_env}\n请输入你的 {provider_name} API Key：",
                parent=self,
            )
            if not key_val:
                messagebox.showerror("错误", f"未提供 {required_env}，已取消")
                return
            os.environ[required_env] = key_val

        # 如果选择 openrouter，将其 key 复制到 OPENAI_API_KEY 以兼容 OpenAI SDK（仅当未显式设置时）
        if provider_lower == "openrouter" and os.getenv("OPENAI_API_KEY") is None:
            key = os.getenv("OPENROUTER_API_KEY")
            if key:
                os.environ["OPENAI_API_KEY"] = key

        # 可选：行情数据 Key，缺失仅提示
        if config.get("online_tools") and not os.getenv("FINNHUB_API_KEY"):
            self.append("提示：未设置 FINNHUB_API_KEY，部分实时数据功能可能不可用。")

        self.btn_start.config(state=tk.DISABLED)
        self.txt.delete("1.0", tk.END)
        self.append(f"开始分析: {ticker} @ {date_str}")
        self.append(f"Provider: {provider_name}  URL: {backend_url}")
        self.append(f"Analysts: {', '.join(analysts)}  Depth: {self.var_depth.get()}  模式: {self.var_mode.get()}")
        self.append("正在推理，请稍候…")
        self._reset_progress_tree()
        self._metrics = {"start": time.time(), "msg": 0, "reports": 0}
        self._update_metrics()
        # 初始化每个节点的预计总步数与初始状态
        self._selected_analysts_set = set(analysts)
        self._setup_node_progress(analysts)

        def task() -> None:
            try:
                graph = TradingAgentsGraph(analysts, config=config, debug=True)
                # 直接流式消费，驱动进度树与日志
                init_state = graph.propagator.create_initial_state(ticker, date_str)
                args = graph.propagator.get_graph_args()
                trace = []
                # 为避免临时网络波动导致直接失败，加入轻量级重试
                def _stream_once():
                    for chunk in graph.graph.stream(init_state, **args):
                        yield chunk

                attempts = 0
                offline_fallback_used = False
                while True:
                    try:
                        for chunk in _stream_once():
                            trace.append(chunk)
                            # LLM 消息
                            if len(chunk.get("messages", [])) > 0:
                                last_msg = chunk["messages"][-1]
                                # 优先捕获 pretty_print 的完整格式化输出
                                pretty_out = ""
                                try:
                                    buf = io.StringIO()
                                    with redirect_stdout(buf):
                                        try:
                                            last_msg.pretty_print()
                                        except Exception:
                                            pass
                                    pretty_out = buf.getvalue().strip()
                                except Exception:
                                    pretty_out = ""

                                content = getattr(last_msg, "content", str(last_msg))
                                if isinstance(content, list):
                                    # Anthropic 形式
                                    text_parts = []
                                    for item in content:
                                        if isinstance(item, dict) and item.get("type") == "text":
                                            text_parts.append(item.get("text", ""))
                                    content = " ".join(text_parts)
                                self._metrics["msg"] += 1
                                # 有 pretty 则使用 pretty；否则退回 content
                                if pretty_out:
                                    self.after(0, lambda c=pretty_out: self.append(c))
                                else:
                                    self.after(0, lambda c=content: self.append(str(c)))
                                self.after(0, self._update_metrics)
                            # 进度树
                            self.after(0, lambda ch=chunk: self._update_progress_from_chunk(ch))
                            # 角色输出（若界面语言为中文，则翻译后再展示）
                            if self.ui_lang == "zh":
                                try:
                                    ch_zh = self._translate_chunk_for_roles(graph, chunk)
                                except Exception:
                                    ch_zh = chunk
                                self.after(0, lambda ch=ch_zh: self._update_role_outputs(ch))
                            else:
                                self.after(0, lambda ch=chunk: self._update_role_outputs(ch))
                        break
                    except Exception as e:  # noqa: BLE001
                        attempts += 1
                        if attempts <= 2:
                            self.after(0, lambda: self.append(f"网络波动，自动重试第 {attempts} 次…"))
                            time.sleep(1.5 * attempts)
                            continue
                        else:
                            # 强制在线时不降级离线，直接抛出错误
                            raise e
                # 完成
                final_state = trace[-1]
                decision = graph.process_signal(final_state["final_trade_decision"])
                # 已在流式过程中实时输出 pretty 文本，这里不再重复
                # 翻译为中文（使用已选 LLM，失败则保留英文）
                zh_decision = self._translate_to_zh(graph, decision)
                if zh_decision:
                    self.after(0, lambda: self.append(f"完成！决策(中文): {zh_decision}"))
                    self.after(0, lambda z=zh_decision: self.lbl_decision.config(text=f"最终决策: {z}"))
                    # 同时保留英文供参考
                    self.after(0, lambda: self.append(f"Decision (EN): {decision}"))
                else:
                    self.after(0, lambda: self.append(f"完成！决策: {decision}"))
                    self.after(0, lambda d=decision: self.lbl_decision.config(text=f"最终决策: {d}"))
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)
                self.after(0, lambda m=err_msg: self.append(f"发生错误: {m}"))
                self.after(0, lambda m=err_msg: messagebox.showerror("错误", m))
            finally:
                self.after(0, lambda: self.btn_start.config(state=tk.NORMAL))

        self._worker = threading.Thread(target=task, daemon=True)
        self._worker.start()

    def _translate_to_zh(self, graph, text: str) -> str:
        try:
            prompt = (
                "请将以下内容翻译为简体中文，保持金融术语准确，保留数字/单位/符号，不要添加解释：\n\n" + text
            )
            res = graph.quick_thinking_llm.invoke(prompt)
            content = getattr(res, "content", res)
            if isinstance(content, list):
                parts = []
                for it in content:
                    if isinstance(it, dict) and it.get("type") == "text":
                        parts.append(it.get("text", ""))
                content = "\n".join(parts)
            out = str(content).strip()
            return out
        except Exception:
            return ""

    def _translate_chunk_for_roles(self, graph, chunk: Dict[str, Any]) -> Dict[str, Any]:
        # 针对角色输出涉及的字段进行翻译，不改动其余结构
        zh_chunk = dict(chunk)
        def tr(text: str) -> str:
            if not isinstance(text, str) or not text.strip():
                return text
            if text in self._translation_cache:
                return self._translation_cache[text]
            out = self._translate_to_zh(graph, text)
            self._translation_cache[text] = out or text
            return self._translation_cache[text]

        # 分析师报告
        for k in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
            if zh_chunk.get(k):
                zh_chunk[k] = tr(zh_chunk[k])

        # 研究讨论与输出
        inv = zh_chunk.get("investment_debate_state")
        if isinstance(inv, dict):
            inv = dict(inv)
            for k in ("bull_history", "bear_history", "history", "current_response", "judge_decision"):
                if inv.get(k):
                    inv[k] = tr(inv[k])
            zh_chunk["investment_debate_state"] = inv
        if zh_chunk.get("investment_plan"):
            zh_chunk["investment_plan"] = tr(zh_chunk["investment_plan"])

        # 交易员方案
        if zh_chunk.get("trader_investment_plan"):
            zh_chunk["trader_investment_plan"] = tr(zh_chunk["trader_investment_plan"])

        # 风控讨论与最终决策
        risk = zh_chunk.get("risk_debate_state")
        if isinstance(risk, dict):
            risk = dict(risk)
            for k in ("risky_history", "neutral_history", "safe_history", "history", "judge_decision",
                      "current_risky_response", "current_safe_response", "current_neutral_response"):
                if risk.get(k):
                    risk[k] = tr(risk[k])
            zh_chunk["risk_debate_state"] = risk
        if zh_chunk.get("final_trade_decision"):
            zh_chunk["final_trade_decision"] = tr(zh_chunk["final_trade_decision"])

        return zh_chunk

    # ---------- 美股列表刷新与筛选 ----------
    def refresh_tickers(self, async_mode: bool = False) -> None:
        if async_mode:
            threading.Thread(target=self._refresh_tickers_worker, daemon=True).start()
        else:
            self._refresh_tickers_worker()

    def _refresh_tickers_worker(self) -> None:
        self._set_tickers_ui(busy=True)
        try:
            key = os.getenv("FINNHUB_API_KEY") or self._ask_key("FINNHUB_API_KEY", "Finnhub")
            if not key:
                return
            url = "https://finnhub.io/api/v1/stock/symbol"
            r = requests.get(url, params={"exchange": "US", "token": key}, timeout=30)
            r.raise_for_status()
            data = r.json() if isinstance(r.json(), list) else []
            # 仅保留常见美股（普通股/ADR/REIT 等），并去除无效代码
            pairs: list[tuple[str, str]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip()
                desc = str(item.get("description", "")).strip()
                t = str(item.get("type", "")).lower()
                if not sym or not sym.isascii():
                    continue
                if t in ("common stock", "adr", "reit", "etp") or t == "equity":
                    pairs.append((sym, desc))
            # 去重并排序
            pairs = sorted(list(dict.fromkeys(pairs)), key=lambda x: x[0])
            self._ticker_pairs = pairs
            self._tickers_all = [f"{s} - {d}" if d else s for s, d in pairs]
            self.after(0, lambda: self._apply_tickers_to_ui(self._tickers_all))
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.after(0, lambda t=ts: self.lbl_tickers_time.config(text=f"已更新 {t}（{len(self._tickers_all)} 只）"))
        except Exception as e:  # noqa: BLE001
            self.after(0, lambda: messagebox.showerror("错误", f"拉取美股列表失败: {e}"))
        finally:
            self._set_tickers_ui(busy=False)

    def _apply_tickers_to_ui(self, values: list[str]) -> None:
        try:
            self.cmb_ticker["values"] = values
        except Exception:
            pass

    def _on_ticker_typing(self, _event=None) -> None:
        text = self.var_ticker.get().strip().upper()
        if not self._tickers_all:
            return
        # 简单前缀或包含匹配
        candidates = [v for v in self._tickers_all if v.upper().startswith(text) or f" {text}" in v.upper()]
        # 限制数量，避免界面卡顿
        if len(candidates) > 100:
            candidates = candidates[:100]
        self._apply_tickers_to_ui(candidates)

    def _set_tickers_ui(self, busy: bool) -> None:
        try:
            self.btn_refresh_tickers.config(state=(tk.DISABLED if busy else tk.NORMAL))
            self.config(cursor="watch" if busy else "")
            self.update_idletasks()
        except Exception:
            pass

    # ---------- 模型列表刷新 ----------
    def refresh_models(self, async_mode: bool = False) -> None:
        provider = self.var_provider.get()
        if async_mode:
            threading.Thread(target=self._refresh_models_worker, args=(provider,), daemon=True).start()
        else:
            self._refresh_models_worker(provider)

    def _refresh_models_worker(self, provider: str) -> None:
        self._set_refresh_ui(busy=True)
        try:
            models = self._models_cache.get(provider)
            if not models:
                models = self._fetch_models(provider)
                self._models_cache[provider] = models
            self.after(0, lambda m=models: self._apply_models_to_ui(m))
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.after(0, lambda t=ts: self.lbl_refresh_time.config(text=f"已更新 {t}"))
        except Exception as e:  # noqa: BLE001
            self.after(0, lambda: messagebox.showerror("错误", f"拉取模型失败: {e}"))
        finally:
            self._set_refresh_ui(busy=False)

    def _set_refresh_ui(self, busy: bool) -> None:
        try:
            self.btn_refresh_models.config(state=(tk.DISABLED if busy else tk.NORMAL))
            self.config(cursor="watch" if busy else "")
            self.update_idletasks()
        except Exception:
            pass

    def _apply_models_to_ui(self, models: List[str]) -> None:
        if not models:
            return
        # 去重排序
        models = sorted(list(dict.fromkeys(models)), key=lambda x: (len(x), x))
        self.cmb_quick["values"] = models
        self.cmb_deep["values"] = models

    def _fetch_models(self, provider: str) -> List[str]:
        p = provider.lower()
        if p == "openai":
            key = os.getenv("OPENAI_API_KEY") or self._ask_key("OPENAI_API_KEY", provider)
            if not key:
                return []
            headers = {"Authorization": f"Bearer {key}"}
            r = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json().get("data", [])
            ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            # 仅保留常用文本/推理模型（启发式）
            ids = [i for i in ids if any(k in i for k in ("gpt", "o", "mini"))]
            return ids
        if p == "anthropic":
            key = os.getenv("ANTHROPIC_API_KEY") or self._ask_key("ANTHROPIC_API_KEY", provider)
            if not key:
                return []
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            r = requests.get("https://api.anthropic.com/v1/models", headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json().get("data", [])
            ids = [m.get("id") or m.get("name") for m in data if isinstance(m, dict)]
            return [i for i in ids if i]
        if p == "google":
            key = os.getenv("GOOGLE_API_KEY") or self._ask_key("GOOGLE_API_KEY", provider)
            if not key:
                return []
            r = requests.get("https://generativelanguage.googleapis.com/v1/models", params={"key": key}, timeout=20)
            r.raise_for_status()
            data = r.json().get("models", [])
            names = [m.get("name") for m in data if isinstance(m, dict) and m.get("name")]
            return [n for n in names if n]
        if p == "openrouter":
            key = os.getenv("OPENROUTER_API_KEY") or self._ask_key("OPENROUTER_API_KEY", provider)
            if not key:
                return []
            headers = {"Authorization": f"Bearer {key}"}
            r = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json().get("data", [])
            ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            return ids
        if p == "ollama":
            try:
                r = requests.get("http://localhost:11434/api/tags", timeout=5)
                r.raise_for_status()
                data = r.json().get("models", [])
                names = [m.get("name") for m in data if isinstance(m, dict) and m.get("name")]
                return names
            except Exception:
                return []
        return []

    def _ask_key(self, env_name: str, provider_name: str) -> str | None:
        key_val = simpledialog.askstring("缺少凭证", f"未检测到 {env_name}\n请输入你的 {provider_name} API Key：", parent=self)
        if key_val:
            os.environ[env_name] = key_val
        return key_val


def main() -> None:
    app = TradingAgentsGUI()
    app.mainloop()


if __name__ == "__main__":
    main()


