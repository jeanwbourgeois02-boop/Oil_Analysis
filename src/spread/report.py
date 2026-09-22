"""
Bericht (Markdown + HTML, deutsch) zur RB/HO Jun-vs-Dec Crack-Spread-Analyse.

    write_report(spread_daily, vintage_stats, seasonal, overall, chart_paths, out_dir,
                 validation_notes=None) -> {"report_md": Path, "report_html": Path}

Aufbau: (1) Kernaussagen, (2) Methodik, (3) Tabelle je Jahrgang, (4) Charts (relative Pfade),
(5) Datenqualitaet (optionale validation_notes + automatische Kennzahlen).

Der Bericht wird aus einer Liste einfacher Bausteine (Ueberschrift, Absatz, Liste, Tabelle,
Bild) erzeugt; zwei Renderer schreiben daraus Markdown bzw. HTML. Das ``markdown``-Paket wird
nicht benoetigt. Inline werden nur ``**fett**`` und ``[Text](URL)`` unterstuetzt.

Zahlen: 2 Dezimalen mit Dezimalkomma (ASCII-Minus, damit Copy-Paste in Excel funktioniert),
Datumsangaben TT.MM.JJJJ.
"""
from __future__ import annotations

import html as _html
import os
import re
import sys
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:
    from . import config
    from .charts import (CHART_SPECS, PALETTE, UNIT, _current_vintage, _get_seasonal, _prep_daily, _prep_stats,
                         _vintage_columns, fmt_date, fmt_de)
else:  # Skriptkontext (py src/spread/report.py)
    _SRC = Path(__file__).resolve().parents[1]
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from spread import config  # type: ignore
    from spread.charts import (CHART_SPECS, PALETTE, UNIT, _current_vintage, _get_seasonal, _prep_daily,  # type: ignore
                               _prep_stats, _vintage_columns, fmt_date, fmt_de)

REPORT_TITLE = f"{config.SPREAD_LABEL} – Analyse der Jahrgänge"
CHART_ORDER = ["chain", "seasonal", "vintage_range", "negative_days", "heatmap"]
CHART_CAPTIONS = {
    "chain": "Durchgehende Kette aller Jahrgänge. Bänder markieren die Fenster (1. Juni Y−1 bis Verfall Ende Mai Y), "
             "die Linie ist an den Jahrgangsgrenzen unterbrochen, weil dort das Kontraktpaar wechselt. "
             "Der laufende Jahrgang ist orange, das Gesamtminimum rot markiert.",
    "seasonal": "Links: abgeschlossene Jahrgänge auf der Achse „Handelstage bis Verfall“ (Verfall rechts), mit Median "
                "(schwarz) und Min–Max-Spanne (grau). Rechts: der laufende Jahrgang auf der Achse „Handelstage seit "
                "Fensterbeginn“ gegen Median und Spanne der abgeschlossenen Jahrgänge auf derselben Achse.",
    "vintage_range": "Je Jahrgang die Spanne zwischen Minimum und Maximum, dazu Startwert (offener Kreis), Wert bei "
                     "Verfall (gefüllter Kreis) und Minimum (Raute). Der rot hinterlegte Bereich liegt unter null.",
    "negative_days": "Anzahl der Handelstage mit negativem Spread je Jahrgang (Anteil an allen Handelstagen des "
                     "Fensters in Klammern). Rot: Jahrgänge, deren Wert bei Verfall negativ war.",
    "heatmap": "Mittlerer Spread je Kalendermonat des Fensters (Juni des Vorjahres bis Mai des Jahrgangs), "
               "divergierend um null: blau positiv, rot negativ.",
}


# ----------------------------------------------------------------------------
# Formatierung
# ----------------------------------------------------------------------------
def num(x, nd: int = 2) -> str:
    return fmt_de(x, nd, unicode_minus=False)


def _int(x) -> str:
    try:
        if x is None or pd.isna(x):
            return "–"
        return fmt_de(int(round(float(x))), 0, unicode_minus=False)
    except (TypeError, ValueError):
        return str(x)


def _get(d: dict | None, key: str, default=None):
    if not isinstance(d, dict):
        return default
    v = d.get(key, default)
    return default if v is None else v


def _val_date_vint(item) -> tuple[float, object, object]:
    """overall_min/overall_max koennen dict, Tupel oder Zahl sein."""
    if isinstance(item, dict):
        return item.get("value", np.nan), item.get("date"), item.get("vintage")
    if isinstance(item, (tuple, list)) and len(item) >= 1:
        v = item[0]
        d = item[1] if len(item) > 1 else None
        vt = item[2] if len(item) > 2 else None
        return v, d, vt
    if isinstance(item, (int, float, np.floating)):
        return float(item), None, None
    return np.nan, None, None


# ----------------------------------------------------------------------------
# Bausteine
# ----------------------------------------------------------------------------
def H(level: int, text: str) -> tuple:
    return ("h", (level, text))


def P(text: str) -> tuple:
    return ("p", text)


def UL(items: list[str]) -> tuple:
    return ("ul", items)


def TABLE(headers: list[str], rows: list[list[str]], align: list[str] | None = None) -> tuple:
    return ("table", (headers, rows, align or ["l"] * len(headers)))


def IMG(src: str, alt: str, caption: str = "") -> tuple:
    return ("img", (src, alt, caption))


_INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
_INLINE_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _inline_html(text: str) -> str:
    s = _html.escape(text, quote=False)
    s = _INLINE_LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = _INLINE_BOLD.sub(r"<strong>\1</strong>", s)
    return s


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_markdown(blocks: list[tuple]) -> str:
    out: list[str] = []
    for kind, payload in blocks:
        if kind == "h":
            level, text = payload
            out.append(f"{'#' * level} {text}\n")
        elif kind == "p":
            out.append(f"{payload}\n")
        elif kind == "ul":
            out.extend(f"- {item}" for item in payload)
            out.append("")
        elif kind == "table":
            headers, rows, align = payload
            out.append("| " + " | ".join(_md_cell(h) for h in headers) + " |")
            out.append("|" + "|".join("---:" if a == "r" else ":---" for a in align) + "|")
            for r in rows:
                out.append("| " + " | ".join(_md_cell(str(c)) for c in r) + " |")
            out.append("")
        elif kind == "img":
            src, alt, caption = payload
            out.append(f"![{alt}]({src})\n")
            if caption:
                out.append(f"*{caption}*\n")
    return "\n".join(out).rstrip() + "\n"


_CSS = f"""
:root{{color-scheme:light;--page:{PALETTE['page']};--surface:{PALETTE['surface']};--ink:{PALETTE['ink']};
--ink2:{PALETTE['ink2']};--muted:{PALETTE['muted']};--grid:{PALETTE['grid']};--blue:{PALETTE['blue']};--neg:rgba(227,73,72,.13)}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;font-weight:600;margin:0 0 6px}}h2{{font-size:20px;font-weight:600;margin:36px 0 12px;padding-top:12px;border-top:1px solid var(--grid)}}
h3{{font-size:16px;font-weight:600;margin:24px 0 8px}}p{{margin:8px 0}}.meta{{color:var(--ink2);margin-bottom:18px}}
ul{{padding-left:22px}}li{{margin:5px 0}}a{{color:var(--blue)}}
.tablewrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--grid);border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--grid);white-space:nowrap}}th{{text-align:left;color:var(--ink2);font-weight:600;background:var(--page)}}
td.r,th.r{{text-align:right}}td.neg{{background:var(--neg)}}tr:last-child td{{border-bottom:none}}
figure{{margin:16px 0 28px;background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:8px}}
figure img{{display:block;width:100%;height:auto}}figcaption{{color:var(--ink2);font-size:13.5px;padding:8px 6px 2px}}
code{{font-family:ui-monospace,Consolas,monospace;font-size:.92em;background:var(--surface);padding:1px 4px;border-radius:4px;border:1px solid var(--grid)}}
footer{{color:var(--muted);font-size:12.5px;margin-top:40px}}
"""


def render_html(blocks: list[tuple], title: str) -> str:
    body: list[str] = []
    for kind, payload in blocks:
        if kind == "h":
            level, text = payload
            body.append(f"<h{level}>{_inline_html(text)}</h{level}>")
        elif kind == "p":
            cls = ' class="meta"' if payload.startswith("Stand") else ""
            body.append(f"<p{cls}>{_inline_html(payload)}</p>")
        elif kind == "ul":
            body.append("<ul>" + "".join(f"<li>{_inline_html(i)}</li>" for i in payload) + "</ul>")
        elif kind == "table":
            headers, rows, align = payload
            ths = "".join(f'<th class="{a}">{_html.escape(h)}</th>' for h, a in zip(headers, align))
            trs = []
            for r in rows:
                tds = []
                for c, a in zip(r, align):
                    c = str(c)
                    neg = " neg" if (a == "r" and c.startswith("-")) else ""
                    tds.append(f'<td class="{a}{neg}">{_html.escape(c)}</td>')
                trs.append("<tr>" + "".join(tds) + "</tr>")
            body.append(f'<div class="tablewrap"><table><thead><tr>{ths}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>')
        elif kind == "img":
            src, alt, caption = payload
            cap = f"<figcaption>{_inline_html(caption)}</figcaption>" if caption else ""
            body.append(f'<figure><img src="{_html.escape(src, quote=True)}" alt="{_html.escape(alt, quote=True)}" loading="lazy">{cap}</figure>')
    return (f'<!DOCTYPE html>\n<html lang="de"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{_html.escape(title)}</title>'
            f"<style>{_CSS}</style></head><body><main>\n" + "\n".join(body) +
            f"\n<footer>Erstellt am {_date.today().strftime('%d.%m.%Y')} · {_html.escape(config.SPREAD_LABEL)}</footer>"
            "</main></body></html>\n")


# ----------------------------------------------------------------------------
# Inhalte
# ----------------------------------------------------------------------------
def key_facts(sd: pd.DataFrame, vs: pd.DataFrame, seasonal: dict | None, overall: dict | None,
              cur: int | None) -> list[str]:
    """5-8 Kernaussagen, aus den Daten berechnet (overall nur als Vorrang-Quelle, alles hat Fallbacks)."""
    overall = overall if isinstance(overall, dict) else {}
    facts: list[str] = []
    comp = vs[vs["is_complete"]]
    n_all, n_comp = len(vs), len(comp)
    cur_vs = vs[vs["vintage"] == cur] if cur is not None else vs.iloc[0:0]

    # 1) Gesamtminimum
    v, d, vt = _val_date_vint(overall.get("overall_min"))
    if v is None or pd.isna(v):
        if sd["spread_bbl"].notna().any():
            i = sd["spread_bbl"].idxmin()
            v, d, vt = float(sd.loc[i, "spread_bbl"]), sd.loc[i, "date"], int(sd.loc[i, "vintage"])
    if v is not None and not pd.isna(v):
        where = f" (Jahrgang {int(vt)})" if vt is not None and not pd.isna(vt) else ""
        facts.append(f"**Tiefster Stand der gesamten Historie:** {num(v)} {UNIT} am {fmt_date(d)}{where}"
                     + (" – der Spread war dort negativ, der Dezember-Crack also reicher als der Juni-Crack." if v < 0
                        else " – der Spread blieb in der gesamten Historie positiv."))

    # 2) Negative Jahrgaenge
    neg_list = _get(overall, "vintages_negative")
    if neg_list is None:
        neg_list = [int(x) for x in vs.loc[vs["n_negative_days"].fillna(0) > 0, "vintage"]]
    neg_list = [int(x) for x in neg_list]
    n_neg = int(_get(overall, "n_vintages_negative_any_day", len(neg_list)))
    n_neg_exp = int(_get(overall, "n_vintages_negative_at_expiry", int((comp["last_value"] < 0).sum())))
    if n_neg > 0:
        extra = ""
        if "longest_negative_streak" in vs.columns and vs["longest_negative_streak"].notna().any():
            j = vs["longest_negative_streak"].idxmax()
            extra = (f" Die längste zusammenhängende Negativphase dauerte {_int(vs.loc[j, 'longest_negative_streak'])} "
                     f"Handelstage (Jahrgang {int(vs.loc[j, 'vintage'])}).")
        facts.append(f"**{n_neg} von {n_all} Jahrgängen** notierten an mindestens einem Handelstag unter null "
                     f"({', '.join(str(x) for x in neg_list)}); {n_neg_exp} davon "
                     f"{'endete' if n_neg_exp == 1 else 'endeten'} bei Verfall negativ.{extra}")
    else:
        facts.append(f"**Kein Jahrgang** ({n_all} untersucht) notierte an irgendeinem Handelstag unter null.")

    # 3) Niveau Start vs. Verfall
    if n_comp:
        f_med, l_med = comp["first_value"].median(), comp["last_value"].median()
        l_mean = float(_get(overall, "mean_last_value_complete", comp["last_value"].mean()))
        facts.append(f"**Typisches Niveau:** zu Fensterbeginn im Median {num(f_med)} {UNIT} "
                     f"(Spanne {num(comp['first_value'].min())} bis {num(comp['first_value'].max())}), bei Verfall im Median "
                     f"{num(l_med)} {UNIT} (Mittel {num(l_mean)}; Spanne {num(comp['last_value'].min())} bis "
                     f"{num(comp['last_value'].max())}). Über das Fenster baut der Spread damit typischerweise "
                     f"{num(f_med - l_med)} {UNIT} ab.")

    # 4) Zeitpunkt und Hoehe des Minimums
    if n_comp and "tdays_to_expiry_at_min" in comp.columns and comp["tdays_to_expiry_at_min"].notna().any():
        t = comp["tdays_to_expiry_at_min"].dropna()
        n_late = int((t <= 20).sum())
        m_mean = float(_get(overall, "mean_min_complete", comp["min"].mean()))
        facts.append(f"**Zeitpunkt des Minimums:** im Median {_int(t.median())} Handelstage vor Verfall "
                     f"(Spanne {_int(t.min())} bis {_int(t.max())}); in {n_late} von {len(t)} Jahrgängen fiel das Minimum in die "
                     f"letzten 20 Handelstage. Das Jahrgangsminimum liegt im Mittel bei {num(m_mean)} {UNIT} "
                     f"(Median {num(comp['min'].median())}).")

    # 5) Verhalten in den letzten 20 Handelstagen
    if n_comp and "value_t20" in comp.columns and comp["value_t20"].notna().any():
        chg = (comp["last_value"] - comp["value_t20"]).dropna()
        facts.append(f"**In den letzten 20 Handelstagen vor Verfall** veränderte sich der Spread im Median um "
                     f"{num(chg.median())} {UNIT} (positiv in {int((chg > 0).sum())} von {len(chg)} Jahrgängen).")

    # 6) Drawdown
    if n_comp and "max_drawdown" in comp.columns and comp["max_drawdown"].notna().any():
        j = comp["max_drawdown"].idxmax()
        facts.append(f"**Rückgang vom Zwischenhoch:** im Median {num(comp['max_drawdown'].median())} {UNIT}, "
                     f"maximal {num(comp.loc[j, 'max_drawdown'])} {UNIT} (Jahrgang {int(comp.loc[j, 'vintage'])}).")

    # 7) Laufender Jahrgang vs. Median bei gleichem tday_index
    if cur is not None:
        by_ti = _get_seasonal(seasonal, "by_tday_index", sd, cur)
        vcols = _vintage_columns(by_ti)
        g = sd[(sd["vintage"] == cur) & sd["spread_bbl"].notna()]
        if len(g):
            ti_last = int(g["tday_index"].iloc[-1]) if not pd.isna(g["tday_index"].iloc[-1]) else len(g) - 1
            y_last = float(g["spread_bbl"].iloc[-1])
            d_last = g["date"].iloc[-1]
            txt = (f"**Laufender Jahrgang {cur}** (Stand {fmt_date(d_last)}, Handelstag {ti_last} seit Fensterbeginn): "
                   f"{num(y_last)} {UNIT}")
            if ti_last in by_ti.index and not pd.isna(by_ti.loc[ti_last, "median"]):
                med = float(by_ti.loc[ti_last, "median"])
                comp_cols = [c for v_, c in vcols.items() if v_ != cur]
                row = by_ti.loc[ti_last, comp_cols].dropna() if comp_cols else pd.Series(dtype=float)
                n_below = int((row < y_last).sum())
                diff = y_last - med
                txt += (f", das sind {num(abs(diff))} {UNIT} {'über' if diff >= 0 else 'unter'} dem historischen Median "
                        f"am gleichen Handelstag ({num(med)} {UNIT}); {n_below} von {len(row)} abgeschlossenen Jahrgängen "
                        f"lagen zu diesem Zeitpunkt tiefer")
            cmin = float(g["spread_bbl"].min())
            dmin = g.loc[g["spread_bbl"].idxmin(), "date"]
            txt += f". Bisheriges Minimum: {num(cmin)} {UNIT} am {fmt_date(dmin)}" + (" (negativ)." if cmin < 0 else ".")
            facts.append(txt)
    elif len(cur_vs) == 0:
        facts.append("**Kein laufender Jahrgang** in den Daten enthalten.")

    # 8) Gesamtmaximum (nur wenn noch Platz)
    v, d, vt = _val_date_vint(overall.get("overall_max"))
    if (v is None or pd.isna(v)) and sd["spread_bbl"].notna().any():
        i = sd["spread_bbl"].idxmax()
        v, d, vt = float(sd.loc[i, "spread_bbl"]), sd.loc[i, "date"], int(sd.loc[i, "vintage"])
    if len(facts) < 8 and v is not None and not pd.isna(v):
        where = f" (Jahrgang {int(vt)})" if vt is not None and not pd.isna(vt) else ""
        facts.append(f"**Höchststand:** {num(v)} {UNIT} am {fmt_date(d)}{where}.")
    return facts[:8]


def methodology_blocks(sd: pd.DataFrame, vs: pd.DataFrame, cur: int | None) -> list[tuple]:
    comp = vs[vs["is_complete"]]
    first_v = int(vs["vintage"].min()) if len(vs) else config.FIRST_VINTAGE
    last_c = int(comp["vintage"].max()) if len(comp) else None
    win = ""
    if len(vs):
        ws, we = vs["window_start"].min(), vs["window_end"].max()
        win = f" Datenstand: {fmt_date(ws)} bis {fmt_date(we)}."
    items = [
        f"**Formel:** `spread_bbl = [(RB_JunY − HO_JunY) − (RB_DecY − HO_DecY)] × 42` in {UNIT}. "
        "RB = NYMEX RBOB Gasoline (Bloomberg-Root XB), HO = NYMEX NY Harbor ULSD (Bloomberg-Root HO), beide in $/gal; "
        f"42 gal = 1 bbl. Positiv bedeutet: der Juni-Gasoline-Crack ist reicher als der Dezember-Crack "
        "(normale Sommer-Fahrsaison-Saisonalität).",
        "**Fenster eines Jahrgangs Y:** 1. Juni (Y−1) bis zum letzten Handelstag des Jun-Kontrakts, d. h. dem letzten "
        "NYMEX-Geschäftstag im Mai Y (CME Rulebook Ch. 191/150; Achtung Memorial Day). Das tatsächliche Fensterende wird "
        "aus den Daten genommen (letzter Tag mit Preis im Jun-Bein). Die Fenster überlappen nicht; die Kette ist lückenlos.",
        f"**Jahrgänge:** {first_v} bis {last_c if last_c else '–'} abgeschlossen"
        + (f", {cur} läuft seit {fmt_date(vs.loc[vs['vintage'] == cur, 'window_start'].iloc[0]) if (vs['vintage'] == cur).any() else '01.06.' + str(cur - 1)}." if cur else ".")
        + win,
        "**ULSD-Bruch:** Ab dem Mai-2013-Kontrakt ist HO = ULSD (< 15 ppm Schwefel). Der Handel der ULSD-Spezifikation begann "
        "am 29.04.2012, das Fenster ab 01.06.2012 (Jahrgang 2013) ist damit vollständig ULSD; ältere Jahrgänge "
        "(Heizöl 2000 ppm) werden nicht verglichen.",
        "**Datenquelle:** Bloomberg, Feld PX_LAST (Settlement) der 60 Kontrakte (4 Beine × 15 Jahrgänge, z. B. "
        "`XBM13 Comdty`, `HOM13 Comdty`, `XBZ13 Comdty`, `HOZ13 Comdty`).",
        "**Inner-Join-Regel:** In die Kette gehen nur Handelstage ein, an denen alle vier Beine einen Preis haben. "
        "Tage mit fehlendem Bein entfallen ersatzlos (keine Interpolation, kein Vortragen).",
        "**Kennzahlen:** `tdays_to_expiry` = verbleibende Handelstage im Fenster (0 am letzten Handelstag); `tday_index` = "
        "Handelstage seit Fensterbeginn (0 am ersten Tag). Negative Tage = Handelstage mit `spread_bbl < 0`; längste "
        "Negativserie in Handelstagen; max. Drawdown = größter Rückgang vom laufenden Hoch innerhalb des Fensters (≥ 0).",
        "**Laufender Jahrgang:** Da der Verfall noch nicht bekannt ist, wird der laufende Jahrgang nicht auf der Achse "
        "„Handelstage bis Verfall“ gezeigt, sondern auf „Handelstage seit Fensterbeginn“ und dort mit Median und Spanne "
        "der abgeschlossenen Jahrgänge am gleichen Handelstag verglichen.",
        "**Darstellung:** Zahlen mit 2 Dezimalstellen und Dezimalkomma, Datumsangaben als TT.MM.JJJJ; Anteil negativer "
        "Tage = negative Tage / Handelstage des Fensters.",
    ]
    return [H(2, "Methodik"), UL(items)]


def vintage_table_block(vs: pd.DataFrame, cur: int | None) -> tuple:
    headers = ["Jahrgang", "Fenster", "Tage", "Start", "Verfall", "Minimum (Datum)", "Maximum (Datum)",
               "Neg. Tage", "Anteil neg.", "Längste neg. Serie", "Max. Drawdown"]
    align = ["l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "r"]
    rows = []
    for r in vs.sort_values("vintage").itertuples(index=False):
        d = r._asdict()
        v = int(d["vintage"])
        is_cur = (cur is not None and v == cur) or not bool(d.get("is_complete", True))
        n_days = d.get("n_days", np.nan)
        n_neg = d.get("n_negative_days", np.nan)
        pct = (100.0 * n_neg / n_days) if (n_days and not pd.isna(n_days) and n_days > 0 and not pd.isna(n_neg)) \
            else d.get("pct_negative_days", np.nan)
        rows.append([
            f"{v} (laufend)" if is_cur else str(v),
            f"{fmt_date(d.get('window_start'))} – {fmt_date(d.get('window_end'))}",
            _int(n_days),
            num(d.get("first_value")),
            (num(d.get("last_value")) + (" *" if is_cur else "")),
            f"{num(d.get('min'))} ({fmt_date(d.get('min_date'))})",
            f"{num(d.get('max'))} ({fmt_date(d.get('max_date'))})",
            _int(n_neg),
            (num(pct, 1) + " %") if not pd.isna(pct) else "–",
            _int(d.get("longest_negative_streak", np.nan)),
            num(d.get("max_drawdown", np.nan)),
        ])
    return TABLE(headers, rows, align)


def data_quality_blocks(sd: pd.DataFrame, vs: pd.DataFrame, validation_notes: str | None) -> list[tuple]:
    blocks: list[tuple] = [H(2, "Datenqualität")]
    if validation_notes and str(validation_notes).strip():
        lines = [ln.rstrip() for ln in str(validation_notes).splitlines()]
        bullets, para = [], []

        def flush_para():
            if para:
                blocks.append(P(" ".join(para)))
                para.clear()

        def flush_bullets():
            if bullets:
                blocks.append(UL(list(bullets)))
                bullets.clear()

        for ln in lines:
            s = ln.strip()
            if s.startswith(("- ", "* ", "• ")):
                flush_para()
                bullets.append(s[2:].strip())
            elif s:
                flush_bullets()
                para.append(s)
            else:
                flush_para()
                flush_bullets()
        flush_para()
        flush_bullets()
    else:
        blocks.append(P("Keine Validierungshinweise aus der Pipeline übergeben (Platzhalter). Die folgenden Kennzahlen "
                        "wurden automatisch aus `spread_daily` berechnet."))

    items = []
    if len(sd):
        items.append(f"Zeitraum: {fmt_date(sd['date'].min())} bis {fmt_date(sd['date'].max())}, "
                     f"{_int(len(sd))} Handelstage mit vollständigen Daten, {_int(sd['vintage'].nunique())} Jahrgänge.")
        n_nan = int(sd["spread_bbl"].isna().sum())
        items.append(f"Fehlende Spread-Werte in der Kette: {_int(n_nan)}.")
        if len(vs) and "n_days" in vs.columns:
            comp = vs[vs["is_complete"]]
            if len(comp):
                items.append(f"Handelstage je abgeschlossenem Jahrgang: {_int(comp['n_days'].min())} bis "
                             f"{_int(comp['n_days'].max())} (Median {_int(comp['n_days'].median())}).")
        gaps = sd["date"].diff().dt.days
        big = sd.loc[gaps > 5, ["date"]].assign(gap=gaps[gaps > 5]).sort_values("gap", ascending=False).head(3)
        if len(big):
            desc = "; ".join(f"{_int(r.gap)} Kalendertage bis {fmt_date(r.date)}" for r in big.itertuples())
            items.append(f"Größte Lücken zwischen aufeinanderfolgenden Handelstagen (> 5 Kalendertage): {desc}.")
        else:
            items.append("Keine Lücken > 5 Kalendertage zwischen aufeinanderfolgenden Handelstagen.")
        dup = int(sd["date"].duplicated().sum())
        items.append(f"Doppelte Handelstage: {_int(dup)}.")
    blocks.append(UL(items))
    return blocks


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def build_blocks(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None, seasonal: dict | None,
                 overall: dict | None, chart_paths: dict | None, out_dir: Path,
                 validation_notes: str | None = None) -> list[tuple]:
    sd = _prep_daily(spread_daily)
    cur = _current_vintage(sd, vintage_stats)
    vs = _prep_stats(vintage_stats, sd, cur)
    chart_paths = chart_paths or {}
    out_dir = Path(out_dir)

    def rel(p) -> str:
        try:
            return os.path.relpath(Path(p), out_dir).replace(os.sep, "/")
        except ValueError:  # anderes Laufwerk
            return Path(p).as_uri()

    stand = fmt_date(sd["date"].max()) if len(sd) else "–"
    n_comp = int(vs["is_complete"].sum()) if len(vs) else 0
    blocks: list[tuple] = [
        H(1, REPORT_TITLE),
        P(f"Stand der Daten: {stand} · erstellt am {_date.today().strftime('%d.%m.%Y')} · "
          f"{n_comp} abgeschlossene Jahrgänge" + (f", Jahrgang {cur} laufend" if cur is not None else "") +
          f" · Einheit {UNIT}"),
        H(2, "Kernaussagen"),
        UL(key_facts(sd, vs, seasonal, overall, cur)),
    ]
    blocks += methodology_blocks(sd, vs, cur)
    blocks += [H(2, "Kennzahlen je Jahrgang"), vintage_table_block(vs, cur),
               P(f"Alle Werte in {UNIT}. * = letzter verfügbarer Wert des laufenden Jahrgangs (kein Verfall). "
                 "Anteil neg. = negative Handelstage / Handelstage des Fensters.")]
    blocks.append(H(2, "Charts"))
    if "dashboard" in chart_paths:
        blocks.append(P(f"Interaktive Version aller Charts (Hover, Zoom, Legende zum Ein-/Ausblenden): "
                        f"[{CHART_SPECS['dashboard'][1]}]({rel(chart_paths['dashboard'])})"))
    for key in CHART_ORDER + [k for k in chart_paths if k not in CHART_ORDER and k != "dashboard"]:
        if key not in chart_paths:
            continue
        title = CHART_SPECS.get(key, ("", key))[1] or key
        blocks.append(H(3, title))
        blocks.append(IMG(rel(chart_paths[key]), title, CHART_CAPTIONS.get(key, "")))
    blocks += data_quality_blocks(sd, vs, validation_notes)
    return blocks


def write_report(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None, seasonal: dict | None,
                 overall: dict | None, chart_paths: dict | None, out_dir: Path | str,
                 validation_notes: str | None = None) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    blocks = build_blocks(spread_daily, vintage_stats, seasonal, overall, chart_paths, out_dir, validation_notes)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"
    md_path.write_text(render_markdown(blocks), encoding="utf-8")
    html_path.write_text(render_html(blocks, REPORT_TITLE), encoding="utf-8")
    return {"report_md": md_path, "report_html": html_path}


if __name__ == "__main__":  # Dev-Treiber: Fixture -> Charts -> Bericht nach output/_dev/
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _tests = config.PROJECT_ROOT / "tests"
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
    from fixtures_charts import make_fixture_frames, make_overall  # type: ignore

    from spread.charts import make_all_charts  # type: ignore

    _sd, _vs, _seas = make_fixture_frames()
    _overall = make_overall(_sd, _vs)
    _out = config.OUTPUT_DIR / "_dev"
    _charts = make_all_charts(_sd, _vs, _seas, _out)
    _notes = ("Fixture-Daten (kein Bloomberg-Export).\n"
              "- Handelstage = Mo–Fr ohne Feiertagskalender.\n"
              "- Verfall = letzter Wochentag im Mai (nominell).")
    _paths = write_report(_sd, _vs, _seas, _overall, _charts, _out, validation_notes=_notes)
    for _k, _p in _paths.items():
        print(f"{_k:12s} {_p}  ({_p.stat().st_size / 1024:,.0f} KB)")
