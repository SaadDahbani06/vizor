"""Visual system for the Streamlit dashboard (Milestone 5.4): a calm, premium light look.

Soft warm-white canvas, white cards with hairline borders and gentle shadows, rounded corners,
Inter throughout with tabular numbers, and one accent colour. The categorical product palette is
unchanged (validated with ``scripts/validate_palette.js`` from the dataviz method on a near-white
surface: lightness band, chroma floor, colour-blind separation and contrast PASS). Status colours
(good / warning / critical) are reserved for status and always come with a word.
"""
from __future__ import annotations

import html

INK = "#15171C"
PAPER = "#F6F5F2"
CARD = "#FFFFFF"
MUTED = "#61656F"
GRID = "#ECEBE7"
LINE = "#E4E3DF"
COBALT = "#2346D8"
ORANGE = "#F26A1B"
GOOD = "#138A52"
WARN = "#B07D00"
BAD = "#C8102E"
BASELINE = "#8A857A"   # baseline / reference series (always dashed + labelled)

# Product colours: fixed order, validated together (never cycled, never by rank).
PRODUCT_COLORS = {"Tacos": "#2346D8", "Burritos": "#F26A1B", "Quesadillas": "#0F8F7A",
                  "Nachos": "#8B3FC7", "Drinks": "#A87800", "Other items": "#8A857A"}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@600;700&display=swap');
:root {{ --ink:{INK}; --paper:{PAPER}; --card:{CARD}; --muted:{MUTED}; --line:{LINE}; --cobalt:{COBALT};
        --orange:{ORANGE}; --good:{GOOD}; --warn:{WARN}; --bad:{BAD};
        --shadow: 0 1px 2px rgba(20,22,28,.04), 0 6px 20px rgba(20,22,28,.05); }}
html, body, .stApp {{ background: var(--paper); color: var(--ink); font-family: 'Inter', sans-serif;
        font-feature-settings: "tnum" 1, "cv11" 1; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.4rem; padding-bottom: 8rem; max-width: 1180px; }}
[data-testid="stMainBlockContainer"] p, [data-testid="stMainBlockContainer"] li {{ font-size: 16px; line-height: 1.55; }}
[data-testid="stVerticalBlock"] {{ gap: 1.1rem; }}
h1, h2, h3, h4 {{ font-family: 'Inter Tight', 'Inter', sans-serif !important; letter-spacing: -0.02em; color: var(--ink); }}
h1 {{ font-weight: 700 !important; font-size: 2.3rem !important; padding-bottom: 0 !important; }}
h4 {{ font-weight: 650 !important; font-size: 1.2rem !important; }}
h3 {{ font-weight: 650 !important; }}
/* Sidebar: business picker only (collapsed by default) */
[data-testid="stSidebar"] {{ background: #FBFAF8; border-right: 1px solid var(--line); }}
/* Floating analyst: round button bottom-right, roomy panel above it */
.st-key-chat_fab {{ position: fixed; right: 28px; bottom: 26px; z-index: 1001; width: auto !important; }}
.st-key-chat_fab button {{ background: var(--ink) !important; color: #fff !important; border: none !important;
    border-radius: 999px !important; padding: 12px 22px !important; font-size: 15px !important;
    box-shadow: 0 8px 28px rgba(20,22,28,.28) !important; }}
.st-key-chat_fab button p {{ color: #fff !important; font-size: 15px !important; font-weight: 600; }}
.st-key-chat_panel {{ position: fixed; right: 28px; bottom: 92px; z-index: 1000; width: min(460px, calc(100vw - 32px)) !important;
    max-height: calc(100vh - 130px); overflow-y: auto; background: var(--card); border: 1px solid var(--line);
    border-radius: 22px; box-shadow: 0 24px 60px rgba(20,22,28,.22); padding: 20px 20px 14px 20px; }}
.st-key-chat_panel [data-testid="stChatMessage"] {{ background: #F7F6F3; border-radius: 16px; padding: 10px 12px; margin-bottom: 8px; }}
.st-key-chat_panel [data-testid="stChatMessage"] p, .st-key-chat_panel [data-testid="stChatMessage"] li
    {{ font-size: 14px; line-height: 1.5; }}
.analyst-head {{ display:flex; align-items:center; gap:12px; margin: 0 0 6px 0; }}
.analyst-dot {{ width:38px; height:38px; border-radius: 50%; background: linear-gradient(135deg, #2346D8, #6C8BFF);
    color:#fff; display:flex; align-items:center; justify-content:center; font: 700 15px 'Inter'; flex: 0 0 auto; }}
.analyst-name {{ font: 700 17px 'Inter Tight', 'Inter'; }}
.analyst-sub {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
/* Masthead */
.msrd-mast {{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }}
.msrd-brand {{ font: 700 22px/1.1 'Inter Tight', 'Inter', sans-serif; letter-spacing:-0.02em; }}
.msrd-brand span {{ display:inline-block; background: var(--ink); color: #fff; border-radius: 8px; padding: 3px 8px;
    margin-right: 8px; font-size: 14px; vertical-align: 3px; }}
.msrd-meta {{ font: 500 13px 'Inter', sans-serif; color: var(--muted); }}
.msrd-pill {{ display:inline-block; border-radius: 999px; padding: 2px 9px; font: 600 11px 'Inter';
    letter-spacing:.02em; margin-left:6px; background: #F1F0EC; color: #3A3D45; vertical-align: 2px; }}
.msrd-pill.synthetic {{ background:#FFF3CF; color:#6B4C00; }}
.msrd-pill.real {{ background:#DDF3E6; color:#0B5E35; }}
.msrd-pill.sim {{ background:#E6EBFF; color:#1E3AB8; }}
.msrd-pill.assumed {{ background:#FCE6D8; color:#8A3A0C; }}
.msrd-pill.forecast {{ background:#EFE3FA; color:#5B2391; }}
/* Navigation and segmented filters: horizontal radios restyled as pills.
   Streamlit 1.67 markup: [data-testid=stRadioGroup] > div > label[data-testid=stRadioOption][data-selected] */
.st-key-nav [data-testid="stRadioGroup"], [class*="st-key-seg_"] [data-testid="stRadioGroup"] {{ gap: 6px !important; flex-wrap: wrap; }}
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-seg_"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none !important; }}
.st-key-nav [data-testid="stRadioGroup"] {{ background: #ECEBE6; padding: 4px; border-radius: 12px; display: inline-flex; }}
.st-key-nav [data-testid="stRadioOption"] {{ border-radius: 9px; margin: 0 !important; padding: 9px 22px !important;
    cursor: pointer; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--card); box-shadow: 0 1px 3px rgba(20,22,28,.12); }}
.st-key-nav p {{ font: 600 15.5px 'Inter', sans-serif !important; color: #3A3D45; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color: var(--ink) !important; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"] {{ border: 1px solid var(--line); border-radius: 999px;
    margin: 0 !important; padding: 6px 16px !important; background: var(--card); cursor: pointer; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--ink); border-color: var(--ink); }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] p {{ color: #fff !important; }}
[class*="st-key-seg_"] p {{ font: 600 14px 'Inter', sans-serif !important; }}
/* Cards and panels */
[class*="st-key-kpi_"], [class*="st-key-card_"], [class*="st-key-panel_"] {{
    background: var(--card); border: 1px solid var(--line); box-shadow: var(--shadow);
    padding: 26px 28px 22px 28px; border-radius: 20px; }}
[class*="st-key-kpi_"] {{ min-height: 0; }}
.kpi-label {{ font: 600 14px 'Inter', sans-serif; color: var(--muted); }}
.kpi-value {{ font: 700 40px/1.1 'Inter Tight', 'Inter', sans-serif; letter-spacing: -0.02em; margin: 8px 0 6px 0; color: var(--ink); }}
.kpi-value.small {{ font-size: 24px; }}
.kpi-sub {{ font: 500 14.5px/1.45 'Inter', sans-serif; color: var(--muted); }}
.chip {{ display:inline-block; font: 600 12px 'Inter'; padding: 1px 8px; border-radius: 999px; margin-right: 6px; }}
.chip.up {{ background: #DDF3E6; color: #0B5E35; }}
.chip.down {{ background: #FCE1E5; color: #8E0B21; }}
.chip.flat {{ background: #F1F0EC; color: #3A3D45; }}
.chip.warn {{ background: #FFF0C2; color: #6B4C00; }}
.cmp-label {{ font: 600 12px 'Inter'; color: var(--muted); }}
.cmp-base {{ font: 500 13px 'Inter'; color: var(--muted); }}
[class*="st-key-card_cmp"] {{ min-height: 150px; padding: 12px 14px 10px 14px !important; }}
.cmp-new {{ font: 700 23px/1.15 'Inter Tight', 'Inter'; color: var(--ink); margin-top: 4px; white-space: nowrap; }}
.insight-title {{ font: 600 12.5px 'Inter'; color: var(--muted); }}
.insight-text {{ font: 500 15.5px/1.45 'Inter'; color: var(--ink); margin: 6px 0; }}
.section-kicker {{ font: 600 12.5px 'Inter'; color: var(--cobalt); margin-bottom: -8px; }}
.greeting {{ font: 700 40px/1.15 'Inter Tight', 'Inter'; letter-spacing: -0.025em; margin: 18px 0 6px 0; }}
.greeting-sub {{ font: 500 19px/1.5 'Inter'; color: #3A3D45; margin-bottom: 18px; }}
.section-title {{ font: 700 22px 'Inter Tight', 'Inter'; margin: 22px 0 4px 0; }}
.big-line {{ font: 700 30px/1.25 'Inter Tight', 'Inter'; letter-spacing: -0.02em; margin: 4px 0 8px 0; }}
.big-line.good {{ color: #0B5E35; }}
.big-line.bad {{ color: #8E0B21; }}
.plain {{ font: 500 16.5px/1.55 'Inter'; color: #3A3D45; margin: 2px 0; }}
.soft {{ font: 500 14px/1.5 'Inter'; color: var(--muted); }}
.idea {{ font: 700 24px/1.3 'Inter Tight', 'Inter'; margin: 6px 0 10px 0; }}
.demo-banner {{ font: 500 13.5px 'Inter'; color: #6B4C00; background: #FFF6DC; border-radius: 999px; padding: 6px 14px;
    display: inline-block; margin: 4px 0 2px 0; }}
.prod-name {{ font: 700 20px 'Inter Tight', 'Inter'; }}
.prod-price {{ font: 700 30px 'Inter Tight', 'Inter'; margin: 4px 0; }}
.alert-row {{ display:flex; gap: 14px; align-items:flex-start; padding: 6px 0; }}
.alert-row:first-child {{ border-top: none; }}
.sev {{ flex: 0 0 auto; font: 700 11px 'Inter'; border-radius: 999px; padding: 2px 8px; margin-top: 2px; }}
.sev.high {{ background: #FCE1E5; color: #8E0B21; }}
.sev.medium {{ background: #FFF0C2; color: #6B4C00; }}
.sev.low {{ background: #F1F0EC; color: #3A3D45; }}
.alert-title {{ font: 650 17px 'Inter'; }}
.alert-detail {{ font: 400 15.5px/1.5 'Inter'; color: #3A3D45; margin-top: 2px; }}
.alert-next {{ font: 500 14.5px 'Inter'; color: var(--muted); margin-top: 4px; }}
.dot {{ width: 12px; height: 12px; border-radius: 50%; margin-top: 7px; flex: 0 0 auto; }}
.dot.high {{ background: var(--bad); }} .dot.medium {{ background: #E3A008; }} .dot.low {{ background: #B9B7B0; }}
.check-head {{ font: 600 13px 'Inter'; color: var(--muted); margin-top: 8px; }}
.callout {{ border: 1px solid #F0E3B5; border-radius: 12px; background: #FFF9E8; padding: 10px 14px; font: 500 14px 'Inter'; }}
.callout.bad {{ background: #FDF0F2; border-color: #F4CBD2; }}
.callout.good {{ background: #EEF8F2; border-color: #C7E7D4; }}
.legend-row {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
/* Buttons */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, [data-testid="stPopover"] button {{
    border: 1px solid var(--line) !important; border-radius: 10px !important; box-shadow: 0 1px 2px rgba(20,22,28,.05);
    font-weight: 600 !important; background: var(--card); color: var(--ink); }}
.stButton > button:hover {{ border-color: #C9C8C2 !important; color: var(--ink); background: #FAFAF8; }}
.stButton > button[kind="primary"] {{ background: var(--ink) !important; color: #fff !important; border-color: var(--ink) !important; }}
[class*="st-key-go_"] button, [class*="st-key-lm_go"] button {{ box-shadow: none !important; border: none !important;
    background: transparent !important; color: var(--cobalt) !important; padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-go_"] button p, [class*="st-key-lm_go"] button p {{ color: var(--cobalt) !important; font-size: 15px !important; font-weight: 600; }}
.stButton > button {{ padding: 8px 18px !important; }}
[class*="st-key-tip_"] button {{ border: none !important; background: transparent !important; box-shadow: none !important;
    padding: 0 !important; min-height: 0 !important; }}
[class*="st-key-tip_"] button p {{ color: var(--muted) !important; font-size: 13.5px !important; font-weight: 500 !important; }}
[class*="st-key-tip_"] button:hover p {{ color: var(--ink) !important; }}
[class*="st-key-cta_"] button {{ background: var(--ink) !important; border-color: var(--ink) !important; border-radius: 12px !important;
    padding: 10px 22px !important; }}
[class*="st-key-cta_"] button p {{ color: #fff !important; font-size: 15.5px !important; }}
[class*="st-key-go_"] button:hover {{ text-decoration: underline; }}
[class*="st-key-suggest_"] button {{ border-radius: 999px !important; font-size: 12.5px !important; padding: 2px 10px !important;
    min-height: 0 !important; font-weight: 500 !important; text-align: left; }}
[data-testid="stExpander"] {{ border: 1px solid var(--line) !important; border-radius: 14px !important; background: var(--card); }}
[data-testid="stDataFrame"] {{ border: 1px solid var(--line); border-radius: 10px; }}
[data-testid="stMetricValue"] {{ font-family: 'Inter Tight', 'Inter', sans-serif; }}
div[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stCheckbox"] label p, [data-testid="stCheckbox"] label div {{ white-space: normal !important;
    overflow: visible !important; text-overflow: clip !important; }}
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


def chip(change_pct: float | None, *, good_when_up: bool = True, suffix: str = "") -> str:
    if change_pct is None:
        return '<span class="chip flat">no comparison</span>'
    if abs(change_pct) < 0.05:
        return f'<span class="chip flat">0.0%{esc(suffix)}</span>'
    up = change_pct > 0
    cls = "up" if up == good_when_up else "down"
    arrow = "▲" if up else "▼"
    return f'<span class="chip {cls}">{arrow} {abs(change_pct):.1f}%{esc(suffix)}</span>'


def pill(label: str, kind: str) -> str:
    return f'<span class="msrd-pill {kind}">{esc(label)}</span>'


ORIGIN_PILLS = {"observed": ("Observed · synthetic", "synthetic"), "real": ("Real data", "real"),
                "forecast": ("Model forecast", "forecast"), "simulated": ("Simulated", "sim"),
                "assumed": ("Assumption", "assumed")}


def origin(kind: str) -> str:
    label, cls = ORIGIN_PILLS[kind]
    return pill(label, cls)


def kpi_html(label: str, value: str, *, chip_html: str = "", sub: str = "", small: bool = False,
             tag: str = "") -> str:
    return (f'<div class="kpi-label">{esc(label)} {tag}</div>'
            f'<div class="kpi-value{" small" if small else ""}">{esc(value)}</div>'
            f'<div class="kpi-sub">{chip_html}{sub}</div>')


def compare_html(label: str, base: str, new: str, chip_html: str = "", note: str = "") -> str:
    """Scenario value large; the current-plan value and the change underneath."""
    return (f'<div class="cmp-label">{esc(label)}</div>'
            f'<div class="cmp-new">{esc(new)}</div>'
            f'<div class="cmp-base">plan: {esc(base)}</div>'
            f'<div class="kpi-sub" style="margin-top:4px">{chip_html}{note}</div>')


def masthead(business: str, label: str, as_of: str, extra: str = "") -> str:
    kind = "synthetic" if "SYNTHETIC" in label.upper() else "real"
    return (f'<div class="msrd-mast"><div><div class="msrd-brand"><span>MSRD</span>Main Street Risk Desk</div>'
            f'<div class="msrd-meta"><b style="color:var(--ink)">{esc(business)}</b>'
            f'{pill(label, kind)} · data through {esc(as_of)}{esc(extra)}</div></div></div>')
