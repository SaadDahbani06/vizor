"""Design system for Main Street Risk Desk (Milestone 6.1): one simple, consistent style.

Tokens: warm off-white canvas, white cards with hairline borders and soft shadows, 16 px radii,
Inter for all text and figures, one brand colour (cobalt) and reserved
status colours (positive, negative, warning) that always come with words. The product palette
for charts is unchanged and passes ``validate_palette.js`` (lightness, chroma, colour-blind
separation, contrast) on white.

Components (HTML strings rendered with ``st.markdown(..., unsafe_allow_html=True)``): KPI cards,
delta pills, a range bar (downside / expected / upside), a before-and-after table, status chips,
info tooltips. Streamlit widgets are restyled through ``st-key-*`` container classes.
"""
from __future__ import annotations

import html

INK = "#111318"
PAPER = "#F6F6F4"
CARD = "#FFFFFF"
MUTED = "#5E6470"
SUBTLE = "#8A8F99"
GRID = "#EEEDE9"
LINE = "#E4E4DF"
COBALT = "#2346D8"
ORANGE = "#F26A1B"
GOOD = "#0F7A4A"
WARN = "#B54708"
BAD = "#B42318"
BASELINE = "#8A857A"   # baseline / reference series (always dashed + labelled)

# Product colours: fixed order, validated together (never cycled, never by rank).
PRODUCT_COLORS = {"Tacos": "#2346D8", "Burritos": "#F26A1B", "Quesadillas": "#0F8F7A",
                  "Nachos": "#8B3FC7", "Drinks": "#A87800", "Other items": "#8A857A"}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
/* One design system: Inter only; white cards with a 1px grey border and 12px radius; black for
   primary actions, blue for links and charts; colour only in small status chips. */
:root {{ --ink:{INK}; --paper:{PAPER}; --card:{CARD}; --muted:{MUTED}; --subtle:{SUBTLE}; --line:{LINE};
        --brand:{COBALT}; --pos:{GOOD}; --pos-soft:#E8F4EE; --neg:{BAD}; --neg-soft:#FCEBEA;
        --warn:{WARN}; --warn-soft:#FDF3E3; --r: 12px; }}
html, body, .stApp {{ background: var(--paper); color: var(--ink); font-family: 'Inter', sans-serif;
        font-feature-settings: "tnum" 1; -webkit-font-smoothing: antialiased; }}
header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.block-container {{ padding-top: 1.4rem; padding-bottom: 6rem; max-width: 1200px; }}
[data-testid="stMainBlockContainer"] p, [data-testid="stMainBlockContainer"] li {{ font-size: 15px; line-height: 1.55; }}
[data-testid="stVerticalBlock"] {{ gap: 1rem; }}
h1, h2, h3, h4 {{ font-family: 'Inter', sans-serif !important; color: var(--ink); letter-spacing: -0.015em; }}
h1 {{ font-size: 1.75rem !important; font-weight: 700 !important; padding: 0 !important; line-height: 1.2 !important; }}
h2 {{ font-size: 1.35rem !important; font-weight: 700 !important; }}
h4 {{ font-size: 1.05rem !important; font-weight: 600 !important; }}

/* header */
.brand {{ display:flex; align-items:center; gap:10px; }}
.brand-mark {{ width:30px; height:30px; border-radius:8px; background: var(--ink); color:#fff; display:flex;
    align-items:center; justify-content:center; font: 700 15px 'Inter'; flex: 0 0 auto; }}
.brand-name {{ font: 700 16px 'Inter'; white-space: nowrap; }}
.biz-line {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
.sample {{ display:inline-block; font: 600 11px 'Inter'; color: var(--warn); background: var(--warn-soft);
    border-radius: 999px; padding: 1px 8px; white-space: nowrap; cursor: help; }}

/* navigation and segmented filters */
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-seg_"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none !important; }}
.st-key-nav [data-testid="stRadioGroup"] {{ background: #EEEEEA; padding: 3px; border-radius: 10px; display: inline-flex;
    gap: 2px !important; flex-wrap: nowrap !important; }}
.st-key-nav [data-testid="stRadioOption"] {{ border-radius: 8px; margin: 0 !important; padding: 6px 14px !important; cursor: pointer; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--card); box-shadow: 0 1px 2px rgba(0,0,0,.08); }}
.st-key-nav p {{ font: 600 14px 'Inter' !important; color: var(--muted); white-space: nowrap; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color: var(--ink) !important; }}
[class*="st-key-seg_"] [data-testid="stRadioGroup"] {{ gap: 6px !important; flex-wrap: wrap; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"] {{ border: 1px solid var(--line); border-radius: 999px; margin: 0 !important;
    padding: 4px 12px !important; background: var(--card); cursor: pointer; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--ink); border-color: var(--ink); }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] p {{ color: #fff !important; }}
[class*="st-key-seg_"] p {{ font: 600 13px 'Inter' !important; }}

/* one card style for everything */
[class*="st-key-kpi_"], [class*="st-key-card_"], [class*="st-key-panel_"] {{
    background: var(--card); border: 1px solid var(--line); border-radius: var(--r); padding: 18px 20px; }}
[class*="st-key-kpi_"] [data-testid="stVerticalBlock"], [class*="st-key-card_prod"] [data-testid="stVerticalBlock"] {{ gap: .35rem; }}
.st-key-kpi_revenue, [class*="st-key-kpi_profit"], [class*="st-key-kpi_cash"], [class*="st-key-kpi_expenses"],
[class*="st-key-kpi_inventory"], [class*="st-key-kpi_bills"] {{ min-height: 0; }}
.st-key-card_attention {{ border-left: 4px solid #E3A008 !important; }}
.st-key-card_attention_calm {{ border-left: 4px solid var(--pos) !important; }}

/* text */
.eyebrow {{ font: 600 12px 'Inter'; color: var(--subtle); text-transform: uppercase; letter-spacing: .05em; }}
.eyebrow.brand {{ color: var(--brand); }}
.hero {{ font: 600 21px/1.45 'Inter'; color: var(--ink); margin: 4px 0; max-width: 960px; }}
.hero b {{ font-weight: 700; }}
.section-title {{ font: 600 17px 'Inter'; color: var(--ink); margin: 14px 0 0 0; }}
.section-sub {{ font: 500 13.5px 'Inter'; color: var(--muted); margin-top: 2px; }}
.kpi-label {{ font: 500 13px 'Inter'; color: var(--muted); display:flex; align-items:center; gap:6px; }}
.kpi-value {{ font: 700 28px/1.15 'Inter'; letter-spacing: -0.02em; margin: 6px 0 4px 0; color: var(--ink); }}
.kpi-sub {{ font: 500 13px/1.5 'Inter'; color: var(--muted); min-height: 22px; }}
.big-number {{ font: 700 34px/1.1 'Inter'; letter-spacing: -0.02em; margin: 4px 0; color: var(--ink); }}
.big-number.pos {{ color: var(--pos); }} .big-number.neg {{ color: var(--neg); }}
.idea {{ font: 600 18px/1.35 'Inter'; margin: 4px 0 2px 0; }}
.plain {{ font: 500 15px/1.55 'Inter'; color: #33363D; margin: 2px 0; }}
.soft {{ font: 500 13.5px/1.5 'Inter'; color: var(--muted); }}
.check-head {{ font: 600 12px 'Inter'; color: var(--subtle); text-transform: uppercase; letter-spacing: .04em; margin-top: 10px; }}
.prod-name {{ font: 600 16px 'Inter'; }}
.prod-price {{ font: 700 24px 'Inter'; letter-spacing: -0.02em; margin: 2px 0; }}
.callout {{ border: 1px solid #F1DDB8; border-radius: 10px; background: var(--warn-soft); padding: 10px 14px; font: 500 14px 'Inter'; color: #6B3A06; }}
.callout.bad {{ background: var(--neg-soft); border-color: #F3C9C4; color: #7A1A12; }}
.hr {{ border-top: 1px solid var(--line); margin: 2px 0; }}
.attn-head {{ font: 600 17px/1.35 'Inter'; color: var(--ink); }}
.attn-sub {{ font: 500 14px/1.5 'Inter'; color: var(--muted); margin-top: 2px; }}
.attn-icon {{ width: 10px; height: 10px; border-radius: 50%; background: #E3A008; margin-top: 7px; flex: 0 0 auto; font-size: 0; }}
.st-key-card_attention_calm .attn-icon {{ background: var(--pos); }}

/* hover explanations: details live here, not on the page */
.hint {{ position: relative; display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px;
    border-radius: 50%; border: 1px solid #CFCFCA; color: var(--subtle); font: 600 10px 'Inter'; cursor: help;
    margin-left: 6px; vertical-align: middle; text-transform: none; letter-spacing: 0; }}
.hint.label {{ width: auto; height: auto; border: none; border-radius: 0; font: 500 13px 'Inter'; color: var(--muted); margin-left: 0; }}
.hint:hover {{ color: var(--ink); border-color: var(--ink); }}
.hint:hover::after {{ content: attr(data-tip); position: absolute; top: calc(100% + 6px); left: -10px; z-index: 9999;
    width: max-content; max-width: 320px; white-space: normal; text-align: left; background: #111318; color: #fff;
    font: 500 12.5px/1.5 'Inter'; padding: 9px 11px; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,.18); }}
.hint.right:hover::after {{ left: auto; right: -10px; }}
h1 .hint {{ width: 18px; height: 18px; font-size: 11px; margin-left: 8px; }}

/* chips */
.tip {{ display:inline-flex; width:15px; height:15px; border-radius:50%; border:1px solid #CFCFCA; color: var(--subtle);
    font: 600 10px 'Inter'; align-items:center; justify-content:center; cursor: help; }}
.delta, .status {{ white-space: nowrap; display:inline-block; font: 600 12px 'Inter'; padding: 2px 8px; border-radius: 999px; margin-right: 6px; }}
.delta.pos, .status.pos {{ background: var(--pos-soft); color: var(--pos); }}
.delta.neg, .status.neg {{ background: var(--neg-soft); color: var(--neg); }}
.status.warn {{ background: var(--warn-soft); color: var(--warn); }}
.delta.flat, .status.neutral {{ background: #F0F0EC; color: #4A4F59; }}

/* lists */
.row-item {{ display:flex; gap: 12px; align-items:flex-start; padding: 10px 0; border-top: 1px solid var(--line); }}
.row-item:first-child {{ border-top: none; }}
.dot {{ width: 8px; height: 8px; border-radius: 50%; margin-top: 7px; flex: 0 0 auto; }}
.dot.high {{ background: var(--neg); }} .dot.medium {{ background: #E3A008; }} .dot.low {{ background: #B9B7B0; }}
.row-title {{ font: 600 14.5px 'Inter'; color: var(--ink); }}
.row-detail {{ font: 400 14px/1.5 'Inter'; color: var(--muted); }}

/* range bar */
.rb {{ margin: 14px 0 6px 0; }}
.rb-track {{ position: relative; height: 8px; border-radius: 999px; background: #EEEEEA; }}
.rb-fill {{ position: absolute; top: 0; bottom: 0; border-radius: 999px; }}
.rb-zero {{ position: absolute; top: -4px; bottom: -4px; width: 2px; background: #9CA0A8; }}
.rb-mid {{ position: absolute; top: -5px; width: 18px; height: 18px; margin-left: -9px; border-radius: 50%; background: #fff;
    border: 2px solid var(--ink); }}
.rb-labels {{ display:flex; justify-content: space-between; margin-top: 10px; font: 500 12.5px 'Inter'; color: var(--muted); }}
.rb-labels b {{ color: var(--ink); font-weight: 600; }}

/* before / after table */
.ba {{ width: 100%; border-collapse: collapse; font: 500 14.5px 'Inter'; border: none !important; margin: 0 !important; }}
.ba tr, .ba td, .ba th {{ border-left: none !important; border-right: none !important; border-top: none !important; background: transparent !important; }}
.ba th {{ text-align: right; font: 600 12px 'Inter'; color: var(--subtle); text-transform: uppercase; letter-spacing: .04em;
    padding: 0 0 8px 12px !important; border-bottom: 1px solid var(--line) !important; }}
.ba th:first-child, .ba td:first-child {{ text-align: left; padding-left: 0 !important; }}
.ba td {{ padding: 10px 0 10px 12px; border-bottom: 1px solid #F0F0EC !important; text-align: right; color: #33363D; }}
.ba td.now {{ color: var(--muted); }} .ba td.new {{ color: var(--ink); font-weight: 600; }}
.ba tr:last-child td {{ border-bottom: none !important; }}

/* price position bars (local market) */
.pp {{ position: relative; height: 8px; background: #EEEEEA; border-radius: 999px; margin: 14px 0 6px 0; }}
.pp-range {{ position:absolute; top:0; bottom:0; background: #D6DDF7; border-radius: 999px; }}
.pp-med {{ position:absolute; top:-4px; bottom:-4px; width:2px; background: #8A8F99; }}
.pp-you {{ position:absolute; top:-5px; width:18px; height:18px; margin-left:-9px; border-radius:50%; background: var(--brand);
    border: 3px solid #fff; box-shadow: 0 0 0 1px var(--brand); }}

/* buttons: secondary (default), primary (cta_/act_), link (go_/lm_go/chatlink_), quiet tip */
.stButton > button, .stDownloadButton > button, [data-testid="stPopover"] button {{
    border: 1px solid var(--line) !important; border-radius: 8px !important; background: var(--card); color: var(--ink);
    font-weight: 600 !important; box-shadow: none !important; }}
.stButton > button:hover {{ border-color: #BDBDB7 !important; background: #FAFAF8; color: var(--ink); }}
[data-testid="stPopover"] button p {{ white-space: nowrap; }}
[class*="st-key-cta_"] button, [class*="st-key-act_"] button, .stFormSubmitButton > button {{
    background: var(--ink) !important; border: 1px solid var(--ink) !important; border-radius: 8px !important; }}
[class*="st-key-cta_"] button p, [class*="st-key-act_"] button p, .stFormSubmitButton > button p {{ color: #fff !important; font-weight: 600 !important; }}
[class*="st-key-cta_"] button:hover, [class*="st-key-act_"] button:hover {{ background: #2A2D33 !important; }}
[class*="st-key-go_"] button, [class*="st-key-lm_go"] button, [class*="st-key-chatlink_"] button {{ box-shadow: none !important;
    border: none !important; background: transparent !important; padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-go_"] button p, [class*="st-key-lm_go"] button p, [class*="st-key-chatlink_"] button p {{ color: var(--brand) !important;
    font-size: 14px !important; font-weight: 600 !important; }}
[class*="st-key-go_"] button:hover p, [class*="st-key-lm_go"] button:hover p {{ text-decoration: underline; }}
[class*="st-key-tip_"] button {{ border: none !important; background: transparent !important; padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-tip_"] button p {{ color: var(--muted) !important; font-size: 13px !important; font-weight: 500 !important; }}
[class*="st-key-suggest_"] button {{ text-align: left; justify-content: flex-start; padding: 8px 12px !important; }}
[class*="st-key-suggest_"] button > div {{ justify-content: flex-start !important; }}
[class*="st-key-suggest_"] button p {{ font-weight: 500 !important; font-size: 13.5px !important; }}
[data-testid="stExpander"] {{ border: 1px solid var(--line) !important; border-radius: var(--r) !important; background: var(--card); }}
[data-testid="stExpander"] summary p {{ font-weight: 600; }}
[data-testid="stDataFrame"] {{ border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
[data-testid="stMetricValue"] {{ font-weight: 700; }}
div[data-testid="stAlert"] {{ border-radius: 10px; }}
[data-testid="stCheckbox"] label p, [data-testid="stCheckbox"] label div {{ white-space: normal !important;
    overflow: visible !important; text-overflow: clip !important; }}

/* assistant side panel */
.st-key-chat_fab {{ position: fixed; right: 24px; bottom: 24px; z-index: 1001; width: auto !important; }}
.st-key-chat_fab button {{ background: var(--ink) !important; border: none !important; border-radius: 999px !important;
    padding: 10px 18px !important; box-shadow: 0 6px 20px rgba(0,0,0,.18) !important; }}
.st-key-chat_fab button p {{ color: #fff !important; font-weight: 600; }}
.st-key-chat_panel {{ position: fixed; top: 0; right: 0; bottom: 0; z-index: 1000; width: min(420px, 100vw) !important;
    background: var(--card); border-left: 1px solid var(--line); box-shadow: -8px 0 24px rgba(0,0,0,.06);
    padding: 18px 18px 14px 18px; overflow-y: auto; }}
.st-key-chat_box {{ display: flex; flex-direction: column-reverse !important; overflow-y: auto; max-height: calc(100vh - 230px); }}
.st-key-chat_panel [data-testid="stChatMessage"] {{ background: transparent; padding: 4px 0; gap: 10px; }}
.st-key-chat_panel [data-testid="stChatMessageContent"] {{ background: var(--paper); border-radius: 12px; padding: 10px 14px; }}
.st-key-chat_panel [data-testid="stChatMessage"] p, .st-key-chat_panel [data-testid="stChatMessage"] li {{ font-size: 14px; line-height: 1.55; }}
.st-key-chat_toggle_close button {{ border: none !important; background: transparent !important; }}
.chat-head {{ display:flex; align-items:center; gap:10px; }}
.chat-avatar {{ width:32px; height:32px; border-radius: 8px; background: var(--ink); color:#fff; display:flex; align-items:center;
    justify-content:center; font: 700 13px 'Inter'; flex: 0 0 auto; }}
.chat-name {{ font: 600 15px 'Inter'; }}
.chat-sub {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
.chat-empty {{ font: 600 18px/1.35 'Inter'; margin: 18px 0 4px 0; }}
</style>
"""


def esc(x) -> str:
    return html.escape(str(x))


def money(x, decimals: int = 0) -> str:
    if x is None:
        return "n/a"
    s = f"${abs(x):,.{decimals}f}"
    return f"-{s}" if round(x, decimals) < 0 else s


def signed_money(x) -> str:
    if x is None:
        return "n/a"
    return ("+" if round(x) > 0 else "") + money(x)


def pct(x, decimals: int = 1, signed: bool = False) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.{decimals}f}%" if signed else f"{x:.{decimals}f}%"


def delta(change_pct: float | None, *, good_when_up: bool = True, suffix: str = "", decimals: int = 1) -> str:
    """Small coloured pill: ▲ 7.1% (green when the move is good for the business)."""
    if change_pct is None:
        return ""
    if abs(change_pct) < 0.05:
        return f'<span class="delta flat">0%{esc(suffix)}</span>'
    up = change_pct > 0
    cls = "pos" if up == good_when_up else "neg"
    return f'<span class="delta {cls}">{"▲" if up else "▼"} {abs(change_pct):.{decimals}f}%{esc(suffix)}</span>'


chip = delta  # backwards-compatible name


def status(label: str, kind: str = "neutral") -> str:
    return f'<span class="status {kind}">{esc(label)}</span>'


def hint(text: str, label: str = "i", *, right: bool = False) -> str:
    """Hover explanation: a small ⓘ (or a quiet text label) that shows ``text`` on hover."""
    text = str(text).replace("**", "")
    cls = "hint" + (" label" if label != "i" else "") + (" right" if right else "")
    return f'<span class="{cls}" data-tip="{esc(text)}">{esc(label)}</span>'


def tip(text: str) -> str:
    """A small ⓘ that explains a figure on hover."""
    return hint(text)


def kpi_card(label: str, value: str, *, delta_html: str = "", sub: str = "", tooltip: str = "") -> str:
    return (f'<div class="kpi-label">{esc(label)}{tip(tooltip) if tooltip else ""}</div>'
            f'<div class="kpi-value">{esc(value)}</div>'
            f'<div class="kpi-sub">{delta_html}{sub}</div>')


def kpi_html(label: str, value: str, *, chip_html: str = "", sub: str = "", small: bool = False, tag: str = "") -> str:
    return kpi_card(label, value, delta_html=chip_html, sub=sub)


def range_bar(lo: float, mid: float, hi: float, fmt=signed_money) -> str:
    """Downside / expected / upside on one track, with a zero line when the range crosses it."""
    a, b_ = min(lo, 0.0), max(hi, 0.0)
    span = (b_ - a) or 1.0

    def x(v):
        return (v - a) / span * 100

    if lo < 0 < hi:
        z = x(0)
        fill = (f'<div class="rb-fill" style="left:{x(lo):.1f}%;width:{z - x(lo):.1f}%;background:#F7C6BF"></div>'
                f'<div class="rb-fill" style="left:{z:.1f}%;width:{x(hi) - z:.1f}%;background:#B7E2CB"></div>'
                f'<div class="rb-zero" style="left:{z:.1f}%"></div>')
    else:
        color = "#B7E2CB" if lo >= 0 else "#F7C6BF"
        fill = f'<div class="rb-fill" style="left:{x(lo):.1f}%;width:{max(x(hi) - x(lo), 1):.1f}%;background:{color}"></div>'
    return (f'<div class="rb"><div class="rb-track">{fill}<div class="rb-mid" style="left:{x(mid):.1f}%"></div></div>'
            f'<div class="rb-labels"><span>Downside <b>{esc(fmt(lo))}</b></span><span>Expected <b>{esc(fmt(mid))}</b></span>'
            f'<span>Upside <b>{esc(fmt(hi))}</b></span></div></div>')


def before_after(rows: list[tuple[str, str, str, str]]) -> str:
    """rows: (label, today, with change, delta html)."""
    body = "".join(f'<tr><td>{esc(lab)}</td><td class="now">{esc(a)}</td><td class="new">{esc(b_)}</td><td>{d}</td></tr>'
                   for lab, a, b_, d in rows)
    return (f'<table class="ba"><thead><tr><th></th><th>Today\'s plan</th><th>With your change</th><th>Change</th></tr></thead>'
            f'<tbody>{body}</tbody></table>')


def confidence_words(p: float, lo: float, hi: float) -> str:
    """Plain-language confidence from the share of test runs that beat the current plan."""
    if p >= 0.9:
        s = "Very likely to help"
    elif p >= 0.65:
        s = "More likely to help than hurt"
    elif p > 0.35:
        s = "Could go either way"
    elif p > 0.1:
        s = "More likely to hurt than help"
    else:
        s = "Very likely to reduce profit"
    if lo < 0 < hi and p >= 0.65:
        s += ", though a loss is possible"
    elif lo < 0 < hi and p <= 0.35:
        s += ", though a gain is possible"
    return s + "."


def compare_html(label: str, base: str, new: str, chip_html: str = "", note: str = "") -> str:
    return (f'<div class="kpi-label">{esc(label)}</div><div class="kpi-value">{esc(new)}</div>'
            f'<div class="kpi-sub">{chip_html}now {esc(base)} {note}</div>')


def pill(label: str, kind: str) -> str:
    return status(label, {"real": "pos", "synthetic": "warn"}.get(kind, "neutral"))


ORIGIN_PILLS = {"observed": ("Observed", "neutral"), "real": ("Public data", "pos"),
                "forecast": ("Forecast", "neutral"), "simulated": ("Estimate", "neutral"),
                "assumed": ("Assumption", "warn")}


def origin(kind: str) -> str:
    label, cls = ORIGIN_PILLS[kind]
    return status(label, cls)
