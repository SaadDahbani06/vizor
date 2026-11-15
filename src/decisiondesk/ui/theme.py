"""Design system for Main Street Risk Desk (Milestone 5.5): a calm, premium financial product.

Tokens: warm off-white canvas, white cards with hairline borders and soft shadows, 16 px radii,
Inter for text and Inter Tight for numbers and headings, one brand colour (cobalt) and reserved
status colours (positive, negative, warning) that always come with words. The product palette
for charts is unchanged and passes ``validate_palette.js`` (lightness, chroma, colour-blind
separation, contrast) on white.

Components (HTML strings rendered with ``st.markdown(..., unsafe_allow_html=True)``): KPI cards,
delta pills, a range bar (downside / expected / upside), a before-and-after table, status chips,
info tooltips. Streamlit widgets are restyled through ``st-key-*`` container classes.
"""
from __future__ import annotations

import html

INK = "#141414"
PAPER = "#F3EEE3"
CARD = "#FFFCF6"
MUTED = "#5E6470"
SUBTLE = "#8A8F99"
GRID = "#EEEDE9"
LINE = "#E3DCCD"
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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@500;600;700;800&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');
:root {{ --ink:{INK}; --paper:{PAPER}; --card:{CARD}; --muted:{MUTED}; --subtle:{SUBTLE}; --line:{LINE};
        --brand:{COBALT}; --brand-soft:#EEF1FD; --pos:{GOOD}; --pos-soft:#E7F5EE; --neg:{BAD}; --neg-soft:#FDEDEB;
        --warn:{WARN}; --warn-soft:#FEF3E6; --radius:16px;
        --shadow: 4px 4px 0 #141414; --shadow-lg: 6px 6px 0 #141414; --edge: 1.5px solid #141414; }}
html, body, .stApp {{ background: var(--paper); color: var(--ink); font-family: 'Inter', sans-serif;
        font-feature-settings: "tnum" 1, "cv11" 1; -webkit-font-smoothing: antialiased; }}
header[data-testid="stHeader"] {{ display: none; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.block-container {{ padding-top: 1.4rem; padding-bottom: 7rem; max-width: 1240px; }}
[data-testid="stMainBlockContainer"] p, [data-testid="stMainBlockContainer"] li {{ font-size: 15px; line-height: 1.55; }}
[data-testid="stVerticalBlock"] {{ gap: 1rem; }}
h1, h2, h3, h4 {{ font-family: 'Inter Tight', 'Inter', sans-serif !important; letter-spacing: -0.02em; color: var(--ink); }}
h1 {{ font-family: 'Fraunces', Georgia, serif !important; font-weight: 700 !important; font-size: 2.3rem !important;
      padding: 0 !important; letter-spacing: -0.01em; }}
h2 {{ font-weight: 650 !important; font-size: 1.5rem !important; }}
h4 {{ font-weight: 650 !important; font-size: 1.08rem !important; }}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: none; }} }}

/* ---------- header ---------- */
.brand {{ display:flex; align-items:center; gap:10px; }}
.brand-mark {{ width:34px; height:34px; border-radius:9px; background: var(--ink); border: var(--edge);
    color:#F3EEE3; display:flex; align-items:center; justify-content:center; font: 700 17px 'Fraunces', serif; }}
.brand-name {{ font: 700 18px/1.05 'Fraunces', Georgia, serif; letter-spacing: -0.01em; }}
.brand-name, .sample {{ white-space: nowrap; }}
@media (max-width: 1360px) {{ .brand-name {{ font-size: 15px; }} .sample {{ font-size: 10.5px; padding: 1px 7px; margin-left: 0; }} }}
.brand-tag {{ font: 600 10px 'Inter'; letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }}
.biz-line {{ font: 500 13px 'Inter'; color: var(--muted); margin-top: 2px; }}
.sample {{ display:inline-block; font: 600 11.5px 'Inter'; color: var(--warn); background: var(--warn-soft);
    border-radius: 999px; padding: 2px 9px; margin-left: 6px; cursor: help; }}
.st-key-nav [data-testid="stRadioGroup"] {{ background: var(--card); padding: 4px; border-radius: 12px; display: inline-flex;
    gap: 2px !important; border: var(--edge); }}
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-seg_"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none !important; }}
.st-key-nav [data-testid="stRadioOption"] {{ border-radius: 9px; margin: 0 !important; padding: 7px 16px !important; cursor: pointer;
    transition: background .15s ease; }}
.st-key-nav [data-testid="stRadioOption"]:hover {{ background: #EFE8D8; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--ink); }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color: #F3EEE3 !important; }}
.st-key-nav p {{ font: 600 14px 'Inter', sans-serif !important; color: #4A4F59; white-space: nowrap; }}
.st-key-nav [data-testid="stRadioGroup"] {{ flex-wrap: nowrap !important; }}
[data-testid="stPopover"] button p {{ white-space: nowrap; }}
[class*="st-key-seg_"] [data-testid="stRadioGroup"] {{ gap: 6px !important; flex-wrap: wrap; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"] {{ border: var(--edge); border-radius: 999px;
    margin: 0 !important; padding: 5px 14px !important; background: var(--card); cursor: pointer; transition: all .15s ease; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"]:hover {{ border-color: #C9C8C2; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--ink); border-color: var(--ink); }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] p {{ color: #fff !important; }}
[class*="st-key-seg_"] p {{ font: 600 13px 'Inter', sans-serif !important; }}

/* ---------- cards ---------- */
[class*="st-key-kpi_"], [class*="st-key-card_"], [class*="st-key-panel_"] {{
    background: var(--card); border: var(--edge); box-shadow: var(--shadow);
    padding: 20px 22px 18px 22px; border-radius: 14px; animation: rise .35s ease both;
    transition: box-shadow .15s ease, transform .15s ease; }}
[class*="st-key-kpi_"]:hover, [class*="st-key-card_prod"]:hover {{ box-shadow: var(--shadow-lg); transform: translate(-2px, -2px); }}
/* KPI colour roles: one dark hero, tints for status; most cards stay calm cream */
.st-key-kpi_revenue {{ background: var(--ink) !important; }}
.st-key-kpi_revenue .kpi-label, .st-key-kpi_revenue .kpi-sub {{ color: #CFC8B8 !important; }}
.st-key-kpi_revenue .kpi-value {{ color: #FFFCF6 !important; }}
.st-key-kpi_revenue .tip {{ border-color: #6B665C; color: #CFC8B8; }}
.st-key-kpi_revenue [class*="st-key-go_"] button p {{ color: #9DB2FF !important; }}
.st-key-kpi_profit.pos-card {{ background: #DDEFE3 !important; }}
[class*="st-key-kpi_"][class*="__tpos"] {{ background: #DDEFE3 !important; }}
[class*="st-key-kpi_"][class*="__twarn"] {{ background: #FFE9B8 !important; }}
[class*="st-key-kpi_"][class*="__tneg"] {{ background: #FAD4CF !important; }}
[class*="st-key-kpi_"][class*="__tblue"] {{ background: #DCE4FF !important; }}
.st-key-card_attention_calm {{ background: #DDEFE3 !important; }}
.st-key-kpi_revenue, [class*="st-key-kpi_profit"], [class*="st-key-kpi_cash"], [class*="st-key-kpi_expenses"],
[class*="st-key-kpi_inventory"], [class*="st-key-kpi_bills"] {{ min-height: 200px; }}
.kpi-tint-pos {{ background: #DDEFE3 !important; }} .kpi-tint-warn {{ background: #FFE9B8 !important; }}
.kpi-tint-neg {{ background: #FAD4CF !important; }} .kpi-tint-blue {{ background: #DCE4FF !important; }}
/* Business attention bar */
.st-key-card_attention {{ background: #FFDF8E !important; }}
.st-key-card_attention.calm {{ background: #DDEFE3 !important; }}
.attn-head {{ font: 800 21px/1.3 'Inter Tight', 'Inter'; color: var(--ink); letter-spacing: -0.015em; }}
.attn-sub {{ font: 500 14.5px/1.5 'Inter'; color: #3A3428; margin-top: 2px; }}
.attn-icon {{ width: 38px; height: 38px; border-radius: 10px; background: var(--ink); color: #FFDF8E; display:flex;
    align-items:center; justify-content:center; font: 800 20px 'Inter Tight'; flex: 0 0 auto; }}
[class*="st-key-act_"] button {{ background: var(--ink) !important; border: var(--edge) !important; border-radius: 10px !important;
    box-shadow: 3px 3px 0 #FFFCF6 !important; }}
[class*="st-key-act_"] button p {{ color: #FFFCF6 !important; font-weight: 700 !important; font-size: 14px !important; }}
[class*="st-key-act2_"] button {{ background: var(--card) !important; border: var(--edge) !important; border-radius: 10px !important; }}
[class*="st-key-act2_"] button p {{ font-weight: 700 !important; font-size: 14px !important; }}
[class*="st-key-kpi_"] {{ padding: 18px 20px 14px 20px; }}

.st-key-kpi_revenue [data-testid="stVegaLiteChart"] {{ margin-top: -4px; }}
[class*="st-key-kpi_"] [data-testid="stVerticalBlock"], [class*="st-key-card_prod"] [data-testid="stVerticalBlock"] {{ gap: .35rem; }}
.st-key-card_briefing {{ background: var(--card); }}
.eyebrow {{ font: 600 12px 'Inter'; letter-spacing: .04em; text-transform: uppercase; color: var(--subtle); }}
.eyebrow.brand {{ color: var(--brand); }}
.hero {{ font: 600 26px/1.35 'Fraunces', Georgia, serif; letter-spacing: -0.015em; color: var(--ink); margin: 6px 0 4px 0; max-width: 980px; }}
.hero b {{ font-weight: 700; }}
.section-title {{ font: 700 20px 'Fraunces', Georgia, serif; margin: 18px 0 0 0; color: var(--ink); }}
.section-sub {{ font: 500 14px 'Inter'; color: var(--muted); margin-top: 2px; }}
.kpi-label {{ font: 600 13px 'Inter', sans-serif; color: var(--muted); display:flex; align-items:center; gap:6px; }}
.kpi-value {{ font: 800 31px/1.1 'Inter Tight', 'Inter', sans-serif; letter-spacing: -0.025em; margin: 6px 0 4px 0; color: var(--ink); }}
.kpi-sub {{ font: 500 13px/1.45 'Inter', sans-serif; color: var(--muted); min-height: 38px; }}
.tip {{ display:inline-flex; width:15px; height:15px; border-radius:50%; border:1px solid #C9CBD1; color: var(--subtle);
    font: 600 10px 'Inter'; align-items:center; justify-content:center; cursor: help; }}
.delta, .status {{ white-space: nowrap; }}
.delta {{ display:inline-block; font: 600 12px 'Inter'; padding: 2px 8px; border-radius: 999px; margin-right: 6px; }}
.delta.pos {{ background: var(--pos-soft); color: var(--pos); }}
.delta.neg {{ background: var(--neg-soft); color: var(--neg); }}
.delta.flat {{ background: #F1F0EC; color: #4A4F59; }}
.status {{ display:inline-flex; align-items:center; gap:6px; font: 600 12.5px 'Inter'; padding: 3px 10px; border-radius: 999px; }}
.status::before {{ content:''; width:7px; height:7px; border-radius:50%; background: currentColor; }}
.status.pos {{ background: var(--pos-soft); color: var(--pos); }}
.status.neg {{ background: var(--neg-soft); color: var(--neg); }}
.status.warn {{ background: var(--warn-soft); color: var(--warn); }}
.status.neutral {{ background: #F1F0EC; color: #4A4F59; }}
.row-item {{ display:flex; gap: 12px; align-items:flex-start; padding: 10px 0; border-top: 1px solid #F0EFEB; }}
.row-item:first-child {{ border-top: none; }}
.dot {{ width: 9px; height: 9px; border-radius: 50%; margin-top: 7px; flex: 0 0 auto; }}
.dot.high {{ background: var(--neg); }} .dot.medium {{ background: #E3A008; }} .dot.low {{ background: #B9B7B0; }}
.row-title {{ font: 600 15px 'Inter'; color: var(--ink); }}
.row-detail {{ font: 400 14px/1.5 'Inter'; color: var(--muted); margin-top: 1px; }}
.big-number {{ font: 800 40px/1.05 'Inter Tight', 'Inter'; letter-spacing: -0.03em; margin: 4px 0; }}
.big-number.pos {{ color: var(--pos); }} .big-number.neg {{ color: var(--neg); }}
.plain {{ font: 500 15px/1.55 'Inter'; color: #3A3F49; margin: 2px 0; }}
.soft {{ font: 500 13.5px/1.5 'Inter'; color: var(--muted); }}
.idea {{ font: 700 21px/1.3 'Fraunces', Georgia, serif; margin: 4px 0 2px 0; }}
/* range bar */
.rb {{ margin: 14px 0 6px 0; }}
.rb-track {{ position: relative; height: 10px; border-radius: 999px; background: #F0EFEB; }}
.rb-fill {{ position: absolute; top: 0; bottom: 0; border-radius: 999px; }}
.rb-zero {{ position: absolute; top: -5px; bottom: -5px; width: 2px; background: #9CA0A8; border-radius: 1px; }}
.rb-mid {{ position: absolute; top: -6px; width: 22px; height: 22px; margin-left: -11px; border-radius: 50%; background: #fff;
    border: 3px solid var(--ink); box-shadow: 0 1px 4px rgba(0,0,0,.2); }}
.rb-labels {{ display:flex; justify-content: space-between; margin-top: 10px; font: 500 12.5px 'Inter'; color: var(--muted); }}
.rb-labels b {{ color: var(--ink); font-weight: 650; }}
/* before / after table */
.ba {{ width: 100%; border-collapse: collapse; font: 500 14.5px 'Inter'; border: none !important; margin: 0 !important; }}
.ba tr, .ba td, .ba th {{ border-left: none !important; border-right: none !important; border-top: none !important; background: transparent !important; }}
.ba th {{ text-align: right; font: 600 12px 'Inter'; color: var(--subtle); text-transform: uppercase; letter-spacing: .04em;
    padding: 0 0 8px 0; border-bottom: 1px solid var(--line); }}
.ba th:first-child, .ba td:first-child {{ text-align: left; }}
.ba td {{ padding: 11px 0 11px 12px; border-bottom: 1px solid #F0EFEB !important; text-align: right; color: #3A3F49; }}
.ba th {{ padding-left: 12px !important; }}
.ba td.now {{ color: var(--muted); }}
.ba td.new {{ color: var(--ink); font-weight: 650; }}
.ba tr:last-child td {{ border-bottom: none; }}
/* price position bars (local market) */
.pp {{ position: relative; height: 8px; background: #F0EFEB; border-radius: 999px; margin: 14px 0 6px 0; }}
.pp-range {{ position:absolute; top:0; bottom:0; background: #D9DEF7; border-radius: 999px; }}
.pp-med {{ position:absolute; top:-4px; bottom:-4px; width:2px; background: #8A8F99; }}
.pp-you {{ position:absolute; top:-5px; width:18px; height:18px; margin-left:-9px; border-radius:50%; background: var(--brand);
    border: 3px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.25); }}
.prod-name {{ font: 650 16px 'Inter Tight', 'Inter'; }}
.prod-price {{ font: 700 26px 'Inter Tight', 'Inter'; margin: 2px 0 2px 0; letter-spacing: -0.02em; }}
.callout {{ border: 1px solid #F3D9B5; border-radius: 12px; background: var(--warn-soft); padding: 10px 14px; font: 500 14px 'Inter'; color: #6B3A06; }}
.callout.bad {{ background: var(--neg-soft); border-color: #F6C9C3; color: #7A1A12; }}
.check-head {{ font: 600 12px 'Inter'; color: var(--subtle); text-transform: uppercase; letter-spacing: .04em; margin-top: 10px; }}

/* ---------- buttons, inputs ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, [data-testid="stPopover"] button {{
    border: var(--edge) !important; border-radius: 10px !important; box-shadow: 2px 2px 0 #141414;
    font-weight: 600 !important; background: var(--card); color: var(--ink); transition: all .12s ease; }}
.stButton > button:hover {{ color: var(--ink); background: #FFF6DF; transform: translate(-1px, -1px); box-shadow: 3px 3px 0 #141414; }}
[class*="st-key-go_"] button, [class*="st-key-lm_go"] button, [class*="st-key-chatlink_"] button {{ box-shadow: none !important;
    border: none !important; background: transparent !important; padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-go_"] button p, [class*="st-key-lm_go"] button p, [class*="st-key-chatlink_"] button p {{ color: var(--brand) !important;
    font-size: 14px !important; font-weight: 600; }}
[class*="st-key-go_"] button:hover p, [class*="st-key-lm_go"] button:hover p {{ text-decoration: underline; }}
[class*="st-key-cta_"] button, .stFormSubmitButton > button {{ background: var(--ink) !important; border-color: var(--ink) !important;
    border-radius: 10px !important; padding: 8px 18px !important; }}
[class*="st-key-cta_"] button p, .stFormSubmitButton > button p {{ color: #fff !important; font-size: 14.5px !important; }}
[class*="st-key-cta_"] button:hover {{ background: #262A33 !important; }}
[class*="st-key-tip_"] button {{ border: none !important; background: transparent !important; box-shadow: none !important;
    padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-tip_"] button p {{ color: var(--muted) !important; font-size: 13px !important; font-weight: 500 !important; }}
[class*="st-key-tip_"] button:hover p {{ color: var(--ink) !important; }}
[class*="st-key-suggest_"] button {{ border-radius: 12px !important; font-size: 13.5px !important; padding: 8px 12px !important;
    min-height: 0 !important; font-weight: 500 !important; text-align: left; width: 100%; justify-content: flex-start; }}
[class*="st-key-suggest_"] button p {{ font-weight: 500 !important; text-align: left; }}
[class*="st-key-suggest_"] button > div {{ justify-content: flex-start !important; }}
.kpi-sub .status {{ margin-bottom: 4px; }}
.hr {{ border-top: 1px solid #F0EFEB; margin: 2px 0; }}
[data-testid="stExpander"] {{ border: var(--edge) !important; border-radius: 12px !important; background: var(--card); }}
[data-testid="stExpander"] summary p {{ font-weight: 600; }}
[data-testid="stDataFrame"] {{ border: var(--edge); border-radius: 10px; overflow: hidden; }}
[data-testid="stMetricValue"] {{ font-family: 'Inter Tight', 'Inter', sans-serif; font-weight: 700; }}
[data-testid="stMetricLabel"] p {{ color: var(--muted) !important; font-weight: 600 !important; }}
div[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stCheckbox"] label p, [data-testid="stCheckbox"] label div {{ white-space: normal !important;
    overflow: visible !important; text-overflow: clip !important; }}
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] > div > div {{ border-radius: 10px !important; }}

/* ---------- assistant (right drawer) ---------- */
.st-key-chat_fab {{ position: fixed; right: 24px; bottom: 24px; z-index: 1001; width: auto !important; }}
.st-key-chat_fab button {{ background: var(--ink) !important; border: none !important; border-radius: 999px !important;
    padding: 12px 20px !important; box-shadow: 0 10px 30px rgba(14,17,22,.30) !important; }}
.st-key-chat_fab button p {{ color: #fff !important; font-size: 14.5px !important; font-weight: 600; }}
.st-key-chat_panel {{ position: fixed; top: 0; right: 0; bottom: 0; z-index: 1000; width: min(440px, 100vw) !important;
    background: var(--card); border-left: var(--edge); box-shadow: -8px 0 0 rgba(20,20,20,.06);
    padding: 20px 20px 16px 20px; overflow-y: auto; animation: slidein .25s ease both; }}
@keyframes slidein {{ from {{ transform: translateX(24px); opacity: 0; }} to {{ transform: none; opacity: 1; }} }}
.st-key-chat_box {{ display: flex; flex-direction: column-reverse !important; overflow-y: auto; max-height: calc(100vh - 240px);
    padding-right: 4px; }}
.st-key-chat_panel [data-testid="stChatMessage"] {{ background: transparent; padding: 4px 0; gap: 10px; }}
.st-key-chat_panel [data-testid="stChatMessage"]:has([aria-label="Chat message from user"]) {{ flex-direction: row-reverse; }}
.st-key-chat_panel [data-testid="stChatMessageContent"] {{ background: #F3EEE3; border: 1px solid #E3DCCD; border-radius: 16px;
    padding: 10px 14px; }}
.st-key-chat_panel [data-testid="stChatMessage"] p, .st-key-chat_panel [data-testid="stChatMessage"] li {{ font-size: 14px; line-height: 1.55; }}
.st-key-chat_toggle_close button {{ border: none !important; box-shadow: none !important; background: transparent !important; }}
.chat-head {{ display:flex; align-items:center; gap:12px; }}
.chat-avatar {{ width:36px; height:36px; border-radius: 11px; background: var(--ink);
    color:#fff; display:flex; align-items:center; justify-content:center; font: 700 14px 'Inter Tight'; flex: 0 0 auto; }}
.chat-name {{ font: 650 16px 'Inter Tight', 'Inter'; }}
.chat-sub {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
.chat-empty {{ font: 600 20px/1.35 'Inter Tight', 'Inter'; margin: 22px 0 6px 0; }}
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


def tip(text: str) -> str:
    """A small ⓘ with a native hover tooltip (for one-line explanations)."""
    return f'<span class="tip" title="{esc(text)}">i</span>'


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
