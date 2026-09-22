"""
Visualisierung der RB/HO Jun-vs-Dec Crack-Spread-Analyse.

Oeffentliche API
----------------
    make_all_charts(spread_daily, vintage_stats, seasonal, out_dir) -> dict[str, Path]
        Schreibt fuenf statische PNGs (matplotlib, Agg, 160 dpi) und ein
        selbsttragendes interaktives ``dashboard.html`` (plotly, plotly.js inline).

    plot_chain(...), plot_seasonal(...), plot_vintage_range(...),
    plot_negative_days(...), plot_heatmap(...)   -> matplotlib.figure.Figure
    build_dashboard(...)                          -> Path

Alle Eingaben folgen dem Datenvertrag in config.py (SPREAD_DAILY_COLUMNS,
VINTAGE_STATS_COLUMNS, seasonal-Tabellen). Die Module load/vintages/stats werden
hier bewusst NICHT importiert.

Gestaltungsentscheidungen (dataviz-Skill, neutrale Referenzpalette)
-------------------------------------------------------------------
* Farben tragen genau eine Bedeutung im gesamten System:
    - Blau  (#2a78d6)  = Historie / abgeschlossene Jahrgaenge
    - Orange(#eb6834)  = laufender Jahrgang (CURRENT_VINTAGE)
    - Rot   (#e34948)  = negativer Pol (Minimum-Marker, Bereich < 0, Jahrgang mit
                          negativem Verfallswert, negativer Arm der Heatmap)
    - Tinte (#0b0b0b)  = Median ueber die abgeschlossenen Jahrgaenge
    - Grau             = Spanne Min-Max, Gitter, Achsen, Hintergrundbaender
  Paare wurden mit scripts/validate_palette.py geprueft (blau/orange, blau/rot:
  PASS; 5-stufige Blau-Rampe als Ordinalrampe: PASS).
* 14 abgeschlossene Jahrgaenge sind zu viele fuer eine kategoriale Palette
  (max. 8). Im saisonalen Overlay tragen daher direkte Endbeschriftungen die
  Identitaet; die Farbe (5-stufige Blau-Rampe, hell = alt, dunkel = jung) ist nur
  ein grober "Aera"-Hinweis fuer Gruppen von ~3 Jahrgaengen.
* Laufender Jahrgang und tdays_to_expiry: der Verfall von 2027 ist noch nicht
  bekannt, daher laesst sich 2027 nicht auf der Achse "Handelstage bis Verfall"
  platzieren. Entscheidung: zwei Panels. Links die abgeschlossenen Jahrgaenge auf
  tdays_to_expiry (umgekehrt, Verfall rechts), rechts der laufende Jahrgang auf
  tday_index (Handelstage seit Fensterbeginn) gegen Median und Min-Max-Spanne
  der abgeschlossenen Jahrgaenge auf derselben tday_index-Achse.
* Die Kette wird an den Jahrgangsgrenzen unterbrochen (kein Verbindungsstrich
  zwischen 31. Mai und 1. Juni): das sind verschiedene Kontraktpaare.
"""
from __future__ import annotations

import math
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import ticker  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm, to_rgb  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402

if __package__:  # Paketkontext (from spread import charts)
    from . import config
else:  # Skriptkontext (py src/spread/charts.py)
    _SRC = Path(__file__).resolve().parents[1]
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from spread import config  # type: ignore

# ----------------------------------------------------------------------------
# Palette (dataviz-Referenzpalette, light mode) und Stil
# ----------------------------------------------------------------------------
PALETTE = {
    "surface": "#fcfcfb",
    "page": "#f9f9f7",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "band": "#f0efec",        # alternierende Jahrgangsbaender / neutraler Mittelpunkt
    "blue": "#2a78d6",        # Historie
    "blue_light": "#b7d3f6",  # Spannen-Balken
    "orange": "#eb6834",      # laufender Jahrgang
    "red": "#e34948",         # negativer Pol
    "red_light": "#f2b4b3",   # abgeleitet: rot 40 % auf surface
    "red_dark": "#8d302f",    # abgeleitet: rot 60 % + ink 40 %
    "blue_dark": "#0d366b",
}
ERA_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]   # validierte Ordinalrampe
DIVERGING_STOPS = [PALETTE["red_dark"], PALETTE["red"], PALETTE["red_light"], PALETTE["band"],
                   PALETTE["blue_light"], PALETTE["blue"], PALETTE["blue_dark"]]
DIVERGING_CMAP = LinearSegmentedColormap.from_list("rbho_diverging", DIVERGING_STOPS)

FONT_STACK = ["Segoe UI", "DejaVu Sans", "Arial", "sans-serif"]
OUTPUT_DPI = 160
RC = {
    "figure.facecolor": PALETTE["surface"],
    "figure.dpi": OUTPUT_DPI,     # = savefig-dpi, damit Pixelmasse/Messungen dem Output entsprechen
    "savefig.dpi": OUTPUT_DPI,
    "savefig.facecolor": PALETTE["surface"],
    "axes.facecolor": PALETTE["surface"],
    "font.family": "sans-serif",
    "font.sans-serif": FONT_STACK,
    "font.size": 9,
    "text.color": PALETTE["ink"],
    "axes.titlesize": 9.5,
    "axes.titlecolor": PALETTE["ink2"],
    "axes.titlelocation": "left",
    "axes.titlepad": 10,
    "axes.labelsize": 9,
    "axes.labelcolor": PALETTE["ink2"],
    "axes.edgecolor": PALETTE["axis"],
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": PALETTE["grid"],
    "grid.linewidth": 0.8,
    "grid.linestyle": "-",
    "xtick.color": PALETTE["muted"],
    "ytick.color": PALETTE["muted"],
    "xtick.labelcolor": PALETTE["ink2"],
    "ytick.labelcolor": PALETTE["ink2"],
    "xtick.labelsize": 8.5,
    "ytick.labelsize": 8.5,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "xtick.major.pad": 5,
    "ytick.major.pad": 5,
    "legend.frameon": False,
    "legend.fontsize": 8.5,
    "legend.handlelength": 1.6,
    "axes.unicode_minus": True,
    "lines.solid_capstyle": "round",
    "lines.solid_joinstyle": "round",
}

MONTHS_DE = {1: "Jan", 2: "Feb", 3: "Mär", 4: "Apr", 5: "Mai", 6: "Jun",
             7: "Jul", 8: "Aug", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Dez"}
WINDOW_MONTHS = [6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5]
UNIT = config.UNIT_LABEL
SOURCE_NOTE = "Quelle: Bloomberg PX_LAST (NYMEX RBOB/ULSD), nur Tage mit allen vier Beinen"

# Dateinamen und deutsche Titel (auch von report.py genutzt)
CHART_SPECS = {
    "chain": ("chart_chain.png", "Durchgehende Kette seit Juni 2012"),
    "seasonal": ("chart_seasonal.png", "Saisonaler Vergleich der Jahrgänge"),
    "vintage_range": ("chart_vintage_range.png", "Spanne je Jahrgang: Start, Minimum, Verfall"),
    "negative_days": ("chart_negative_days.png", "Negative Handelstage je Jahrgang"),
    "heatmap": ("chart_heatmap.png", "Monatsmittel je Jahrgang"),
    "dashboard": ("dashboard.html", "Interaktives Dashboard"),
}


# ----------------------------------------------------------------------------
# Formatierung (deutsch)
# ----------------------------------------------------------------------------
def fmt_de(x, nd: int = 2, strip: bool = False, unicode_minus: bool = True) -> str:
    """1234.5 -> '1.234,50'; NaN -> '–'."""
    try:
        if x is None or pd.isna(x):
            return "–"
    except (TypeError, ValueError):
        pass
    s = f"{float(x):,.{nd}f}"
    if strip and "." in s:
        s = s.rstrip("0").rstrip(".")
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    if unicode_minus:
        s = s.replace("-", "−")
    return s


def fmt_date(x) -> str:
    try:
        if x is None or pd.isna(x):
            return "–"
        return pd.Timestamp(x).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(x)


def _de_formatter(nd: int | None = None) -> ticker.FuncFormatter:
    if nd is None:
        return ticker.FuncFormatter(lambda v, _p: fmt_de(v, 2, strip=True))
    return ticker.FuncFormatter(lambda v, _p: fmt_de(v, nd))


def _int_formatter() -> ticker.FuncFormatter:
    return ticker.FuncFormatter(lambda v, _p: fmt_de(v, 0))


# ----------------------------------------------------------------------------
# Datenvorbereitung (defensiv gegen dtype-Varianten und NaN)
# ----------------------------------------------------------------------------
def _prep_daily(spread_daily: pd.DataFrame) -> pd.DataFrame:
    df = spread_daily.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["date"])
    df["vintage"] = pd.to_numeric(df["vintage"]).astype("int64")
    df["spread_bbl"] = pd.to_numeric(df["spread_bbl"], errors="coerce").astype(float)
    if "is_current" not in df.columns:
        df["is_current"] = False
    df["is_current"] = df["is_current"].map(lambda b: bool(b) if not pd.isna(b) else False).astype(bool)
    for col in ("tdays_to_expiry", "tday_index"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        else:
            df[col] = np.nan
    if "expiry_date" in df.columns:
        df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    else:
        df["expiry_date"] = pd.NaT
    df = df.sort_values("date").reset_index(drop=True)
    if df["tday_index"].isna().any():
        df["tday_index"] = df.groupby("vintage").cumcount().astype(float)
    return df


def _stats_from_daily(sd: pd.DataFrame, cur: int | None) -> pd.DataFrame:
    """Minimal-Statistik pro Jahrgang, falls vintage_stats fehlt."""
    rows = []
    for v, g in sd.groupby("vintage", sort=True):
        s = g["spread_bbl"]
        ok = s.notna()
        if not ok.any():
            continue
        imin, imax = s.idxmin(), s.idxmax()
        neg = (s < 0)
        rows.append({
            "vintage": int(v), "window_start": g["date"].iloc[0], "window_end": g["date"].iloc[-1],
            "is_complete": int(v) != cur, "n_days": int(ok.sum()),
            "first_value": float(s[ok].iloc[0]), "last_value": float(s[ok].iloc[-1]),
            "min": float(s[imin]), "min_date": g.loc[imin, "date"],
            "max": float(s[imax]), "max_date": g.loc[imax, "date"],
            "n_negative_days": int(neg.sum()), "pct_negative_days": float(100 * neg.sum() / max(ok.sum(), 1)),
        })
    return pd.DataFrame(rows)


def _prep_stats(vintage_stats: pd.DataFrame | None, sd: pd.DataFrame, cur: int | None) -> pd.DataFrame:
    if vintage_stats is None or len(vintage_stats) == 0:
        vs = _stats_from_daily(sd, cur)
    else:
        vs = vintage_stats.copy()
    vs["vintage"] = pd.to_numeric(vs["vintage"]).astype("int64")
    for col in ("window_start", "window_end", "min_date", "max_date", "first_negative_date"):
        if col in vs.columns:
            vs[col] = pd.to_datetime(vs[col], errors="coerce")
    for col in ("first_value", "last_value", "min", "max", "n_negative_days", "pct_negative_days", "n_days",
                "mean", "median", "std", "longest_negative_streak", "max_drawdown",
                "tdays_to_expiry_at_min", "tdays_to_expiry_at_max"):
        if col in vs.columns:
            vs[col] = pd.to_numeric(vs[col], errors="coerce").astype(float)
    if "is_complete" not in vs.columns:
        vs["is_complete"] = vs["vintage"] != cur
    vs["is_complete"] = vs["is_complete"].map(lambda b: bool(b) if not pd.isna(b) else True).astype(bool)
    if cur is not None:
        vs.loc[vs["vintage"] == cur, "is_complete"] = False
    return vs.sort_values("vintage").reset_index(drop=True)


def _current_vintage(sd: pd.DataFrame, vintage_stats: pd.DataFrame | None = None) -> int | None:
    cur = sd.loc[sd["is_current"], "vintage"]
    if len(cur):
        return int(cur.iloc[0])
    if vintage_stats is not None and len(vintage_stats) and "is_complete" in vintage_stats.columns:
        inc = vintage_stats.loc[~vintage_stats["is_complete"].astype(bool), "vintage"]
        if len(inc):
            return int(inc.iloc[-1])
    return None


def _vintage_columns(table: pd.DataFrame) -> dict[int, object]:
    out = {}
    for c in table.columns:
        s = str(c)
        if s.isdigit() and len(s) == 4:
            out[int(s)] = c
    return out


def _seasonal_from_daily(sd: pd.DataFrame, key: str, cur: int | None) -> pd.DataFrame:
    if key == "by_tdays_to_expiry":
        base = sd[(sd["vintage"] != cur) & sd["tdays_to_expiry"].notna()]
        idx = "tdays_to_expiry"
    else:
        base = sd[sd["tday_index"].notna()]
        idx = "tday_index"
    if base.empty:
        return pd.DataFrame(columns=config.SEASONAL_AGG_COLUMNS)
    t = base.assign(**{idx: base[idx].astype(int)}).pivot_table(
        index=idx, columns="vintage", values="spread_bbl", aggfunc="first").sort_index()
    t.columns = [int(c) for c in t.columns]
    t.columns.name = None
    return t


def _get_seasonal(seasonal: dict | None, key: str, sd: pd.DataFrame, cur: int | None) -> pd.DataFrame:
    t = None
    if isinstance(seasonal, dict):
        t = seasonal.get(key)
    if t is None or len(t) == 0:
        t = _seasonal_from_daily(sd, key, cur)
    t = t.copy()
    t.index = pd.to_numeric(t.index, errors="coerce")
    t = t[~t.index.isna()]
    t.index = t.index.astype(int)
    t = t.sort_index()
    vcols = _vintage_columns(t)
    for v, c in vcols.items():
        t[c] = pd.to_numeric(t[c], errors="coerce").astype(float)
    complete_cols = [c for v, c in vcols.items() if v != cur]
    if any(a not in t.columns for a in config.SEASONAL_AGG_COLUMNS):
        block = t[complete_cols].astype(float) if complete_cols else None
        for a in config.SEASONAL_AGG_COLUMNS:
            if a not in t.columns:
                t[a] = getattr(block, a)(axis=1) if block is not None and block.shape[1] else np.nan
    for a in config.SEASONAL_AGG_COLUMNS:
        t[a] = pd.to_numeric(t[a], errors="coerce").astype(float)
    return t


def _era_colors(vintages: list[int], max_groups: int = 5) -> tuple[dict[int, str], list[tuple[str, str]]]:
    """Ordnet Jahrgaenge in bis zu 5 zusammenhaengende Gruppen (Aeren) der Blau-Rampe."""
    vintages = sorted(int(v) for v in vintages)
    n = len(vintages)
    if n == 0:
        return {}, []
    k = max(1, min(max_groups, n))
    chunks = np.array_split(np.array(vintages), k)
    if k == 1:
        steps = [ERA_RAMP[2]]
    else:
        steps = [ERA_RAMP[i] for i in np.round(np.linspace(0, len(ERA_RAMP) - 1, k)).astype(int)]
    colors, legend = {}, []
    for chunk, col in zip(chunks, steps):
        for v in chunk:
            colors[int(v)] = col
        label = f"{chunk[0]}" if len(chunk) == 1 else f"{chunk[0]}–{chunk[-1]}"
        legend.append((label, col))
    return colors, legend


# ----------------------------------------------------------------------------
# Zeichen-Hilfen
# ----------------------------------------------------------------------------
def _title(ax, title: str, subtitle: str | None = None, pad: float | None = None) -> None:
    """Titel (fett) ueber einem Untertitel (sekundaere Tinte), beide linksbuendig zur Achse."""
    pad = RC["axes.titlepad"] if pad is None else pad
    if subtitle:
        ax.set_title(subtitle, loc="left", pad=pad)
        ax.annotate(title, xy=(0, 1), xycoords="axes fraction", xytext=(0, pad + 16), textcoords="offset points",
                    ha="left", va="baseline", fontsize=12.5, fontweight="bold", color=PALETTE["ink"],
                    annotation_clip=False)
    else:
        ax.set_title(title, loc="left", fontsize=12.5, fontweight="bold", color=PALETTE["ink"], pad=pad)


def _zero_line(ax, axis: str = "y") -> None:
    if axis == "y":
        ax.axhline(0, color=PALETTE["muted"], lw=1.0, zorder=1.6)
    else:
        ax.axvline(0, color=PALETTE["muted"], lw=1.0, zorder=1.6)


def _footer(fig: Figure, text: str = SOURCE_NOTE) -> None:
    """Quellenzeile unten rechts als (layout-bewusste) Figurlegende ohne Handle."""
    fig.legend(handles=[Line2D([], [], color="none")], labels=[text], loc="outside lower right",
               handlelength=0, handletextpad=0, fontsize=7, labelcolor=PALETTE["muted"])


def _row_major(items: list, ncol: int) -> list:
    """matplotlib fuellt Legenden spaltenweise; diese Permutation laesst sie zeilenweise lesbar erscheinen."""
    n = len(items)
    nrows, nlarge = divmod(n, ncol)
    rows_per_col = [nrows + 1] * nlarge + [nrows] * (ncol - nlarge)
    out = []
    for c, rc in enumerate(rows_per_col):
        for r in range(rc):
            out.append(items[r * ncol + c])
    return out


def _bottom_legend(fig: Figure, handles: list, max_frac: float = 0.58, **kw):
    """Figurlegende unten links. Die Spaltenzahl wird so lange reduziert (mehr Zeilen), bis die
    Legende hoechstens max_frac der Figurbreite einnimmt und die Quellenzeile rechts Platz hat."""
    n = len(handles)
    if n == 0:
        return None
    kw.setdefault("columnspacing", 1.4)
    for rows in range(1, n + 1):
        ncol = math.ceil(n / rows)
        leg = fig.legend(handles=_row_major(handles, ncol), loc="outside lower left", ncol=ncol, **kw)
        fig.canvas.draw()
        if leg.get_window_extent().width / fig.bbox.width <= max_frac or ncol == 1:
            return leg
        leg.remove()
    return None


def _css(fig: Figure, px: float) -> float:
    """CSS-Pixel (1/96 Zoll, Masseinheit der dataviz-Markspezifikation) -> Geraetepixel bei fig.dpi."""
    return px * fig.dpi / 96.0


def _px_to_data(ax, px: float) -> tuple[float, float]:
    """Geraetepixel (bei figure.dpi) -> Datenkoordinaten-Delta (dx, dy)."""
    inv = ax.transData.inverted()
    p0 = inv.transform((0.0, 0.0))
    p1 = inv.transform((px, px))
    return abs(p1[0] - p0[0]), abs(p1[1] - p0[1])


def _spread_positions(y_px: np.ndarray, gap: float, lo: float, hi: float) -> np.ndarray:
    """1-D Entzerrung von Labelpositionen (Pixel), begrenzt auf [lo, hi]."""
    n = len(y_px)
    if n == 0:
        return y_px
    order = np.argsort(y_px, kind="stable")
    pos = y_px[order].astype(float).copy()
    if n > 1 and (n - 1) * gap > (hi - lo):
        gap = (hi - lo) / (n - 1)
    for i in range(1, n):
        pos[i] = max(pos[i], pos[i - 1] + gap)
    if pos[-1] > hi:
        pos -= pos[-1] - hi
        for i in range(n - 2, -1, -1):
            pos[i] = min(pos[i], pos[i + 1] - gap)
    if pos[0] < lo:
        pos += lo - pos[0]
        for i in range(1, n):
            pos[i] = max(pos[i], pos[i - 1] + gap)
    out = np.empty(n)
    out[order] = pos
    return out


def _rounded_bar(ax, x_center: float, y_base: float, y_tip: float, width: float,
                 rx: float, ry: float, color: str, zorder: float = 3) -> None:
    """Vertikaler Balken mit 4px-Rundung am Datenende, eckig an der Basislinie."""
    h = y_tip - y_base
    if h == 0 or np.isnan(h):
        return
    s = 1.0 if h > 0 else -1.0
    rx = min(rx, width / 2.0)
    ry = min(ry, abs(h))
    x0, x1 = x_center - width / 2.0, x_center + width / 2.0
    k = 0.5523
    verts = [
        (x0, y_base), (x1, y_base), (x1, y_tip - s * ry),
        (x1, y_tip - s * ry * (1 - k)), (x1 - rx * (1 - k), y_tip), (x1 - rx, y_tip),
        (x0 + rx, y_tip),
        (x0 + rx * (1 - k), y_tip), (x0, y_tip - s * ry * (1 - k)), (x0, y_tip - s * ry),
        (x0, y_base),
    ]
    codes = [MplPath.MOVETO, MplPath.LINETO, MplPath.LINETO,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.LINETO,
             MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
             MplPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color, edgecolor="none", lw=0, zorder=zorder))


def _luminance(color) -> float:
    r, g, b = to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _label_for(v: int, cur: int | None, star: bool = False) -> str:
    if cur is not None and v == cur:
        return f"{v}*" if star else f"{v} (laufend)"
    return str(v)


# ----------------------------------------------------------------------------
# 1) Kette
# ----------------------------------------------------------------------------
def plot_chain(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None = None,
               figsize=(13.0, 5.6)) -> Figure:
    with plt.rc_context(RC):
        sd = _prep_daily(spread_daily)
        cur = _current_vintage(sd, vintage_stats)
        fig, ax = plt.subplots(figsize=figsize, layout="constrained")
        fig.get_layout_engine().set(w_pad=0.08, h_pad=0.12)

        vints = sorted(sd["vintage"].unique())
        if sd.empty or sd["spread_bbl"].notna().sum() == 0:
            ax.text(0.5, 0.5, "Keine Daten", transform=ax.transAxes, ha="center", va="center", color=PALETTE["muted"])
            return fig

        ymin, ymax = float(sd["spread_bbl"].min()), float(sd["spread_bbl"].max())
        yr = max(ymax - ymin, 1.0)
        lo, hi = ymin - 0.24 * yr, ymax + 0.16 * yr
        lo = min(lo, -0.05 * yr)
        hi = max(hi, 0.05 * yr)
        ax.set_ylim(lo, hi)

        d0, d1 = sd["date"].min(), sd["date"].max()
        ax.set_xlim(d0 - pd.Timedelta(days=15), d1 + pd.Timedelta(days=25))

        # Jahrgangsbaender mit Jahreszahl oben
        starts = {v: sd.loc[sd["vintage"] == v, "date"].min() for v in vints}
        for i, v in enumerate(vints):
            x0 = starts[v]
            x1 = starts[vints[i + 1]] if i + 1 < len(vints) else d1 + pd.Timedelta(days=25)
            if i % 2 == 1:
                ax.axvspan(x0, x1, color=PALETTE["band"], lw=0, zorder=0)
            xm = x0 + (x1 - x0) / 2
            ax.text(xm, 0.985, _label_for(v, cur, star=True) if v == cur else str(v),
                    transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=8,
                    color=PALETTE["orange"] if v == cur else PALETTE["muted"],
                    fontweight="bold" if v == cur else "normal")

        _zero_line(ax)

        # Linie je Jahrgang (Unterbrechung an den Jahrgangsgrenzen)
        for v in vints:
            g = sd[sd["vintage"] == v]
            is_cur = (v == cur)
            ax.plot(g["date"].to_numpy(), g["spread_bbl"].to_numpy(),
                    color=PALETTE["orange"] if is_cur else PALETTE["blue"],
                    lw=1.8 if is_cur else 1.25, zorder=3.5 if is_cur else 3)

        # Gesamtminimum
        imin = sd["spread_bbl"].idxmin()
        xmin_d, ymin_v, vmin = sd.loc[imin, "date"], float(sd.loc[imin, "spread_bbl"]), int(sd.loc[imin, "vintage"])
        ax.plot([xmin_d], [ymin_v], marker="D", ms=6, mfc=PALETTE["red"], mec=PALETTE["surface"], mew=1.2,
                lw=0, zorder=5)
        frac = (xmin_d - d0) / (d1 - d0) if d1 > d0 else 0.5
        right = frac > 0.72
        ax.annotate(f"Tiefstwert {fmt_de(ymin_v)} {UNIT}\n{fmt_date(xmin_d)} · Jahrgang {vmin}",
                    xy=(xmin_d, ymin_v), xytext=(-16 if right else 16, -30), textcoords="offset points",
                    ha="right" if right else "left", va="top", fontsize=8, color=PALETTE["ink"],
                    arrowprops=dict(arrowstyle="-", color=PALETTE["muted"], lw=0.8, shrinkA=0, shrinkB=4))

        # Endpunkt des laufenden Jahrgangs am rechten Rand beschriften
        if cur is not None:
            g = sd[(sd["vintage"] == cur) & sd["spread_bbl"].notna()]
            if len(g):
                xl, yl = g["date"].iloc[-1], float(g["spread_bbl"].iloc[-1])
                ax.plot([xl], [yl], marker="o", ms=6, mfc=PALETTE["orange"], mec=PALETTE["surface"], mew=1.2,
                        lw=0, zorder=5)
                ax.annotate(f"{fmt_date(xl)}\n{fmt_de(yl)} {UNIT}", xy=(xl, yl), xycoords="data",
                            xytext=(1.006, yl), textcoords=ax.get_yaxis_transform(), ha="left", va="center",
                            fontsize=7.5, color=PALETTE["ink2"], annotation_clip=False)

        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.yaxis.set_major_formatter(_de_formatter())
        ax.set_ylabel(f"Spread ({UNIT})")
        ax.grid(axis="x", visible=False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_visible(False)

        first_v, last_v = vints[0], vints[-1]
        _title(ax, f"{config.SPREAD_LABEL} – durchgehende Kette",
               f"[(RB Jun − HO Jun) − (RB Dez − HO Dez)] × 42, in {UNIT} · Jahrgänge {first_v}–{last_v} · "
               f"Fenster 1. Juni (Y−1) bis Verfall Ende Mai Y · Bänder = Jahrgänge")
        handles = [Line2D([], [], color=PALETTE["blue"], lw=1.5, label="Abgeschlossene Jahrgänge")]
        if cur is not None:
            handles.append(Line2D([], [], color=PALETTE["orange"], lw=2, label=f"Jahrgang {cur} (laufend, * im Band)"))
        handles.append(Line2D([], [], marker="D", color="none", mfc=PALETTE["red"], mec=PALETTE["surface"], ms=6,
                              label="Gesamtminimum"))
        handles.append(Line2D([], [], color=PALETTE["muted"], lw=1, label="Nulllinie"))
        _bottom_legend(fig, handles)
        _footer(fig)
        return fig


# ----------------------------------------------------------------------------
# 2) Saisonaler Vergleich (zwei Panels)
# ----------------------------------------------------------------------------
def plot_seasonal(spread_daily: pd.DataFrame, seasonal: dict | None = None,
                  vintage_stats: pd.DataFrame | None = None, figsize=(14.5, 6.4)) -> Figure:
    with plt.rc_context(RC):
        sd = _prep_daily(spread_daily)
        cur = _current_vintage(sd, vintage_stats)
        by_td = _get_seasonal(seasonal, "by_tdays_to_expiry", sd, cur)
        by_ti = _get_seasonal(seasonal, "by_tday_index", sd, cur)
        vcols_td = {v: c for v, c in _vintage_columns(by_td).items() if v != cur}
        complete = sorted(vcols_td)
        colors, era_legend = _era_colors(complete)

        fig, (axl, axr) = plt.subplots(1, 2, figsize=figsize, sharey=True, layout="constrained",
                                       gridspec_kw={"width_ratios": [3.0, 1.6], "wspace": 0.16})
        fig.get_layout_engine().set(w_pad=0.08, h_pad=0.12)

        # ---------- links: abgeschlossene Jahrgaenge auf tdays_to_expiry ----------
        n_td = int(by_td.index.max()) if len(by_td) else 260
        axl.set_xlim(n_td, 0)
        axl.xaxis.set_major_locator(ticker.MultipleLocator(50 if n_td > 150 else 20))
        axl.xaxis.set_major_formatter(_int_formatter())
        axl.set_xlabel("Handelstage bis Verfall (0 = letzter Handelstag des Jun-Kontrakts)")
        axl.set_ylabel(f"Spread ({UNIT})")
        _zero_line(axl)
        if complete and len(by_td):
            x = by_td.index.to_numpy()
            if by_td["min"].notna().any():
                axl.fill_between(x, by_td["min"].to_numpy(), by_td["max"].to_numpy(), color=PALETTE["axis"],
                                 alpha=0.28, lw=0, zorder=1)
            for v in complete:
                axl.plot(x, by_td[vcols_td[v]].to_numpy(), color=colors[v], lw=1.0, zorder=3)
            if by_td["median"].notna().any():
                axl.plot(x, by_td["median"].to_numpy(), color=PALETTE["ink"], lw=2.2, zorder=4)
        else:
            axl.text(0.5, 0.5, "Keine abgeschlossenen Jahrgänge in den Daten", transform=axl.transAxes,
                     ha="center", va="center", color=PALETTE["muted"])

        # ---------- rechts: laufender Jahrgang auf tday_index ----------
        vcols_ti = _vintage_columns(by_ti)
        n_ti = int(by_ti.index.max()) if len(by_ti) else 260
        axr.set_xlim(0, n_ti)
        axr.xaxis.set_major_locator(ticker.MultipleLocator(50 if n_ti > 150 else 20))
        axr.xaxis.set_major_formatter(_int_formatter())
        axr.set_xlabel("Handelstage seit Fensterbeginn (0 = 1. Juni)")
        _zero_line(axr)
        if len(by_ti) and by_ti["min"].notna().any():
            xi = by_ti.index.to_numpy()
            axr.fill_between(xi, by_ti["min"].to_numpy(), by_ti["max"].to_numpy(), color=PALETTE["axis"],
                             alpha=0.28, lw=0, zorder=1)
            axr.plot(xi, by_ti["median"].to_numpy(), color=PALETTE["ink"], lw=2.2, zorder=4)
        cur_last = None
        if cur is not None and cur in vcols_ti:
            ycur = by_ti[vcols_ti[cur]]
            ok = ycur.notna()
            if ok.any():
                axr.plot(by_ti.index[ok].to_numpy(), ycur[ok].to_numpy(), color=PALETTE["orange"], lw=2.4, zorder=5)
                ti_last = int(by_ti.index[ok][-1])
                y_last = float(ycur[ok].iloc[-1])
                g = sd[(sd["vintage"] == cur) & sd["spread_bbl"].notna()]
                d_last = g["date"].iloc[-1] if len(g) else None
                cur_last = (ti_last, y_last, d_last)
                axr.plot([ti_last], [y_last], marker="o", ms=6.5, mfc=PALETTE["orange"], mec=PALETTE["surface"],
                         mew=1.2, lw=0, zorder=6)
                med_here = float(by_ti.loc[ti_last, "median"]) if ti_last in by_ti.index else float("nan")
                # kleines Direktlabel am Linienende; die Zahlen stehen im Eckkasten oben rechts (dort ist es leer,
                # weil der Spread zum Verfall hin typischerweise tiefer liegt)
                axr.annotate(str(cur), xy=(ti_last, y_last), xytext=(6, 0), textcoords="offset points",
                             ha="left", va="center", fontsize=8, fontweight="bold", color=PALETTE["ink"])
                box = [f"Tag {ti_last}" + (f" · {fmt_date(d_last)}" if d_last is not None else ""),
                       f"{cur}: {fmt_de(y_last)} {UNIT}"]
                if not np.isnan(med_here):
                    axr.plot([ti_last], [med_here], marker="o", ms=5.5, mfc=PALETTE["ink"], mec=PALETTE["surface"],
                             mew=1.2, lw=0, zorder=6)
                    diff = y_last - med_here
                    box += [f"Median: {fmt_de(med_here)} {UNIT}",
                            f"Differenz: {'+' if diff >= 0 else ''}{fmt_de(diff)} {UNIT}"]
                axr.text(0.98, 0.97, "\n".join(box), transform=axr.transAxes, ha="right", va="top", fontsize=8,
                         color=PALETTE["ink"], linespacing=1.35,
                         bbox=dict(boxstyle="round,pad=0.45", facecolor=PALETTE["surface"], edgecolor=PALETTE["grid"],
                                   lw=0.8))
        else:
            axr.text(0.5, 0.94, "Kein laufender Jahrgang in den Daten", transform=axr.transAxes, ha="center",
                     va="top", fontsize=8.5, color=PALETTE["muted"])

        # y-Grenzen mit Luft
        allv = pd.concat([sd["spread_bbl"]]).dropna()
        if len(allv):
            ymin, ymax = float(allv.min()), float(allv.max())
            yr = max(ymax - ymin, 1.0)
            axl.set_ylim(min(ymin - 0.08 * yr, -0.05 * yr), max(ymax + 0.10 * yr, 0.05 * yr))
        axl.yaxis.set_major_formatter(_de_formatter())
        for a in (axl, axr):
            a.grid(axis="x", visible=False)
            a.spines["left"].set_visible(False)
            a.spines["bottom"].set_visible(False)

        if complete:
            span = f"{complete[0]}–{complete[-1]}" if len(complete) > 1 else f"{complete[0]}"
            axl.set_title(f"Abgeschlossene Jahrgänge {span}, Verfall rechts (x = 0)", loc="left")
            axr.set_title((f"Laufender Jahrgang {cur} vs. Median/Spanne {span}" if cur is not None
                           else f"Median/Spanne {span}"), loc="left")
        else:
            axl.set_title("Keine abgeschlossenen Jahrgänge in den Daten", loc="left")
            axr.set_title(f"Laufender Jahrgang {cur}" if cur is not None else "Kein laufender Jahrgang", loc="left")
        axl.annotate(f"{config.SPREAD_LABEL} – saisonaler Verlauf der Jahrgänge", xy=(0, 1), xycoords="axes fraction",
                     xytext=(0, 26), textcoords="offset points", ha="left", va="baseline", fontsize=12.5,
                     fontweight="bold", color=PALETTE["ink"], annotation_clip=False)

        # Endbeschriftung der Jahrgaenge (rechter Rand des linken Panels) mit Leitlinien
        if complete and len(by_td):
            fig.canvas.draw()
            ends = []
            for v in complete:
                s = by_td[vcols_td[v]].dropna()
                if len(s):
                    ends.append((v, float(s.iloc[0])))    # kleinster Index = Verfallstag (Index aufsteigend)
            if ends:
                bbox = axl.get_window_extent()
                y_px = np.array([axl.transData.transform((0, y))[1] for _, y in ends])
                y_lab_px = _spread_positions(y_px, gap=_css(fig, 11.5), lo=bbox.y0 + _css(fig, 6),
                                             hi=bbox.y1 - _css(fig, 6))
                inv = axl.transData.inverted()
                for (v, y_end), yp in zip(ends, y_lab_px):
                    y_lab = inv.transform((0, yp))[1]
                    axl.annotate("", xy=(0, y_end), xycoords="data", xytext=(1.012, y_lab),
                                 textcoords=axl.get_yaxis_transform(),
                                 arrowprops=dict(arrowstyle="-", color=colors[v], lw=0.7, shrinkA=0, shrinkB=0),
                                 annotation_clip=False)
                    axl.text(1.018, y_lab, str(v), transform=axl.get_yaxis_transform(), ha="left", va="center",
                             fontsize=7.5, color=PALETTE["ink2"])

        handles = [Line2D([], [], color=c, lw=1.4, label=("Jahrgänge " if "–" in lab else "Jahrgang ") + lab)
                   for lab, c in era_legend]
        if complete:
            handles += [Line2D([], [], color=PALETTE["ink"], lw=2.2, label="Median (abgeschlossene Jahrgänge)"),
                        Patch(facecolor=PALETTE["axis"], alpha=0.28, label="Spanne Min–Max")]
        if cur_last is not None:
            handles.append(Line2D([], [], color=PALETTE["orange"], lw=2.4, label=f"{cur} (laufend)"))
        _bottom_legend(fig, handles)
        _footer(fig)
        return fig


# ----------------------------------------------------------------------------
# 3) Spanne je Jahrgang
# ----------------------------------------------------------------------------
def plot_vintage_range(vintage_stats: pd.DataFrame | None, spread_daily: pd.DataFrame | None = None,
                       figsize=None) -> Figure:
    with plt.rc_context(RC):
        sd = _prep_daily(spread_daily) if spread_daily is not None else None
        cur = _current_vintage(sd, vintage_stats) if sd is not None else _current_vintage(
            pd.DataFrame({"vintage": [], "is_current": [], "date": [], "spread_bbl": []}), vintage_stats)
        vs = _prep_stats(vintage_stats, sd if sd is not None else pd.DataFrame(), cur)
        n = len(vs)
        if figsize is None:
            figsize = (11.0, 2.4 + 0.36 * max(n, 1))
        fig, ax = plt.subplots(figsize=figsize, layout="constrained")
        fig.get_layout_engine().set(w_pad=0.08, h_pad=0.12)
        if n == 0:
            ax.text(0.5, 0.5, "Keine Daten", transform=ax.transAxes, ha="center", va="center", color=PALETTE["muted"])
            return fig

        ys = np.arange(n)
        xmin_all = float(np.nanmin(vs["min"])) if vs["min"].notna().any() else -1.0
        xmax_all = float(np.nanmax(vs["max"])) if vs["max"].notna().any() else 1.0
        xr = max(xmax_all - xmin_all, 1.0)
        x_lo = min(xmin_all - 0.16 * xr, -0.04 * xr)
        x_hi = max(xmax_all + 0.06 * xr, 0.04 * xr)
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(n - 0.5, -0.5)

        if x_lo < 0:
            ax.axvspan(x_lo, 0, color=PALETTE["red"], alpha=0.06, lw=0, zorder=0)
            ax.text(x_lo + 0.01 * xr, n - 0.52, "Spread < 0", ha="left", va="bottom", fontsize=7.5,
                    color=PALETTE["red_dark"])
        _zero_line(ax, axis="x")

        for y, row in zip(ys, vs.itertuples(index=False)):
            v = int(row.vintage)
            is_cur = (cur is not None and v == cur) or (not bool(row.is_complete))
            mn, mx = row.min, row.max
            if not (np.isnan(mn) or np.isnan(mx)):
                ax.plot([mn, mx], [y, y], color=PALETTE["blue_light"], lw=3.6, solid_capstyle="round", zorder=2)
                ax.text(mn - 0.012 * xr, y, fmt_de(mn), ha="right", va="center", fontsize=7.5, color=PALETTE["ink2"])
            if not np.isnan(row.first_value):
                ax.plot([row.first_value], [y], marker="o", ms=6.5, mfc=PALETTE["surface"], mec=PALETTE["blue"],
                        mew=1.6, lw=0, zorder=4)
            if not np.isnan(row.last_value):
                ax.plot([row.last_value], [y], marker="o", ms=6.5, mfc=PALETTE["orange"] if is_cur else PALETTE["blue"],
                        mec=PALETTE["surface"], mew=1.2, lw=0, zorder=5)
            if not np.isnan(mn):
                ax.plot([mn], [y], marker="D", ms=5.5, mfc=PALETTE["red"], mec=PALETTE["surface"], mew=1.2, lw=0,
                        zorder=6)

        ax.set_yticks(ys)
        ax.set_yticklabels([_label_for(int(v), cur) for v in vs["vintage"]])
        ax.xaxis.set_major_formatter(_de_formatter())
        ax.set_xlabel(f"Spread ({UNIT})")
        ax.grid(axis="y", visible=False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_visible(False)

        _title(ax, f"{config.SPREAD_LABEL} – Spanne je Jahrgang",
               "Balken = Min–Max des Jahrgangs · Zahl links = Minimum · Kreise = Start / Verfall · Raute = Minimum")
        handles = [Line2D([], [], color=PALETTE["blue_light"], lw=3.6, label="Spanne Min–Max"),
                   Line2D([], [], marker="o", color="none", mfc=PALETTE["surface"], mec=PALETTE["blue"], mew=1.6, ms=6.5,
                          label="Startwert (1. Handelstag)"),
                   Line2D([], [], marker="o", color="none", mfc=PALETTE["blue"], mec=PALETTE["surface"], ms=6.5,
                          label="Wert bei Verfall"),
                   Line2D([], [], marker="D", color="none", mfc=PALETTE["red"], mec=PALETTE["surface"], ms=5.5,
                          label="Minimum")]
        if cur is not None and (vs["vintage"] == cur).any():
            handles.append(Line2D([], [], marker="o", color="none", mfc=PALETTE["orange"], mec=PALETTE["surface"], ms=6.5,
                                  label=f"{cur}: aktueller Wert (laufend)"))
        _bottom_legend(fig, handles)
        _footer(fig)
        return fig


# ----------------------------------------------------------------------------
# 4) Negative Handelstage
# ----------------------------------------------------------------------------
def plot_negative_days(vintage_stats: pd.DataFrame | None, spread_daily: pd.DataFrame | None = None,
                       figsize=(11.0, 4.8)) -> Figure:
    with plt.rc_context(RC):
        sd = _prep_daily(spread_daily) if spread_daily is not None else None
        cur = _current_vintage(sd, vintage_stats) if sd is not None else _current_vintage(
            pd.DataFrame({"vintage": [], "is_current": [], "date": [], "spread_bbl": []}), vintage_stats)
        vs = _prep_stats(vintage_stats, sd if sd is not None else pd.DataFrame(), cur)
        fig, ax = plt.subplots(figsize=figsize, layout="constrained")
        fig.get_layout_engine().set(w_pad=0.08, h_pad=0.12)
        n = len(vs)
        if n == 0:
            ax.text(0.5, 0.5, "Keine Daten", transform=ax.transAxes, ha="center", va="center", color=PALETTE["muted"])
            return fig

        xs = np.arange(n)
        nneg = vs["n_negative_days"].fillna(0).to_numpy(dtype=float)
        ndays = vs["n_days"].to_numpy(dtype=float) if "n_days" in vs.columns else np.full(n, np.nan)
        pct_col = vs["pct_negative_days"].to_numpy(dtype=float) if "pct_negative_days" in vs.columns else np.full(n, np.nan)
        pct = np.where(ndays > 0, 100.0 * nneg / np.where(ndays > 0, ndays, 1), pct_col)
        ended_neg = (vs["last_value"] < 0).to_numpy()

        top = max(float(np.nanmax(nneg)) if n else 0.0, 4.0)
        ax.set_xlim(-0.6, n - 0.4)
        ax.set_ylim(0, top * 1.28)
        ax.spines["bottom"].set_visible(True)
        ax.spines["bottom"].set_color(PALETTE["muted"])
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", visible=False)

        fig.canvas.draw()
        dx, dy = _px_to_data(ax, 1.0)
        bar_w, rx, ry = _css(fig, 22) * dx, _css(fig, 4) * dx, _css(fig, 4) * dy   # <= 24px dick, 4px Rundung
        for x, h, neg_end, v in zip(xs, nneg, ended_neg, vs["vintage"]):
            color = PALETTE["red"] if neg_end else PALETTE["blue"]
            _rounded_bar(ax, float(x), 0.0, float(h), bar_w, rx, ry, color)
            label = fmt_de(h, 0) if h <= 0 or np.isnan(pct[x]) else f"{fmt_de(h, 0)} ({fmt_de(pct[x], 1)} %)"
            ax.text(x, h + 0.02 * top, label, ha="center", va="bottom", fontsize=8, color=PALETTE["ink2"])

        ax.set_xticks(xs)
        ax.set_xticklabels([_label_for(int(v), cur, star=True) for v in vs["vintage"]])
        ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True, nbins=6))
        ax.yaxis.set_major_formatter(_int_formatter())
        ax.set_ylabel("Handelstage mit Spread < 0")

        n_neg_v = int((nneg > 0).sum())
        sub = f"Anzahl Handelstage mit Spread < 0 je Jahrgang (Anteil in %) · {n_neg_v} von {n} Jahrgängen mit mindestens einem negativen Tag"
        if not ended_neg.any():
            sub += " · kein Jahrgang endete negativ"
        _title(ax, f"{config.SPREAD_LABEL} – negative Handelstage je Jahrgang", sub)
        handles = [Patch(facecolor=PALETTE["blue"], label="Wert bei Verfall ≥ 0")]
        if ended_neg.any():
            handles.append(Patch(facecolor=PALETTE["red"], label="Wert bei Verfall < 0 (Jahrgang endete negativ)"))
        if cur is not None and (vs["vintage"] == cur).any():
            g = sd[sd["vintage"] == cur] if sd is not None else None
            stand = f", Stand {fmt_date(g['date'].max())}" if g is not None and len(g) else ""
            handles.append(Line2D([], [], color="none", label=f"* laufender Jahrgang{stand}"))
        _bottom_legend(fig, handles, handlelength=1.2)
        _footer(fig)
        return fig


# ----------------------------------------------------------------------------
# 5) Heatmap Jahrgang x Monat
# ----------------------------------------------------------------------------
def monthly_matrix(spread_daily: pd.DataFrame) -> pd.DataFrame:
    """Mittel von spread_bbl je (Jahrgang, Kalendermonat), Spalten in Fensterreihenfolge Jun..Mai."""
    sd = _prep_daily(spread_daily)
    m = sd.assign(month=sd["date"].dt.month).pivot_table(index="vintage", columns="month", values="spread_bbl",
                                                          aggfunc="mean")
    m = m.reindex(columns=WINDOW_MONTHS).sort_index()
    m.columns.name = "month"
    return m


def plot_heatmap(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None = None, figsize=None) -> Figure:
    with plt.rc_context(RC):
        sd = _prep_daily(spread_daily)
        cur = _current_vintage(sd, vintage_stats)
        m = monthly_matrix(sd)
        n = len(m)
        if figsize is None:
            figsize = (11.5, 2.3 + 0.40 * max(n, 1))
        fig, ax = plt.subplots(figsize=figsize, layout="constrained")
        fig.get_layout_engine().set(w_pad=0.08, h_pad=0.12)
        if n == 0 or m.notna().sum().sum() == 0:
            ax.text(0.5, 0.5, "Keine Daten", transform=ax.transAxes, ha="center", va="center", color=PALETTE["muted"])
            return fig

        z = m.to_numpy(dtype=float)
        zmin = float(np.nanmin(z))
        zmax = float(np.nanmax(z))
        lim = max(abs(zmin), abs(zmax), 0.5)
        norm = TwoSlopeNorm(vcenter=0.0, vmin=-lim, vmax=lim)  # symmetrisch, damit gleiche Farbe = gleicher Betrag
        im = ax.imshow(z, cmap=DIVERGING_CMAP, norm=norm, aspect="auto", interpolation="nearest")
        ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(False)

        # 2px-Oberflaechenluecken zwischen den Zellen
        for k in range(1, z.shape[1]):
            ax.axvline(k - 0.5, color=PALETTE["surface"], lw=0.9)
        for k in range(1, z.shape[0]):
            ax.axhline(k - 0.5, color=PALETTE["surface"], lw=0.9)
        ax.axvline(6.5, color=PALETTE["ink2"], lw=1.2)  # Jahreswechsel Dez -> Jan

        for i in range(z.shape[0]):
            for j in range(z.shape[1]):
                val = z[i, j]
                if np.isnan(val):
                    ax.text(j, i, "–", ha="center", va="center", fontsize=7.5, color=PALETTE["muted"])
                    continue
                cell = DIVERGING_CMAP(norm(val))
                ax.text(j, i, fmt_de(val, 2), ha="center", va="center", fontsize=7.6,
                        color="#ffffff" if _luminance(cell[:3]) < 0.45 else PALETTE["ink"])

        ax.set_xticks(range(len(WINDOW_MONTHS)))
        ax.set_xticklabels([MONTHS_DE[k] for k in WINDOW_MONTHS])
        ax.set_yticks(range(n))
        ax.set_yticklabels([_label_for(int(v), cur, star=True) for v in m.index])
        ax.tick_params(axis="x", labeltop=False, labelbottom=True)
        ax.text(3.0, -0.5, "Vorjahr (Y−1)", transform=ax.transData, ha="center", va="bottom", fontsize=8,
                color=PALETTE["ink2"])
        ax.text(9.0, -0.5, "Jahrgang (Y)", transform=ax.transData, ha="center", va="bottom", fontsize=8,
                color=PALETTE["ink2"])
        ax.set_ylim(n - 0.5, -0.5)
        ax.set_ylabel("Jahrgang")
        ax.set_xlabel("Kalendermonat im Fenster (Juni des Vorjahres bis Mai des Jahrgangs)")

        cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
        cb.set_label(f"Ø Spread ({UNIT})")
        cb.outline.set_visible(False)
        cb.ax.yaxis.set_major_formatter(_de_formatter())
        cb.ax.tick_params(size=0)

        star = f" · * {cur} laufend (Stand {fmt_date(sd['date'].max())})" if cur is not None else ""
        _title(ax, f"{config.SPREAD_LABEL} – Monatsmittel je Jahrgang",
               f"Mittelwert von spread_bbl je Kalendermonat · divergierend um 0 (rot < 0 < blau){star}", pad=26)
        _footer(fig)
        return fig


# ----------------------------------------------------------------------------
# Interaktives Dashboard (plotly)
# ----------------------------------------------------------------------------
def _plotly_layout(title: str, subtitle: str | None = None, height: int = 480, **kw) -> dict:
    ttl = f"<b>{title}</b>" + (f"<br><span style='font-size:12px;color:{PALETTE['ink2']}'>{subtitle}</span>" if subtitle else "")
    axis = dict(gridcolor=PALETTE["grid"], gridwidth=1, zeroline=False, linecolor=PALETTE["axis"], ticks="",
                tickfont=dict(color=PALETTE["ink2"]), title=dict(font=dict(color=PALETTE["ink2"])))
    lay = dict(
        template="plotly_white",
        title=dict(text=ttl, x=0.01, xanchor="left", font=dict(size=16, color=PALETTE["ink"])),
        font=dict(family='system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', size=12, color=PALETTE["ink"]),
        paper_bgcolor=PALETTE["surface"], plot_bgcolor=PALETTE["surface"],
        separators=",.", height=height, margin=dict(l=64, r=32, t=84, b=56),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=PALETTE["axis"], font=dict(color=PALETTE["ink"], size=12)),
        legend=dict(orientation="h", yanchor="top", y=-0.16, x=0, font=dict(size=11, color=PALETTE["ink2"])),
        xaxis=dict(axis), yaxis=dict(axis),
    )
    lay.update(kw)
    return lay


def _hline_zero(fig) -> None:
    fig.add_hline(y=0, line=dict(color=PALETTE["muted"], width=1.2), layer="above")


def build_dashboard(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None, seasonal: dict | None,
                    path: Path) -> Path:
    import html as _html

    import plotly.graph_objects as go

    sd = _prep_daily(spread_daily)
    cur = _current_vintage(sd, vintage_stats)
    vs = _prep_stats(vintage_stats, sd, cur)
    by_td = _get_seasonal(seasonal, "by_tdays_to_expiry", sd, cur)
    by_ti = _get_seasonal(seasonal, "by_tday_index", sd, cur)
    vints = sorted(sd["vintage"].unique())
    complete = [v for v in vints if v != cur]
    colors, era_legend = _era_colors(complete)
    stand = fmt_date(sd["date"].max()) if len(sd) else "–"
    date_str = sd["date"].dt.strftime("%d.%m.%Y")
    figs: list[tuple[str, str, object]] = []

    # ---- 1) Kette ----
    f1 = go.Figure()
    d1 = sd["date"].max()
    starts = {v: sd.loc[sd["vintage"] == v, "date"].min() for v in vints}
    for i, v in enumerate(vints):
        x0 = starts[v]
        x1 = starts[vints[i + 1]] if i + 1 < len(vints) else d1 + pd.Timedelta(days=25)
        if i % 2 == 1:
            f1.add_vrect(x0=x0, x1=x1, fillcolor=PALETTE["band"], opacity=1.0, line_width=0, layer="below")
        f1.add_annotation(x=x0 + (x1 - x0) / 2, y=1.0, yref="paper", text=str(v), showarrow=False, yanchor="top",
                          font=dict(size=11, color=PALETTE["orange"] if v == cur else PALETTE["muted"]))
    xs, ys, cds = [], [], []
    for v in vints:
        g = sd[sd["vintage"] == v]
        xs += list(g["date"]) + [None]
        ys += list(g["spread_bbl"]) + [None]
        cds += list(zip([v] * len(g), date_str[g.index])) + [(v, "")]
    hover = "%{customdata[1]}<br>Jahrgang %{customdata[0]}<br><b>%{y:.2f} " + UNIT + "</b><extra></extra>"
    if cur is not None:
        hist = sd["vintage"] != cur
        gh = sd[hist]
        xh, yh, ch = [], [], []
        for v in complete:
            g = gh[gh["vintage"] == v]
            xh += list(g["date"]) + [None]
            yh += list(g["spread_bbl"]) + [None]
            ch += list(zip([v] * len(g), date_str[g.index])) + [(v, "")]
        f1.add_trace(go.Scatter(x=xh, y=yh, customdata=ch, mode="lines", name="Abgeschlossene Jahrgänge",
                                line=dict(color=PALETTE["blue"], width=1.4), hovertemplate=hover, connectgaps=False))
        gc = sd[~hist]
        f1.add_trace(go.Scatter(x=gc["date"], y=gc["spread_bbl"], customdata=list(zip(gc["vintage"], date_str[gc.index])),
                                mode="lines", name=f"Jahrgang {cur} (laufend)",
                                line=dict(color=PALETTE["orange"], width=2.0), hovertemplate=hover))
    else:
        f1.add_trace(go.Scatter(x=xs, y=ys, customdata=cds, mode="lines", name="Spread",
                                line=dict(color=PALETTE["blue"], width=1.4), hovertemplate=hover, connectgaps=False))
    if sd["spread_bbl"].notna().any():
        imin = sd["spread_bbl"].idxmin()
        f1.add_trace(go.Scatter(x=[sd.loc[imin, "date"]], y=[sd.loc[imin, "spread_bbl"]],
                                customdata=[(int(sd.loc[imin, "vintage"]), date_str[imin])], mode="markers",
                                name="Gesamtminimum", marker=dict(symbol="diamond", size=11, color=PALETTE["red"],
                                                                  line=dict(color=PALETTE["surface"], width=1.5)),
                                hovertemplate="Gesamtminimum<br>" + hover))
    _hline_zero(f1)
    f1.update_layout(_plotly_layout(
        f"{config.SPREAD_LABEL} – durchgehende Kette",
        f"[(RB Jun − HO Jun) − (RB Dez − HO Dez)] × 42 in {UNIT} · Bänder = Jahrgänge · Bereich unten zum Zoomen ziehen",
        height=560))
    f1.update_xaxes(rangeslider=dict(visible=True, thickness=0.08), tickformat="%Y", hoverformat="%d.%m.%Y",
                    rangeselector=dict(buttons=[
                        dict(count=1, label="1J", step="year", stepmode="backward"),
                        dict(count=3, label="3J", step="year", stepmode="backward"),
                        dict(count=5, label="5J", step="year", stepmode="backward"),
                        dict(step="all", label="Alle")],
                        bgcolor=PALETTE["surface"], activecolor=PALETTE["grid"], font=dict(color=PALETTE["ink2"])))
    f1.update_yaxes(title_text=f"Spread ({UNIT})", ticksuffix="")
    figs.append(("kette", "Kette", f1))

    # ---- 2) Saisonal (tdays_to_expiry) ----
    f2 = go.Figure()
    if len(by_td) and by_td["min"].notna().any():
        x = list(by_td.index)
        f2.add_trace(go.Scatter(x=x, y=by_td["max"], mode="lines", line=dict(width=0), hoverinfo="skip",
                                showlegend=False, name="Max"))
        f2.add_trace(go.Scatter(x=x, y=by_td["min"], mode="lines", line=dict(width=0), fill="tonexty",
                                fillcolor="rgba(195,194,183,0.30)", hoverinfo="skip", name="Spanne Min–Max"))
    vcols_td = {v: c for v, c in _vintage_columns(by_td).items() if v != cur}
    for v in complete:
        g = sd[(sd["vintage"] == v) & sd["tdays_to_expiry"].notna()]
        if g.empty and v in vcols_td:
            s = by_td[vcols_td[v]].dropna()
            f2.add_trace(go.Scatter(x=list(s.index), y=list(s.values), mode="lines", name=str(v),
                                    line=dict(color=colors.get(v, PALETTE["blue"]), width=1.2),
                                    hovertemplate=f"Jahrgang {v}<br>T−%{{x}}<br><b>%{{y:.2f}} {UNIT}</b><extra></extra>"))
            continue
        f2.add_trace(go.Scatter(x=g["tdays_to_expiry"], y=g["spread_bbl"], customdata=date_str[g.index], mode="lines",
                                name=str(v), line=dict(color=colors.get(v, PALETTE["blue"]), width=1.2),
                                hovertemplate=f"Jahrgang {v} · %{{customdata}}<br>T−%{{x}}<br><b>%{{y:.2f}} {UNIT}</b><extra></extra>"))
    if len(by_td) and by_td["median"].notna().any():
        f2.add_trace(go.Scatter(x=list(by_td.index), y=by_td["median"], mode="lines", name="Median",
                                line=dict(color=PALETTE["ink"], width=2.6),
                                hovertemplate="Median<br>T−%{x}<br><b>%{y:.2f} " + UNIT + "</b><extra></extra>"))
    _hline_zero(f2)
    f2.update_layout(_plotly_layout(
        "Saisonaler Vergleich – abgeschlossene Jahrgänge",
        "x = Handelstage bis Verfall (Verfall rechts) · Klick auf Legende blendet Jahrgänge ein/aus, Doppelklick isoliert",
        height=600, legend=dict(orientation="v", x=1.01, y=1, xanchor="left", yanchor="top", font=dict(size=11)),
        margin=dict(l=64, r=120, t=84, b=56)))
    f2.update_xaxes(autorange="reversed", title_text="Handelstage bis Verfall")
    f2.update_yaxes(title_text=f"Spread ({UNIT})")
    figs.append(("saisonal", "Saisonaler Vergleich", f2))

    # ---- 3) laufender Jahrgang (tday_index) ----
    f3 = go.Figure()
    if len(by_ti) and by_ti["min"].notna().any():
        xi = list(by_ti.index)
        f3.add_trace(go.Scatter(x=xi, y=by_ti["max"], mode="lines", line=dict(width=0), hoverinfo="skip",
                                showlegend=False, name="Max"))
        f3.add_trace(go.Scatter(x=xi, y=by_ti["min"], mode="lines", line=dict(width=0), fill="tonexty",
                                fillcolor="rgba(195,194,183,0.30)", hoverinfo="skip", name="Spanne Min–Max"))
    for k, v in enumerate(complete):
        g = sd[(sd["vintage"] == v) & sd["tday_index"].notna()]
        f3.add_trace(go.Scatter(x=g["tday_index"], y=g["spread_bbl"], customdata=date_str[g.index], mode="lines",
                                name="Abgeschlossene Jahrgänge", legendgroup="hist", showlegend=(k == 0),
                                line=dict(color=PALETTE["axis"], width=0.9),
                                hovertemplate=f"Jahrgang {v} · %{{customdata}}<br>Tag %{{x}}<br><b>%{{y:.2f}} {UNIT}</b><extra></extra>"))
    if len(by_ti) and by_ti["median"].notna().any():
        f3.add_trace(go.Scatter(x=list(by_ti.index), y=by_ti["median"], mode="lines", name="Median",
                                line=dict(color=PALETTE["ink"], width=2.6),
                                hovertemplate="Median<br>Tag %{x}<br><b>%{y:.2f} " + UNIT + "</b><extra></extra>"))
    if cur is not None:
        g = sd[(sd["vintage"] == cur) & sd["spread_bbl"].notna()]
        if len(g):
            f3.add_trace(go.Scatter(x=g["tday_index"], y=g["spread_bbl"], customdata=date_str[g.index], mode="lines",
                                    name=f"{cur} (laufend)", line=dict(color=PALETTE["orange"], width=2.6),
                                    hovertemplate=f"Jahrgang {cur} · %{{customdata}}<br>Tag %{{x}}<br><b>%{{y:.2f}} {UNIT}</b><extra></extra>"))
            f3.add_trace(go.Scatter(x=[g["tday_index"].iloc[-1]], y=[g["spread_bbl"].iloc[-1]],
                                    customdata=[date_str[g.index[-1]]], mode="markers+text", showlegend=False,
                                    text=[f"{fmt_de(g['spread_bbl'].iloc[-1])} {UNIT}"], textposition="top left",
                                    textfont=dict(color=PALETTE["ink"], size=11),
                                    marker=dict(size=10, color=PALETTE["orange"], line=dict(color=PALETTE["surface"], width=1.5)),
                                    hovertemplate=f"Jahrgang {cur} · %{{customdata}}<br>Tag %{{x}}<br><b>%{{y:.2f}} {UNIT}</b><extra></extra>"))
    _hline_zero(f3)
    f3.update_layout(_plotly_layout(
        f"Laufender Jahrgang {cur} vs. Historie" if cur is not None else "Median und Spanne auf tday_index",
        "x = Handelstage seit Fensterbeginn (1. Juni) · Median und Spanne über die abgeschlossenen Jahrgänge", height=500))
    f3.update_xaxes(title_text="Handelstage seit Fensterbeginn")
    f3.update_yaxes(title_text=f"Spread ({UNIT})")
    figs.append(("laufend", "Laufender Jahrgang", f3))

    # ---- 4) Spanne je Jahrgang ----
    f4 = go.Figure()
    ylabels = [_label_for(int(v), cur) for v in vs["vintage"]]
    xmin_all = float(np.nanmin(vs["min"])) if vs["min"].notna().any() else -1.0
    if xmin_all < 0:
        f4.add_vrect(x0=xmin_all * 1.15, x1=0, fillcolor=PALETTE["red"], opacity=0.06, line_width=0, layer="below")
    for lab, row in zip(ylabels, vs.itertuples(index=False)):
        f4.add_trace(go.Scatter(x=[row.min, row.max], y=[lab, lab], mode="lines", showlegend=False,
                                line=dict(color=PALETTE["blue_light"], width=8), hoverinfo="skip"))
    f4.add_trace(go.Scatter(x=vs["first_value"], y=ylabels, mode="markers", name="Startwert",
                            customdata=[fmt_date(d) for d in vs.get("window_start", pd.Series([None] * len(vs)))],
                            marker=dict(symbol="circle-open", size=11, color=PALETTE["blue"], line=dict(width=2)),
                            hovertemplate="Start %{customdata}<br>Jahrgang %{y}<br><b>%{x:.2f} " + UNIT + "</b><extra></extra>"))
    f4.add_trace(go.Scatter(x=vs["last_value"], y=ylabels, mode="markers", name="Wert bei Verfall / aktuell",
                            customdata=[fmt_date(d) for d in vs.get("window_end", pd.Series([None] * len(vs)))],
                            marker=dict(symbol="circle", size=11,
                                        color=[PALETTE["orange"] if (cur is not None and int(v) == cur) else PALETTE["blue"]
                                               for v in vs["vintage"]],
                                        line=dict(color=PALETTE["surface"], width=1.5)),
                            hovertemplate="Ende %{customdata}<br>Jahrgang %{y}<br><b>%{x:.2f} " + UNIT + "</b><extra></extra>"))
    f4.add_trace(go.Scatter(x=vs["min"], y=ylabels, mode="markers", name="Minimum",
                            customdata=[fmt_date(d) for d in vs.get("min_date", pd.Series([None] * len(vs)))],
                            marker=dict(symbol="diamond", size=10, color=PALETTE["red"], line=dict(color=PALETTE["surface"], width=1.5)),
                            hovertemplate="Minimum am %{customdata}<br>Jahrgang %{y}<br><b>%{x:.2f} " + UNIT + "</b><extra></extra>"))
    f4.add_vline(x=0, line=dict(color=PALETTE["muted"], width=1.2))
    f4.update_layout(_plotly_layout("Spanne je Jahrgang", "Balken = Min–Max · Kreise = Start / Verfall · Raute = Minimum",
                                    height=140 + 34 * len(vs)))
    f4.update_yaxes(autorange="reversed", showgrid=False)
    f4.update_xaxes(title_text=f"Spread ({UNIT})")
    figs.append(("spanne", "Spanne je Jahrgang", f4))

    # ---- 5) negative Tage ----
    f5 = go.Figure()
    nneg = vs["n_negative_days"].fillna(0).to_numpy(dtype=float)
    ndays = vs["n_days"].to_numpy(dtype=float) if "n_days" in vs.columns else np.full(len(vs), np.nan)
    pct = np.where(ndays > 0, 100.0 * nneg / np.where(ndays > 0, ndays, 1), np.nan)
    ended_neg = (vs["last_value"] < 0).to_numpy()
    f5.add_trace(go.Bar(x=[_label_for(int(v), cur, star=True) for v in vs["vintage"]], y=nneg, width=0.45,
                        marker=dict(color=[PALETTE["red"] if e else PALETTE["blue"] for e in ended_neg]),
                        text=[fmt_de(h, 0) if (h <= 0 or np.isnan(p)) else f"{fmt_de(h, 0)} ({fmt_de(p, 1)} %)"
                              for h, p in zip(nneg, pct)],
                        textposition="outside", textfont=dict(color=PALETTE["ink2"], size=11), cliponaxis=False,
                        customdata=list(zip(pct, ended_neg)), showlegend=False,
                        hovertemplate="Jahrgang %{x}<br><b>%{y} negative Handelstage</b> (%{customdata[0]:.1f} %)<extra></extra>"))
    f5.add_trace(go.Bar(x=[None], y=[None], name="Wert bei Verfall ≥ 0", marker=dict(color=PALETTE["blue"])))
    if ended_neg.any():
        f5.add_trace(go.Bar(x=[None], y=[None], name="Wert bei Verfall < 0", marker=dict(color=PALETTE["red"])))
    f5.update_layout(_plotly_layout("Negative Handelstage je Jahrgang",
                                    "Anzahl Handelstage mit Spread < 0 (Anteil in %) · * laufender Jahrgang", height=440),
                     barcornerradius=4, bargap=0.5)
    f5.update_yaxes(title_text="Handelstage mit Spread < 0", rangemode="tozero")
    figs.append(("negativ", "Negative Handelstage", f5))

    # ---- 6) Heatmap ----
    m = monthly_matrix(sd)
    z = m.to_numpy(dtype=float)
    lim = max(float(np.nanmax(np.abs(z))) if np.isfinite(z).any() else 0.5, 0.5)
    stops = [(i / (len(DIVERGING_STOPS) - 1), c) for i, c in enumerate(DIVERGING_STOPS)]
    f6 = go.Figure(go.Heatmap(
        z=z, x=[MONTHS_DE[k] for k in WINDOW_MONTHS], y=[_label_for(int(v), cur, star=True) for v in m.index],
        colorscale=stops, zmin=-lim, zmax=lim, zmid=0, xgap=2, ygap=2,
        text=[[fmt_de(v, 2) for v in row] for row in z], texttemplate="%{text}", textfont=dict(size=11),
        hovertemplate="Jahrgang %{y} · %{x}<br><b>Ø %{z:.2f} " + UNIT + "</b><extra></extra>",
        colorbar=dict(title=dict(text=f"Ø {UNIT}"), outlinewidth=0, thickness=14, len=0.8)))
    f6.add_vline(x=6.5, line=dict(color=PALETTE["ink2"], width=1.2))
    f6.update_layout(_plotly_layout("Monatsmittel je Jahrgang", "Juni des Vorjahres bis Mai des Jahrgangs · divergierend um 0 (rot < 0 < blau)",
                                    height=160 + 34 * len(m)))
    f6.update_yaxes(autorange="reversed", showgrid=False)
    f6.update_xaxes(showgrid=False, side="bottom")
    figs.append(("heatmap", "Monatsmittel", f6))

    # ---- HTML zusammensetzen (plotly.js einmal inline -> offline nutzbar) ----
    cfg = {"responsive": True, "displaylogo": False,
           "modeBarButtonsToRemove": ["lasso2d", "select2d"], "toImageButtonOptions": {"format": "png", "scale": 2}}
    divs = []
    for i, (key, label, fig) in enumerate(figs):
        divs.append(fig.to_html(full_html=False, include_plotlyjs=(i == 0), config=cfg, div_id=f"fig-{key}",
                                default_width="100%", default_height=None))
    nav = " · ".join(f'<a href="#sec-{k}">{_html.escape(lab)}</a>' for k, lab, _ in figs)
    sections = "\n".join(
        f'<section id="sec-{k}"><div class="card">{d}</div></section>' for (k, lab, _), d in zip(figs, divs))
    first_v, last_v = (vints[0], vints[-1]) if vints else ("", "")
    page = f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(config.SPREAD_LABEL)} – Dashboard</title>
<style>
:root{{color-scheme:light;--surface:{PALETTE['surface']};--page:{PALETTE['page']};--ink:{PALETTE['ink']};--ink2:{PALETTE['ink2']};
--muted:{PALETTE['muted']};--grid:{PALETTE['grid']};--blue:{PALETTE['blue']}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1400px;margin:0 auto;padding:24px 16px 48px}}header h1{{font-size:22px;margin:0 0 4px;font-weight:600}}
header p{{margin:0 0 6px;color:var(--ink2)}}nav{{margin:10px 0 22px;color:var(--muted)}}nav a{{color:var(--blue);text-decoration:none}}
nav a:hover{{text-decoration:underline}}section{{margin:0 0 22px}}.card{{background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:8px 8px 4px}}
footer{{color:var(--muted);font-size:12px;margin-top:24px}}
</style></head><body><main>
<header><h1>{_html.escape(config.SPREAD_LABEL)} – interaktives Dashboard</h1>
<p>Spread = [(RB Jun − HO Jun) − (RB Dez − HO Dez)] × 42 in {UNIT} · Jahrgänge {first_v}–{last_v} · Stand {stand}</p>
<p>Hover zeigt Datum, Jahrgang und Wert. Legendeneinträge anklicken blendet Serien aus, Doppelklick isoliert eine Serie.</p>
<nav>{nav}</nav></header>
{sections}
<footer>{_html.escape(SOURCE_NOTE)} Diese Datei ist selbsttragend (plotly.js eingebettet) und funktioniert offline.</footer>
</main></body></html>
"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# Alles auf einmal
# ----------------------------------------------------------------------------
def _save(fig: Figure, path: Path) -> Path:
    """Speichert im selben rc-Kontext, in dem die Figur gebaut und vermessen wurde.

    Schriftfamilien ("sans-serif") werden erst beim Rendern aufgeloest. Ohne den Kontext wuerde
    savefig auf den matplotlib-Standard (DejaVu Sans, ca. 12 % breiter als Segoe UI) zurueckfallen,
    und die beim Bau gemessenen Legenden-/Labelbreiten stimmen nicht mehr (Ueberlappungen).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(RC):
        fig.savefig(path, dpi=OUTPUT_DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def make_all_charts(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame | None, seasonal: dict | None,
                    out_dir: Path | str) -> dict[str, Path]:
    """Erzeugt alle statischen Charts und das Dashboard in out_dir. Rueckgabe: {key: Path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    paths["chain"] = _save(plot_chain(spread_daily, vintage_stats), out_dir / CHART_SPECS["chain"][0])
    paths["seasonal"] = _save(plot_seasonal(spread_daily, seasonal, vintage_stats), out_dir / CHART_SPECS["seasonal"][0])
    paths["vintage_range"] = _save(plot_vintage_range(vintage_stats, spread_daily), out_dir / CHART_SPECS["vintage_range"][0])
    paths["negative_days"] = _save(plot_negative_days(vintage_stats, spread_daily), out_dir / CHART_SPECS["negative_days"][0])
    paths["heatmap"] = _save(plot_heatmap(spread_daily, vintage_stats), out_dir / CHART_SPECS["heatmap"][0])
    paths["dashboard"] = build_dashboard(spread_daily, vintage_stats, seasonal, out_dir / CHART_SPECS["dashboard"][0])
    return paths


if __name__ == "__main__":  # Dev-Treiber mit Fixture -> output/_dev/
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    warnings.simplefilter("default")
    _tests = config.PROJECT_ROOT / "tests"
    if str(_tests) not in sys.path:
        sys.path.insert(0, str(_tests))
    from fixtures_charts import make_fixture_frames  # type: ignore

    _sd, _vs, _seas = make_fixture_frames()
    _out = config.OUTPUT_DIR / "_dev"
    _paths = make_all_charts(_sd, _vs, _seas, _out)
    for _k, _p in _paths.items():
        print(f"{_k:14s} {_p}  ({_p.stat().st_size / 1024:,.0f} KB)")
