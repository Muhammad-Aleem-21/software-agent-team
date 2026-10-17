"""Look and feel for the Streamlit app: CSS (animations, responsive grid) and small HTML helpers."""
from __future__ import annotations

from html import escape

AGENTS = [
    ("product_manager", "📋", "Product Manager", "Writes the spec"),
    ("developer", "💻", "Developer", "Writes the code"),
    ("reviewer", "🔍", "Reviewer", "Checks the code"),
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 2rem; max-width: 1100px; }

@keyframes fadeUp { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(99,102,241,.55); } 70% { box-shadow: 0 0 0 14px rgba(99,102,241,0); } 100% { box-shadow: 0 0 0 0 rgba(99,102,241,0); } }
@keyframes flow { to { background-position: 200% 0; } }
@keyframes float { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
@keyframes shimmer { 0% { background-position: 0% 50%; } 100% { background-position: 100% 50%; } }

/* hero */
.hero { padding: 1.6rem 1.8rem; border-radius: 20px; margin-bottom: 1.2rem; color: #fff;
  background: linear-gradient(120deg, #4f46e5, #7c3aed, #db2777, #4f46e5); background-size: 300% 300%;
  animation: shimmer 10s ease infinite alternate, fadeUp .6s ease both; }
.hero h1 { margin: 0; font-size: clamp(1.5rem, 4vw, 2.3rem); font-weight: 800; letter-spacing: -.02em; }
.hero p { margin: .4rem 0 0; opacity: .9; font-size: clamp(.9rem, 2vw, 1.05rem); }

/* pipeline */
.pipe { display: flex; align-items: stretch; gap: .6rem; margin: .6rem 0 1rem; animation: fadeUp .5s ease both; }
.node { flex: 1; border-radius: 16px; padding: .9rem .8rem; text-align: center; border: 1px solid rgba(128,128,128,.25);
  background: rgba(128,128,128,.07); transition: all .3s ease; opacity: .6; }
.node .ico { font-size: 1.7rem; }
.node .nm { font-weight: 700; font-size: .95rem; margin-top: .2rem; }
.node .sub { font-size: .75rem; opacity: .7; }
.node.active { opacity: 1; border-color: #6366f1; background: rgba(99,102,241,.12); animation: pulse 1.4s infinite; }
.node.active .ico { animation: float 1.2s ease-in-out infinite; }
.node.done { opacity: 1; border-color: #22c55e; background: rgba(34,197,94,.10); }
.arrow { align-self: center; width: 28px; height: 4px; border-radius: 4px; flex: none;
  background: linear-gradient(90deg, rgba(128,128,128,.3) 40%, #6366f1 50%, rgba(128,128,128,.3) 60%); background-size: 200% 100%; animation: flow 1.6s linear infinite; }
.badge { display: inline-block; padding: .2rem .7rem; border-radius: 99px; font-size: .78rem; font-weight: 600;
  background: rgba(99,102,241,.15); color: #6366f1; margin-bottom: .4rem; }

/* metric cards */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .8rem; margin: 1rem 0; }
.card { border-radius: 16px; padding: 1rem 1.1rem; border: 1px solid rgba(128,128,128,.22);
  background: rgba(128,128,128,.07); animation: fadeUp .5s ease both; transition: transform .2s ease, box-shadow .2s ease; }
.card:hover { transform: translateY(-3px); box-shadow: 0 8px 24px rgba(99,102,241,.18); }
.card:nth-child(2) { animation-delay: .06s; } .card:nth-child(3) { animation-delay: .12s; }
.card:nth-child(4) { animation-delay: .18s; } .card:nth-child(5) { animation-delay: .24s; }
.card .lbl { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; opacity: .65; }
.card .val { font-size: clamp(1.2rem, 3vw, 1.6rem); font-weight: 800; margin-top: .15rem; }

/* verdict */
.verdict { border-radius: 14px; padding: .8rem 1.1rem; font-weight: 600; animation: fadeUp .4s ease both; }
.verdict.ok { background: rgba(34,197,94,.12); border: 1px solid #22c55e; }
.verdict.warn { background: rgba(245,158,11,.12); border: 1px solid #f59e0b; }

/* inputs and buttons */
.stTextArea textarea { border-radius: 14px !important; font-size: 1rem; transition: box-shadow .2s ease; }
.stTextArea textarea:focus { box-shadow: 0 0 0 3px rgba(99,102,241,.35) !important; }
.stButton > button[kind="primary"] { border: 0; border-radius: 12px; padding: .65rem 1.6rem; font-weight: 700; color: #fff;
  background: linear-gradient(120deg, #6366f1, #8b5cf6, #ec4899); background-size: 200% 200%; transition: transform .15s ease, box-shadow .15s ease; }
.stButton > button[kind="primary"]:hover { transform: translateY(-2px); box-shadow: 0 8px 22px rgba(139,92,246,.45); animation: shimmer 2s ease infinite alternate; }
.stButton > button[kind="primary"]:active { transform: scale(.98); }
.stTabs [data-baseweb="tab-list"] { gap: .3rem; flex-wrap: wrap; }
.stTabs [data-baseweb="tab"] { border-radius: 10px 10px 0 0; padding: .5rem 1rem; transition: background .2s ease; }
.stTabs [data-baseweb="tab"]:hover { background: rgba(99,102,241,.10); }
[data-testid="stTabPanel"] { animation: fadeUp .35s ease both; }
pre { border-radius: 12px !important; }

/* phones */
@media (max-width: 640px) {
  .block-container { padding: 1rem .8rem; }
  .hero { padding: 1.1rem; border-radius: 16px; }
  .pipe { flex-direction: column; }
  .arrow { width: 4px; height: 18px; align-self: center; background-size: 100% 200%; }
  .stButton > button[kind="primary"] { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
"""

HERO = (
    '<div class="hero"><h1>🧑‍💻 AI Software Team</h1>'
    "<p>Describe a feature. A Product Manager, Developer and Reviewer build it together, "
    "and you see exactly what it cost.</p></div>"
)


def pipeline_html(active: str | None, done: set[str], attempt: int = 0) -> str:
    """The three agents as cards; the active one pulses, finished ones turn green."""
    parts = []
    if attempt:
        parts.append(f'<span class="badge">Attempt {attempt}</span>')
    parts.append('<div class="pipe">')
    for i, (key, icon, name, sub) in enumerate(AGENTS):
        state = "active" if key == active else ("done" if key in done else "")
        if i:
            parts.append('<div class="arrow"></div>')
        parts.append(
            f'<div class="node {state}"><div class="ico">{icon}</div>'
            f'<div class="nm">{name}</div><div class="sub">{sub}</div></div>'
        )
    parts.append("</div>")
    return "".join(parts)


def cards_html(items: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<div class="card"><div class="lbl">{escape(l)}</div><div class="val">{escape(v)}</div></div>'
        for l, v in items
    )
    return f'<div class="cards">{cells}</div>'


def verdict_html(ok: bool, text: str) -> str:
    return f'<div class="verdict {"ok" if ok else "warn"}">{"✅" if ok else "⚠️"} {escape(text)}</div>'