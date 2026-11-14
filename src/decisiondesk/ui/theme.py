"""Visual system for the Streamlit dashboard: an editorial, neo-brutalist financial look.

Warm off-white paper, black ink and 2px borders with offset shadows, large numbers set in Space
Grotesk, and a validated categorical palette (``scripts/validate_palette.js`` from the dataviz
method: lightness band, chroma floor, colour-blind separation and contrast all PASS on #FFFDF8).
Status colours (good / warning / critical) are reserved for status and always come with a word.
"""
from __future__ import annotations

import html

INK = "#111111"
PAPER = "#F3EFE6"
CARD = "#FFFDF8"
MUTED = "#5B5850"
GRID = "#E2DCCF"
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
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap');
:root {{ --ink:{INK}; --paper:{PAPER}; --card:{CARD}; --muted:{MUTED}; --cobalt:{COBALT};
        --orange:{ORANGE}; --good:{GOOD}; --warn:{WARN}; --bad:{BAD}; }}
html, body, .stApp {{ background: var(--paper); color: var(--ink); font-family: 'Inter', sans-serif; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1480px; }}
h1, h2, h3, h4 {{ font-family: 'Space Grotesk', sans-serif !important; letter-spacing: -0.02em; color: var(--ink); }}
h1 {{ font-weight: 700 !important; }}
[data-testid="stSidebar"] {{ background: var(--card); border-right: 2px solid var(--ink); }}
/* Masthead */
.msrd-mast {{ display:flex; align-items:flex-end; justify-content:space-between; gap:16px; flex-wrap:wrap;
             border-bottom: 3px solid var(--ink); padding-bottom: 10px; margin-bottom: 6px; }}
.msrd-brand {{ font: 700 30px/1 'Space Grotesk', sans-serif; letter-spacing:-0.03em; }}
.msrd-brand span {{ background: var(--ink); color: var(--card); padding: 2px 8px; margin-right: 6px; }}
.msrd-meta {{ font: 500 13px 'Inter', sans-serif; color: var(--muted); text-align:right; }}
.msrd-pill {{ display:inline-block; border:1.5px solid var(--ink); padding:1px 8px; font:700 11px 'Inter';
             letter-spacing:.06em; text-transform:uppercase; margin-left:6px; background: var(--card); }}
.msrd-pill.synthetic {{ background:#FFE9A8; }}
.msrd-pill.real {{ background:#D8F0E2; }}
.msrd-pill.sim {{ background:#E4E9FF; }}
.msrd-pill.assumed {{ background:#FBE0D0; }}
.msrd-pill.forecast {{ background:#EADCF7; }}
/* Top navigation and segmented filters: horizontal radios restyled as tabs.
   Streamlit 1.67 markup: [data-testid=stRadioGroup] > div > label[data-testid=stRadioOption][data-selected] */
.st-key-nav [data-testid="stRadioGroup"], [class*="st-key-seg_"] [data-testid="stRadioGroup"] {{ gap: 0 !important; flex-wrap: wrap; }}
.st-key-nav [data-testid="stRadioOption"] {{ border: 2px solid var(--ink); margin: 0 -2px -2px 0 !important;
    padding: 8px 13px !important; background: var(--card); cursor: pointer; }}
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
[class*="st-key-seg_"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none !important; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--ink); }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"] p {{ color: var(--card) !important; }}
.st-key-nav [data-testid="stRadioOption"]:hover {{ background: #E9E3D6; }}
.st-key-nav [data-testid="stRadioOption"][data-selected="true"]:hover {{ background: var(--ink); }}
.st-key-nav p {{ font: 700 14px 'Inter', sans-serif !important; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"] {{ border: 1.5px solid var(--ink); margin: 0 -1.5px -1.5px 0 !important;
    padding: 4px 12px !important; background: var(--card); cursor: pointer; }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] {{ background: var(--cobalt); }}
[class*="st-key-seg_"] [data-testid="stRadioOption"][data-selected="true"] p {{ color: #fff !important; }}
[class*="st-key-seg_"] p {{ font: 600 12.5px 'Inter', sans-serif !important; }}
/* Cards and panels */
[class*="st-key-kpi_"], [class*="st-key-card_"], [class*="st-key-panel_"] {{
    background: var(--card); border: 2px solid var(--ink); box-shadow: 5px 5px 0 var(--ink);
    padding: 14px 16px 12px 16px; border-radius: 0; }}
[class*="st-key-panel_"] {{ box-shadow: 4px 4px 0 var(--ink); }}
[class*="st-key-kpi_"] {{ min-height: 262px; }}
.kpi-label {{ font: 700 11.5px 'Inter', sans-serif; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); }}
.kpi-value {{ font: 700 36px/1.05 'Space Grotesk', sans-serif; letter-spacing: -0.03em; margin: 6px 0 4px 0; color: var(--ink); }}
.kpi-value.small {{ font-size: 28px; }}
.kpi-sub {{ font: 500 13px 'Inter', sans-serif; color: var(--muted); }}
.chip {{ display:inline-block; font: 700 12px 'Inter'; padding: 1px 7px; border: 1.5px solid var(--ink); margin-right: 6px; }}
.chip.up {{ background: #D8F0E2; color: #0B5E35; border-color: #0B5E35; }}
.chip.down {{ background: #FBDADF; color: #8E0B21; border-color: #8E0B21; }}
.chip.flat {{ background: #EEE9DE; color: var(--ink); }}
.chip.warn {{ background: #FFF0C2; color: #6B4C00; border-color: #6B4C00; }}
.cmp-label {{ font: 700 11px 'Inter'; letter-spacing:.08em; text-transform: uppercase; color: var(--muted); }}
.cmp-row {{ display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }}
.cmp-base {{ font: 600 13px 'Inter'; color: var(--muted); }}
[class*="st-key-card_cmp"] {{ min-height: 168px; padding: 12px 12px 10px 12px !important; }}
.cmp-arrow {{ font: 700 15px 'Inter'; color: var(--muted); }}
.cmp-new {{ font: 700 25px/1.15 'Space Grotesk'; color: var(--ink); margin-top: 4px; white-space: nowrap; }}
.insight-title {{ font: 700 12px 'Inter'; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); }}
.insight-text {{ font: 600 16px/1.35 'Space Grotesk'; color: var(--ink); margin: 6px 0; }}
.section-kicker {{ font: 700 12px 'Inter'; letter-spacing: .12em; text-transform: uppercase; color: var(--cobalt); margin-bottom: -6px; }}
.callout {{ border: 2px solid var(--ink); background: #FFF6DA; padding: 10px 14px; font: 500 14px 'Inter'; }}
.callout.bad {{ background: #FBDADF; }}
.callout.good {{ background: #D8F0E2; }}
.legend-row {{ font: 500 12.5px 'Inter'; color: var(--muted); }}
/* Buttons */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    border: 2px solid var(--ink) !important; border-radius: 0 !important; box-shadow: 3px 3px 0 var(--ink);
    font-weight: 700 !important; background: var(--card); color: var(--ink); }}
.stButton > button:hover {{ transform: translate(-1px,-1px); box-shadow: 4px 4px 0 var(--ink); border-color: var(--ink) !important; color: var(--ink); }}
.stButton > button[kind="primary"] {{ background: var(--cobalt) !important; color: #fff !important; }}
[class*="st-key-go_"] button {{ box-shadow: none !important; border-width: 1.5px !important; padding: 2px 10px !important;
    min-height: 0 !important; font-size: 13px !important; }}
[data-testid="stExpander"] {{ border: 2px solid var(--ink) !important; border-radius: 0 !important; background: var(--card); }}
[data-testid="stDataFrame"] {{ border: 1.5px solid var(--ink); }}
[data-testid="stMetricValue"] {{ font-family: 'Space Grotesk', sans-serif; }}
div[data-testid="stAlert"] {{ border-radius: 0; border: 1.5px solid var(--ink); }}
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
    return (f'<div class="msrd-mast"><div class="msrd-brand"><span>MSRD</span>Main Street Risk Desk</div>'
            f'<div class="msrd-meta"><b style="color:var(--ink)">{esc(business)}</b>'
            f'{pill(label, kind)}<br>data through {esc(as_of)}{esc(extra)}</div></div>')
