import calendar
import json
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import cdsapi
import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st
import xarray as xr
from folium.plugins import MiniMap
from streamlit_folium import st_folium
from timezonefinder import TimezoneFinder

st.set_page_config(page_title="Wind Atlas - ERA5 Sailing Forecasts", layout="wide")

st.html(
    """
    <style>
    .app-masthead {
        display: flex; align-items: baseline; gap: .65rem; flex-wrap: wrap;
        padding: .5rem 0 .1rem;
    }
    .app-masthead .mark {
        font-size: 1.6rem; font-weight: 800; letter-spacing: -.02em;
        background: linear-gradient(90deg, #8ecae6, #ffb703 55%, #fb8500);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .app-masthead .tag { font-size: .82rem; opacity: .55; }
    </style>
    <div class="app-masthead">
        <span class="mark">🌬️ Wind Atlas</span>
        <span class="tag">Eighty years of hourly wind, anywhere you sail</span>
    </div>
    """
)

# --- House styles: motion, heroes, cards --------------------------------
# Up here so every screen gets them, the map screen included.

st.html(
    """
    <style>
    @keyframes riseIn {
        from { opacity: 0; transform: translateY(12px); }
        to   { opacity: 1; transform: none; }
    }
    @keyframes sweepIn {
        from { opacity: 0; transform: scale(.96); }
        to   { opacity: 1; transform: none; }
    }
    @keyframes popIn {
        0%   { opacity: 0; transform: scale(.4) rotate(-18deg); }
        70%  { opacity: 1; transform: scale(1.15) rotate(4deg); }
        100% { opacity: 1; transform: scale(1) rotate(0); }
    }
    @keyframes shimmer {
        0%   { background-position: -220% 0; }
        100% { background-position: 220% 0; }
    }

    /* Metrics and charts arrive rather than snapping in. */
    [data-testid="stMetric"] { animation: riseIn .5s ease both; }
    [data-testid="stMetric"]:nth-of-type(2) { animation-delay: .07s; }
    [data-testid="stMetric"]:nth-of-type(3) { animation-delay: .14s; }
    [data-testid="stMetric"]:nth-of-type(4) { animation-delay: .21s; }
    [data-testid="stVegaLiteChart"],
    [data-testid="stImage"],
    [data-testid="stDeckGlJsonChart"] { animation: sweepIn .55s ease both; }

    /* Bordered cards lift slightly under the pointer. */
    [data-testid="stVerticalBlockBorderWrapper"] {
        transition: transform .18s ease, box-shadow .18s ease;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 26px rgba(0, 0, 0, .18);
    }

    /* Progress meters glide to their value instead of jumping. */
    [data-testid="stProgress"] div[role="progressbar"] > div {
        transition: width .45s cubic-bezier(.22, .68, .36, 1);
    }

    .rating-hero { animation: riseIn .6s ease both; text-align: center; padding: .4rem 0 1rem; }
    .rating-score {
        font-size: 4.2rem; font-weight: 700; line-height: 1;
        background: linear-gradient(100deg, currentColor 38%, #fb8500 50%, currentColor 62%);
        background-size: 220% 100%;
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: shimmer 2.6s ease-in-out .5s 2;
    }
    .rating-stars { font-size: 1.6rem; letter-spacing: .18rem; }
    .rating-stars span { display: inline-block; animation: popIn .45s ease both; }
    .rating-stars span:nth-child(2) { animation-delay: .09s; }
    .rating-stars span:nth-child(3) { animation-delay: .18s; }
    .rating-stars span:nth-child(4) { animation-delay: .27s; }
    .rating-stars span:nth-child(5) { animation-delay: .36s; }

    /* Hero blocks on the entry screens. */
    .hero { animation: riseIn .55s ease both; padding: .2rem 0 1.1rem; }
    .hero-title {
        font-size: 2.9rem; font-weight: 700; line-height: 1.1; letter-spacing: -.025em;
        color: #e8eef5;
    }
    .hero-sub {
        font-size: 1.02rem; opacity: .72; max-width: 62ch; margin-top: .5rem;
        line-height: 1.5;
    }
    .hero-eyebrow {
        text-transform: uppercase; letter-spacing: .16em; font-size: .72rem;
        opacity: .55; font-weight: 600; margin-bottom: .3rem;
    }
    .stat-strip {
        display: flex; gap: 1.6rem; flex-wrap: wrap; opacity: .8;
        font-size: .86rem; margin-top: .35rem;
    }
    .stat-strip b { font-weight: 600; opacity: 1; }

    /* Wind drifting across the hero. */
    @keyframes drift {
        from { transform: translateX(-18%); }
        to   { transform: translateX(18%); }
    }
    @keyframes gradientRoll {
        0%   { background-position: 0% 50%; }
        100% { background-position: 200% 50%; }
    }
    @keyframes breathe {
        0%, 100% { opacity: .45; }
        50%      { opacity: .9; }
    }

    .hero { animation: riseIn .55s ease both; padding: .2rem 0 1.1rem; position: relative; }
    .hero-streaks {
        position: absolute; inset: -20% -30%; z-index: 0; pointer-events: none;
        background: repeating-linear-gradient(
            74deg, transparent 0 22px,
            rgba(142, 202, 230, .16) 22px 23px,
            transparent 23px 60px,
            rgba(251, 133, 0, .13) 60px 61px,
            transparent 61px 104px
        );
        -webkit-mask-image: radial-gradient(70% 120% at 20% 40%, #000 0%, transparent 72%);
        mask-image: radial-gradient(70% 120% at 20% 40%, #000 0%, transparent 72%);
        animation: drift 13s ease-in-out infinite alternate, breathe 7s ease-in-out infinite;
    }
    .hero > *:not(.hero-streaks) { position: relative; z-index: 1; }
    .hero-sub {
        font-size: 1.02rem; opacity: .72; max-width: 52ch; margin-top: .6rem;
        line-height: 1.55; animation: riseIn .6s ease .12s both;
    }
    .hero-eyebrow {
        text-transform: uppercase; letter-spacing: .16em; font-size: .72rem;
        opacity: .55; font-weight: 600; margin-bottom: .3rem;
    }
    .stat-strip {
        display: flex; gap: 1.6rem; flex-wrap: wrap; opacity: .8;
        font-size: .86rem; margin-top: .55rem;
    }
    .stat-strip b { font-weight: 600; opacity: 1; }

    /* The map sits in its own rounded panel rather than running edge to edge. */
    iframe[title="streamlit_folium.st_folium"] {
        border-radius: 16px;
        box-shadow: 0 14px 40px rgba(0, 0, 0, .22);
        animation: sweepIn .7s ease .18s both;
    }

    /* Words rise in one after another when a view switches. */
    @keyframes wordIn {
        from { opacity: 0; transform: translateY(14px); filter: blur(4px); }
        to   { opacity: 1; transform: none; filter: none; }
    }
    .reveal span {
        display: inline-block;
        opacity: 0;
        animation: wordIn .55s cubic-bezier(.2,.7,.3,1) both;
    }

    /* Colour accents on the dark ground. */
    .hero-eyebrow { color: #fb8500; opacity: .9; }
    .stat-strip b { color: #8ecae6; }
    .accent-rule {
        height: 3px; border: 0; border-radius: 3px; margin: .2rem 0 1.1rem;
        background: linear-gradient(90deg, #fb8500, #ffb703 32%, #8ecae6 68%, transparent);
        animation: sweepIn .7s ease both;
    }
    </style>
    """
)

MS_TO_KNOTS = 1.9438444924406046

# ERA5 is published on a 0.25 degree grid, so any click resolves to a cell.
GRID = 0.25
# What you sail decides which winds count. Ladder = the four bands on the
# monthly chart; groove = the range you actually want, both in knots.
ACTIVITIES = {
    "Any wind": {"ladder": [6, 12, 18, 24], "groove": (0, 30), "icon": ":material/air:"},
    "Dinghy": {"ladder": [6, 10, 15, 20], "groove": (6, 25), "icon": ":material/sailing:"},
    "Foil": {"ladder": [12, 16, 20, 25], "groove": (12, 30), "icon": ":material/waves:"},
    "Kite": {"ladder": [15, 20, 26, 32], "groove": (15, 40), "icon": ":material/paragliding:"},
}
# Within-day spread above this counts as thoroughly gusty (see calibration in
# the data: median day sits near 0.25).
GUSTY_CV = 0.5

# How the overall score is put together. Whether the wind is any good carries
# most of the weight; how much it wanders through the day carries least.
RATING_WEIGHTS = {
    "Days in the groove": 0.55,
    "Season length": 0.20,
    "Big days": 0.15,
    "Steadiness": 0.10,
}

# A few spots to start from; the map accepts any point.
PRESETS = {
    # Offshore ERA5 cells — a cell centred on land measures a sheltered,
    # slowed-down wind that has nothing to do with sailing conditions.
    "Vilamoura": (36.75, -8.25),
    "Imperia": (43.75, 8.00),
    "Valencia": (39.50, 0.00),
    "Kiel": (54.50, 10.25),
}

SESSION_COLORS = ["tab:blue", "tab:orange"]

THRESHOLD_COLORS = ["#8ecae6", "#ffb703", "#fb8500", "#9d0208"]

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
# Upper bound in knots -> what a sailor would call it.
BEAUFORT = [(1, "calm"), (4, "light air"), (7, "light breeze"), (11, "gentle breeze"),
            (17, "moderate breeze"), (22, "fresh breeze"), (28, "strong breeze"),
            (34, "near gale"), (41, "gale"), (999, "storm")]

data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
REGISTRY = data_dir / "locations.json"


def cds_client():
    """Credentials come from Streamlit secrets when deployed, or from
    ~/.cdsapirc when running locally."""
    try:
        credentials = st.secrets["cds"]
        return cdsapi.Client(url=credentials["url"], key=credentials["key"])
    except Exception:
        # No secrets configured — fall back to the local rc file.
        return cdsapi.Client()


def snap_to_grid(value):
    """ERA5 resolves to 0.25 degrees, so snap the click to the cell it lands in."""
    return round(value / GRID) * GRID


@st.cache_resource(show_spinner=False)
def timezone_finder():
    return TimezoneFinder()


def timezone_at(latitude, longitude):
    """IANA zone for the coordinates, so local hours honour daylight saving."""
    zone = timezone_finder().timezone_at(lat=latitude, lng=longitude)
    return zone or "UTC"


def dataset_for(latitude, longitude):
    """One cached netCDF per grid cell."""
    return data_dir / f"wind_{latitude:+.2f}_{longitude:+.2f}.nc"


def ladder_for(activity, unit):
    """The four speed bands, converted to whatever unit is on screen."""
    knots = ACTIVITIES[activity]["ladder"]
    if unit == "knots":
        return list(knots)
    return [round(value / MS_TO_KNOTS * 2) / 2 for value in knots]


def can_score(activity):
    """A range that starts at 0 kt counts every day as sailable, so a score
    would say nothing about the spot — such a choice is for exploring only."""
    return ACTIVITIES[activity]["groove"][0] > 0


def remember_location(latitude, longitude, name, zone):
    registry = load_registry()
    registry[f"{latitude:+.2f}_{longitude:+.2f}"] = {
        "lat": latitude, "lon": longitude, "name": name, "zone": zone,
    }
    REGISTRY.write_text(json.dumps(registry, indent=2))


def load_registry():
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def downloaded_locations():
    """Every grid cell already on disk, named where we know the name."""
    registry = load_registry()
    found = {}
    for path in sorted(data_dir.glob("wind_*.nc")):
        key = path.stem.removeprefix("wind_")
        entry = registry.get(key)
        if entry is None:
            try:
                latitude, longitude = (float(part) for part in key.split("_"))
            except ValueError:
                continue
            entry = {
                "lat": latitude, "lon": longitude,
                "name": f"{latitude:+.2f}, {longitude:+.2f}",
                "zone": timezone_at(latitude, longitude),
            }
        found[key] = {**entry, "path": path}
    return found


def unpack_download(download_path, target):
    """The timeseries endpoint returns a zip archive containing the netCDF."""
    if not zipfile.is_zipfile(download_path):
        download_path.replace(target)
        return target
    with zipfile.ZipFile(download_path) as archive:
        nc_names = [n for n in archive.namelist() if n.endswith(".nc")]
        if not nc_names:
            raise RuntimeError("No netCDF file found in the downloaded archive")
        archive.extract(nc_names[0], data_dir)
    (data_dir / nc_names[0]).replace(target)
    download_path.unlink(missing_ok=True)
    return target


@st.cache_data(show_spinner="Loading data...")
def load_wind(path, mtime, zone):
    """Load the wind components and derive speed. mtime busts the cache on refetch."""
    with xr.open_dataset(path) as ds:
        index = pd.DatetimeIndex(ds["valid_time"].values, name="time")
        df = pd.DataFrame(
            {"u10": ds["u10"].values, "v10": ds["v10"].values},
            index=index.tz_localize("UTC").tz_convert(zone),
        )
        if "t2m" in ds.data_vars:
            # ERA5 stores 2m temperature in kelvin.
            df["temp_c"] = ds["t2m"].values - 273.15
    df["speed"] = np.hypot(df["u10"], df["v10"])
    return df


def hour_mask(index, hours):
    """Select observations whose local hour falls inside an inclusive window."""
    if hours is None:
        return pd.Series(True, index=index)
    start, end = hours
    hour = index.hour
    if start <= end:
        return pd.Series((hour >= start) & (hour <= end), index=index)
    # Window wraps past midnight, e.g. 22:00 to 03:00.
    return pd.Series((hour >= start) | (hour <= end), index=index)


def theme_colors():
    """Palette that follows the active Streamlit theme."""
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    return {
        # Matched to .streamlit/config.toml so the figures sit in the palette.
        "fg": "#e8eef5" if dark else "#1f2328",
        "muted": "#8b98a5" if dark else "#57606a",
        "grid": "#26333f" if dark else "#d0d7de",
    }


def style_figure(fig, *axes):
    """Drop the opaque canvas so charts sit on the app background."""
    colors = theme_colors()
    fig.patch.set_alpha(0)
    for ax in axes:
        ax.set_facecolor("none")
        ax.tick_params(colors=colors["muted"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(colors["grid"])
        ax.xaxis.label.set_color(colors["muted"])
        ax.yaxis.label.set_color(colors["muted"])
        ax.title.set_color(colors["fg"])
        for text in ax.texts:
            text.set_color(colors["muted"])
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_alpha(0)
            for text in legend.get_texts():
                text.set_color(colors["muted"])
            if legend.get_title() is not None:
                legend.get_title().set_color(colors["muted"])
    for legend in fig.legends:
        for text in legend.get_texts():
            text.set_color(colors["muted"])
    return fig


# Warm-to-cool ramp used for word-by-word headings.
GRADIENT = ["#fb8500", "#ffb703", "#8ecae6"]


def blend(first, second, amount):
    pair = [
        round(int(first[i:i + 2], 16) * (1 - amount) + int(second[i:i + 2], 16) * amount)
        for i in (1, 3, 5)
    ]
    return "#{:02x}{:02x}{:02x}".format(*pair)


def ramp_colour(fraction):
    """Colour at `fraction` along the multi-stop gradient."""
    span = len(GRADIENT) - 1
    position = min(max(fraction, 0.0), 1.0) * span
    index = min(int(position), span - 1)
    return blend(GRADIENT[index], GRADIENT[index + 1], position - index)


def reveal(text, delay=0.0, step=0.055, coloured=True):
    """Wrap each word so it rises in one after another, tinted along the ramp.

    The colour is baked per word rather than clipped from a background
    gradient: an animated inline-block paints on its own, which silently
    breaks `background-clip: text` on the parent.
    """
    words = str(text).split()
    total = max(len(words) - 1, 1)
    pieces = []
    for index, word in enumerate(words):
        tint = f"color:{ramp_colour(index / total)};" if coloured else ""
        pieces.append(
            f'<span style="{tint}animation-delay:{delay + index * step:.2f}s">'
            f"{word}&nbsp;</span>"
        )
    return "".join(pieces)


def pick(value, fallback):
    """segmented_control returns None when the user deselects; keep a value."""
    return fallback if value is None else value


def sessions_mask(index, windows):
    """True where the hour falls in any of the chosen sailing windows."""
    if not windows or any(window is None for window in windows):
        return pd.Series(True, index=index)
    combined = pd.Series(False, index=index)
    for window in windows:
        combined |= hour_mask(index, window)
    return combined


def wind_direction(u, v):

    """Meteorological convention: the direction the wind blows *from*."""
    return np.degrees(np.arctan2(-u, -v)) % 360


def prevailing_direction(degrees):
    """Most common compass sector. Averaging degrees would break at the
    0/360 wrap — due north would average out to due south."""
    sectors = np.floor(((np.asarray(degrees) + 11.25) % 360) / 22.5).astype(int)
    return COMPASS[int(np.bincount(sectors, minlength=16).argmax())]


def beaufort(knots):
    """Plain-language description of a wind speed."""
    for limit, name in BEAUFORT:
        if knots < limit:
            return name
    return BEAUFORT[-1][1]


def rose_bands(direction, speed, thresholds, bins=72, sigma=3.0):
    """Smooth the direction histogram so the petals flow into each other
    instead of stepping between 22.5-degree sectors."""
    edges = [0.0, *thresholds, np.inf]
    slot = np.floor((np.asarray(direction) % 360) / (360 / bins)).astype(int)
    half = int(sigma * 3)
    kernel = np.exp(-0.5 * (np.arange(-half, half + 1) / sigma) ** 2)
    kernel /= kernel.sum()

    speed = np.asarray(speed)
    total = max(len(speed), 1)
    bands = []
    for low, high in zip(edges, edges[1:]):
        in_band = (speed >= low) & (speed < high)
        counts = np.bincount(slot[in_band], minlength=bins).astype(float)
        share = counts / total * 100 * (bins / 16)
        wrapped = np.concatenate([share[-half:], share, share[:half]])
        bands.append(np.convolve(wrapped, kernel, mode="same")[half:-half])
    return np.deg2rad(np.arange(bins) * (360 / bins)), bands


# CARTO started stamping "API KEY REQUIRED" across its basemaps, so the dark
# ground comes from Esri's canvas instead, which serves without a key. It ships
# the place names as a separate transparent layer laid over the base.
MAP_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
MAP_LABELS = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
)
MAP_ATTR = "Esri"
# The canvas is drawn up to zoom 16; past that Leaflet stretches the last tiles
# rather than showing nothing.
MAP_NATIVE_ZOOM = 16
MAP_CHROME_CSS = """
<style>
.spot-dot {
    width: 13px; height: 13px; border-radius: 50%;
    background: #8ecae6;
    border: 2px solid rgba(255,255,255,.9);
    box-shadow: 0 0 0 4px rgba(142,202,230,.22), 0 2px 6px rgba(0,0,0,.45);
}
.spot-dot.picked { background: #fb8500; box-shadow: 0 0 0 5px rgba(251,133,0,.25), 0 2px 8px rgba(0,0,0,.5); }
.spot-dot.picked::after {
    content: ""; position: absolute; inset: -5px; border-radius: 50%;
    border: 2px solid rgba(251,133,0,.55);
    animation: spotPulse 2s ease-out infinite;
}
@keyframes spotPulse {
    0%   { transform: scale(.7); opacity: .9; }
    100% { transform: scale(1.9); opacity: 0; }
}
/* Tile licences require the credit to stay, so shrink it out of the way
   rather than deleting it. */
.leaflet-control-attribution {
    background: rgba(0,0,0,.30) !important;
    font-size: 8px !important;
    line-height: 11px !important;
    padding: 0 4px !important;
    opacity: .45;
}
.leaflet-control-attribution, .leaflet-control-attribution a {
    color: #71808f !important;
}
.leaflet-control-attribution img { display: none !important; }
</style>
"""


def dot_marker(latitude, longitude, tooltip, picked=False):
    """A designed dot rather than folium's default pin."""
    classes = "spot-dot picked" if picked else "spot-dot"
    return folium.Marker(
        [latitude, longitude],
        tooltip=tooltip,
        icon=folium.DivIcon(
            html=f'<div class="{classes}"></div>',
            icon_size=(13, 13),
            icon_anchor=(6, 6),
        ),
    )


def base_map(**options):
    """A dark map carrying the house styling, ready for markers."""
    chart = folium.Map(tiles=None, **options)
    for url, overlay in ((MAP_TILES, False), (MAP_LABELS, True)):
        folium.TileLayer(
            url, attr=MAP_ATTR, overlay=overlay, control=False,
            max_zoom=18, max_native_zoom=MAP_NATIVE_ZOOM,
        ).add_to(chart)
    chart.get_root().header.add_child(folium.Element(MAP_CHROME_CSS))
    return chart


def locator_map(latitude, longitude, key, height=230):
    """The cell against the coastline, so you can see whether the water you
    care about is actually water.

    An offshore cell can sit a thousand miles from the nearest land, where a
    close view is an empty square of ocean — hence the zoom controls and the
    world inset, which place the cell when the tiles alone cannot."""
    local = base_map(
        location=[latitude, longitude], zoom_start=7,
        control_scale=True, dragging=True,
    )
    MiniMap(
        tile_layer=folium.TileLayer(MAP_TILES, attr=MAP_ATTR),
        position="bottomleft", width=104, height=104,
        zoom_level_offset=-6, toggle_display=True,
    ).add_to(local)
    folium.Rectangle(
        bounds=[
            [latitude - GRID / 2, longitude - GRID / 2],
            [latitude + GRID / 2, longitude + GRID / 2],
        ],
        color="#fb8500", weight=2, fill=True, fill_opacity=0.25,
    ).add_to(local)
    dot_marker(latitude, longitude, "Your cell", picked=True).add_to(local)
    st_folium(local, height=height, use_container_width=True, key=key,
              returned_objects=[])


def draw_rose(direction, speed, thresholds, unit_label):
    """Where the wind comes from, coloured by how hard it blows. The radial
    scale carries no numbers — the shape and colour are the message."""
    theta, bands = rose_bands(direction, speed, thresholds)
    closed = np.append(theta, theta[0] + 2 * np.pi)
    labels = (
        [f"< {thresholds[0]:g}"]
        + [f"{lo:g}–{hi:g}" for lo, hi in zip(thresholds, thresholds[1:])]
        + [f"> {thresholds[-1]:g}"]
    )
    palette = theme_colors()

    fig = plt.figure(figsize=(5.2, 3.5))
    ax = fig.add_axes([0.02, 0.10, 0.60, 0.80], projection="polar")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)

    lower = np.zeros(len(theta))
    for band, color, label in zip(bands, ["#dfe3e8", *THRESHOLD_COLORS], labels):
        upper = lower + band
        ax.fill_between(
            closed, np.append(lower, lower[0]), np.append(upper, upper[0]),
            color=color, linewidth=0, label=label,
        )
        lower = upper
    ax.plot(closed, np.append(lower, lower[0]), color=palette["fg"], lw=0.7, alpha=0.35)

    ax.set_xticks(np.deg2rad(np.arange(0, 360, 45)))
    ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"], fontsize=9)
    ax.tick_params(axis="x", colors=palette["fg"], pad=1)
    ax.set_yticklabels([])
    ax.tick_params(axis="y", length=0)
    ax.grid(color=palette["grid"], alpha=0.5, linewidth=0.6)
    ax.spines["polar"].set_color(palette["grid"])
    ax.spines["polar"].set_linewidth(0.6)
    ax.legend(
        title=unit_label, loc="center left", bbox_to_anchor=(1.06, 0.5),
        ncol=1, fontsize=8, title_fontsize=8, frameon=False, labelspacing=0.9,
    )
    style_figure(fig, ax)
    return fig


def best_window(series_knots, length=3):
    """The consecutive block of hours with the strongest average wind."""
    by_hour = series_knots.groupby(series_knots.index.hour).mean()
    by_hour = by_hour.reindex(range(24))
    doubled = pd.concat([by_hour, by_hour])
    rolled = doubled.rolling(length).mean().dropna()
    end = int(rolled.idxmax()) if not rolled.empty else 0
    start = (end - length + 1) % 24
    return start, end, float(rolled.max()) if not rolled.empty else float("nan")




# --- Download -------------------------------------------------------------

# --- Stage 1: pick a spot on the map --------------------------------------

if "spot" not in st.session_state:
    st.session_state["spot"] = None


def enter_spot(latitude, longitude, zone, name):
    st.session_state["spot"] = {
        "lat": latitude, "lon": longitude, "zone": zone, "name": name,
    }
    remember_location(latitude, longitude, name, zone)


if st.session_state["spot"] is None:
    saved = downloaded_locations()

    side, chart = st.columns([2, 3], gap="large")

    with side:
        st.html(
            '<div class="hero">'
            '<div class="hero-streaks"></div>'
            '<div class="hero-eyebrow">Step 1 · Wind atlas</div>'
            '<div class="hero-title reveal">'
            + reveal("Pick a spot anywhere on earth")
            + "</div>"
            '<div class="hero-sub">Click the map and eighty years of hourly wind '
            f'comes back for that {GRID}° cell — about 25 km across. '
            f'<b>{len(saved)}</b> already downloaded.</div>'
            "</div>"
        )

        preset = pick(
            st.segmented_control(
                "Known spots", list(PRESETS), default=None, key="preset"
            ),
            None,
        )
        if preset and st.session_state.get("preset_applied") != preset:
            st.session_state["preset_applied"] = preset
            st.session_state["clicked"] = PRESETS[preset]
            st.session_state["recentre"] = PRESETS[preset]
            st.rerun()

        if saved:
            key = st.selectbox(
                "Or reopen one you have", list(saved),
                format_func=lambda k: saved[k]["name"], key="saved_pick",
            )
            if st.button(":material/bolt: Open it", width="stretch"):
                entry = saved[key]
                enter_spot(entry["lat"], entry["lon"], entry["zone"], entry["name"])
                st.rerun()

    with chart:
        clicked = st.session_state.get("clicked")
        centre = clicked or (25.0, 5.0)
        world = base_map(
            location=list(centre),
            zoom_start=7 if clicked else 2,
            control_scale=True,
            min_zoom=2,
            world_copy_jump=True,
        )
        # Everything already on disk, so the map doubles as your library.
        for entry in saved.values():
            dot_marker(entry["lat"], entry["lon"], entry["name"]).add_to(world)
        if clicked:
            cell_lat, cell_lon = snap_to_grid(clicked[0]), snap_to_grid(clicked[1])
            folium.Rectangle(
                bounds=[
                    [cell_lat - GRID / 2, cell_lon - GRID / 2],
                    [cell_lat + GRID / 2, cell_lon + GRID / 2],
                ],
                color="#fb8500", weight=2, fill=True, fill_opacity=0.25,
                tooltip=f"{cell_lat:+.2f}, {cell_lon:+.2f}",
            ).add_to(world)
            dot_marker(cell_lat, cell_lon, "Selected cell", picked=True).add_to(world)

        # st_folium keeps its own view across reruns, so a preset change has to
        # push a new centre explicitly or only the panel below would update.
        recentre = st.session_state.pop("recentre", None)
        result = st_folium(
            world, height=430, use_container_width=True, key="picker",
            center=list(recentre) if recentre else None,
            zoom=8 if recentre else None,
            returned_objects=["last_clicked"],
        )
        latest = None
        if result and result.get("last_clicked"):
            latest = (result["last_clicked"]["lat"], result["last_clicked"]["lng"])
        # st_folium replays its stored click on every rerun, so compare against
        # the click we last consumed — not against the current selection. Doing
        # the latter let a stale click overwrite a preset chosen a moment before.
        if latest is not None and latest != st.session_state.get("click_seen"):
            st.session_state["click_seen"] = latest
            st.session_state["clicked"] = latest
            st.rerun()

    if not st.session_state.get("clicked"):
        st.info("Click the map to choose a location, or open one you already have.")
        st.stop()

    raw_lat, raw_lon = st.session_state["clicked"]
    latitude, longitude = snap_to_grid(raw_lat), snap_to_grid(raw_lon)
    zone = timezone_at(latitude, longitude)
    cached = dataset_for(latitude, longitude)

    with st.container(border=True):
        facts_row = st.columns([2, 2, 2, 3], vertical_alignment="center")
        facts_row[0].metric(
            "Grid cell", f"{latitude:+.2f}, {longitude:+.2f}",
            icon=":material/location_on:",
            help=f"You clicked {raw_lat:.3f}, {raw_lon:.3f}",
        )
        facts_row[1].metric("Time zone", zone.split("/")[-1].replace("_", " "),
                            icon=":material/schedule:", help=zone)
        facts_row[2].metric(
            "Data", "on disk" if cached.exists() else "needs fetching",
            icon=":material/database:",
        )
        with facts_row[3]:
            known = load_registry().get(f"{latitude:+.2f}_{longitude:+.2f}", {})
            name = st.text_input(
                "Name this spot",
                value=preset or known.get("name") or f"{latitude:+.2f}, {longitude:+.2f}",
                key=f"name_{latitude:+.2f}_{longitude:+.2f}",
            )
            if st.button(":material/arrow_forward: Use this location",
                         type="primary", width="stretch"):
                enter_spot(latitude, longitude, zone, name)
                st.rerun()
    st.stop()


# Open on a sport rather than "Any wind", which cannot be rated or compared.
st.session_state.setdefault("activity", "Dinghy")
activity = st.session_state["activity"]
spot = st.session_state["spot"]
LATITUDE, LONGITUDE = spot["lat"], spot["lon"]
LOCAL_TZ, LOCATION_NAME = spot["zone"], spot["name"]
dataset_path = dataset_for(LATITUDE, LONGITUDE)

# Navigation: step back one screen without losing the location.
# One back / forward pair, plus the reset to the very beginning.
# Back steps out of a view to the options, or off the options back to the map.
# Forward returns to whichever view you last left.
current_mode = st.session_state.get("mode")
nav = st.container(horizontal=True)

if nav.button(":material/arrow_back: Back", type="secondary"):
    if current_mode is not None:
        st.session_state["last_mode"] = current_mode
        st.session_state["mode"] = None
    else:
        for key in ("spot", "clicked", "preset_applied"):
            st.session_state.pop(key, None)
    st.rerun()

forward_target = st.session_state.get("last_mode")
if nav.button(
    ":material/arrow_forward: Forward",
    type="secondary",
    disabled=current_mode is not None or forward_target is None,
):
    st.session_state["mode"] = forward_target
    st.session_state["rating_fresh"] = forward_target == "rating"
    st.rerun()

if nav.button(":material/home: Start over", type="tertiary"):
    for key in ("spot", "clicked", "mode", "preset_applied", "last_mode"):
        st.session_state.pop(key, None)
    st.rerun()


# --- Stage 2: make sure this location has data ----------------------------

if not dataset_path.exists():
    st.subheader(f"Download the record for {LOCATION_NAME}")
    st.caption(
        f"ERA5 cell {LATITUDE:+.2f}, {LONGITUDE:+.2f} · {LOCAL_TZ}. "
        "A longer range takes longer to prepare on Copernicus' side."
    )
    span = pick(
        st.segmented_control(
            "How far back", ["10 years", "20 years", "50 years", "Everything (1940–)"],
            default="20 years", key="span",
        ),
        "20 years",
    )
    years = {"10 years": 10, "20 years": 20, "50 years": 50}.get(span)
    first = date(1940, 1, 1) if years is None else date(date.today().year - years + 1, 1, 1)

    st.caption(f"Requesting {first} to {date.today()}.")
    if st.button("Fetch from ERA5", type="primary"):
        with st.spinner("Copernicus is preparing the data — this can take a while..."):
            try:
                client = cds_client()
                download_path = data_dir / f"download_{LATITUDE:+.2f}_{LONGITUDE:+.2f}.zip"
                client.retrieve(
                    "reanalysis-era5-single-levels-timeseries",
                    {
                        "variable": [
                            "10m_u_component_of_wind",
                            "10m_v_component_of_wind",
                            "2m_temperature",
                        ],
                        "location": {"latitude": LATITUDE, "longitude": LONGITUDE},
                        "date": [f"{first}/{date.today()}"],
                        "data_format": "netcdf",
                    },
                    str(download_path),
                )
                unpack_download(download_path, dataset_path)
                st.success("Downloaded.")
                st.rerun()
            except Exception as error:
                st.error(f"Could not fetch that location: {error}")
                st.caption(
                    "If this is the deployed app, add your Copernicus token "
                    "under Settings → Secrets (see .streamlit/secrets.toml.example)."
                )
    st.stop()

df = load_wind(dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ)
data_start = df.index.min().date()
data_end = df.index.max().date()


RATING_YEARS = 20


# Air temperature bands (Celsius) -> what you would put on. Air, not water,
# so treat it as a hint rather than gospel.
WETSUIT = [
    (12, "5/4 wetsuit, boots and gloves"),
    (16, "4/3 wetsuit"),
    (20, "3/2 wetsuit"),
    (24, "shorty or a top"),
    (99, "boardshorts"),
]


def kit_for(celsius):
    for limit, advice in WETSUIT:
        if celsius < limit:
            return advice
    return WETSUIT[-1][1]


def what_to_expect(rating, activity, session_text):
    """Turn the numbers into the sentences you would actually say out loud."""
    groove = rating["groove_pct"]
    low, high = ACTIVITIES[activity]["groove"]
    verdict = (
        "a reliable spot" if groove >= 55 else
        "worth the trip in season" if groove >= 35 else
        "a sometimes spot" if groove >= 20 else
        "usually too light"
    )
    lines = [
        f"For **{activity.lower()}** this is **{verdict}**. Sailable wind "
        f"({low}–{high} kt) turns up on **{groove:.0f}% of days** during "
        f"{session_text}.",
        f"Best odds in **{calendar.month_name[rating['best_month']]}** "
        f"({rating['best_month_pct']:.0f}% of days); thinnest in "
        f"**{calendar.month_name[rating['quiet_month']]}**.",
        f"The wind normally arrives from **{rating['prevailing']}** and is "
        f"strongest around **{rating['best_window']}**.",
    ]
    if rating["too_light"] >= 40:
        lines.append(
            f"Be ready for calms: **{rating['too_light']:.0f}% of days** never "
            f"reach {low} kt."
        )
    warmth = rating.get("warmth")
    if warmth:
        lines.append(
            f"On days you would sail it averages **{warmth['on_good_days']:.0f}°C** "
            f"— {kit_for(warmth['on_good_days'])}. Ranges from "
            f"{warmth['coolest']:.0f}°C in {calendar.month_name[warmth['coolest_month']]} "
            f"to {warmth['warmest']:.0f}°C in {calendar.month_name[warmth['warmest_month']]}."
        )
    else:
        lines.append(
            "_No temperature in this download — refetch the location to include it._"
        )
    return lines


@st.cache_data(show_spinner=False)
def rate_location(path, mtime, zone, first_year, activity, windows):
    """Score a spot for one activity, over the hours you actually sail. The
    weights are a judgement call — the four parts are shown so you can
    disagree."""
    frame = load_wind(path, mtime, zone)
    usable = frame[frame.index.year >= first_year]
    usable = usable[sessions_mask(usable.index, windows)]
    knots = usable["speed"] * MS_TO_KNOTS

    low, high = ACTIVITIES[activity]["groove"]
    by_day = knots.resample("1D")
    daily = by_day.mean().dropna()
    spread = (by_day.std() / by_day.mean()).dropna()

    in_groove = daily.between(low, high)
    groove = in_groove.mean() * 100
    too_light = (daily < low).mean() * 100
    big = (by_day.max().dropna() >= high).mean() * 100
    steadiness = max(0.0, 1 - float(spread.mean()) / GUSTY_CV)

    per_month = in_groove.groupby(daily.index.month).mean() * 100
    good_months = int((per_month >= 40).sum())
    per_hour = knots.groupby(knots.index.hour).mean()
    direction = wind_direction(usable["u10"], usable["v10"])

    parts = {
        "Days in the groove": (
            min(groove / 6, 10),
            f"{groove:.0f}% of days sit in {low}–{high} kt",
        ),
        "Season length": (
            good_months / 12 * 10,
            f"{good_months} of 12 months are good 40% of the time",
        ),
        "Steadiness": (
            steadiness * 10,
            f"typical day varies {float(spread.mean()) * 100:.0f}% around its mean",
        ),
        "Big days": (min(big / 3, 10), f"{big:.0f}% of days touch {high} kt"),
    }
    overall = sum(score * RATING_WEIGHTS[name] for name, (score, _) in parts.items())

    start_hour, end_hour, _ = best_window(knots)

    warmth = None
    if "temp_c" in usable.columns:
        temps = usable["temp_c"]
        monthly = temps.groupby(temps.index.month).mean().reindex(range(1, 13))
        on_groove_days = temps[temps.index.normalize().isin(
            daily[in_groove].index.normalize()
        )]
        warmth = {
            "mean": float(temps.mean()),
            "curve": monthly.tolist(),
            "warmest_month": int(monthly.idxmax()),
            "warmest": float(monthly.max()),
            "coolest_month": int(monthly.idxmin()),
            "coolest": float(monthly.min()),
            "on_good_days": float(on_groove_days.mean()) if len(on_groove_days) else float(temps.mean()),
        }

    return {
        "warmth": warmth,
        "overall": overall,
        "parts": parts,
        "activity": activity,
        "groove_pct": groove,
        "too_light": too_light,
        "beaufort": beaufort(float(knots.mean())),
        "best_window": f"{start_hour:02d}:00–{end_hour:02d}:59",
        "month_curve": per_month.reindex(range(1, 13)).tolist(),
        "hour_curve": per_hour.reindex(range(24)).tolist(),
        "direction": direction.to_numpy(),
        "knots": knots.to_numpy(),
        "best_month": int(per_month.idxmax()),
        "best_month_pct": float(per_month.max()),
        "quiet_month": int(per_month.idxmin()),
        "prevailing": prevailing_direction(direction),
        "years": f"{first_year}–{frame.index.max().year}",
    }


if "mode" not in st.session_state:
    st.session_state["mode"] = None


# --- Start screen ---------------------------------------------------------

if st.session_state["mode"] is None:
    st.html(
        '<div class="hero">'
        '<div class="hero-eyebrow">Your spot</div>'
        f'<div class="hero-title reveal">{reveal(LOCATION_NAME)}</div>'
        '<hr class="accent-rule">'
        '<div class="stat-strip">'
        f"<span><b>{abs(LATITUDE):.2f}°{'N' if LATITUDE >= 0 else 'S'}, "
        f"{abs(LONGITUDE):.2f}°{'E' if LONGITUDE >= 0 else 'W'}</b></span>"
        f"<span><b>{data_start.year}–{data_end.year}</b> of hourly readings</span>"
        f"<span>local time <b>{LOCAL_TZ}</b></span>"
        "</div></div>"
    )
    locator_map(LATITUDE, LONGITUDE, key="start_map", height=300)

    st.write("")
    st.session_state["activity"] = pick(
        st.segmented_control(
            "Step 2 · What are you sailing?", list(ACTIVITIES),
            default=st.session_state["activity"],
            format_func=lambda name: f"{ACTIVITIES[name]['icon']} {name}",
            key="activity_pick",
        ),
        st.session_state["activity"],
    )
    chosen = st.session_state["activity"]
    groove_low, groove_high = ACTIVITIES[chosen]["groove"]
    if can_score(chosen):
        st.caption(
            f"Scoring and the speed bands use {groove_low}–{groove_high} kt as the "
            "range worth going out in."
        )
    else:
        st.caption(
            f"{chosen} counts every day from {groove_low} to {groove_high} kt, so "
            "it is for exploring only. Pick a sport to rate or compare spots."
        )

    st.write("")
    st.markdown("###### Step 3 · What do you want to see?")
    cards = [
        (
            ":material/insights: Explore the data",
            "Windy days per month, sessions by hour, year-on-year trends and "
            "wind direction.",
            "Start exploring",
            "explore",
        ),
        (
            ":material/star: Rate this spot",
            "One score out of ten for what you sail: days in the groove, "
            "season length and steadiness.",
            "Rate the location",
            "rating",
        ),
        (
            ":material/compare_arrows: Compare two spots",
            "Put this spot against another you have downloaded: scores, months "
            "and wind direction.",
            "Compare locations",
            "compare",
        ),
    ]
    # Fixed height keeps all three cards identical; the stretch spacer pins
    # each button to the bottom however many lines the blurb wraps to.
    for column, (heading, blurb, action, target) in zip(st.columns(3), cards):
        with column.container(border=True, height=230):
            st.markdown(f"#### {heading}")
            st.write(blurb)
            st.space("stretch")
            locked = target != "explore" and not can_score(chosen)
            if st.button(action, width="stretch", type="secondary", disabled=locked):
                st.session_state["mode"] = target
                st.session_state.pop("last_mode", None)
                st.session_state["rating_fresh"] = target == "rating"
                st.rerun()
    st.stop()


# --- Settings ------------------------------------------------------------
# Below the start screen on purpose: the sidebar only appears once you are
# inside a view, and then it carries everything that changes what you see.
# Shared by every view, so the rating cannot quietly score different hours
# than the ones you picked.

st.sidebar.header("Settings")

activity = st.sidebar.selectbox(
    "What you are sailing", list(ACTIVITIES),
    index=list(ACTIVITIES).index(st.session_state["activity"]),
    key="activity_side",
)
st.session_state["activity"] = activity
groove_low, groove_high = ACTIVITIES[activity]["groove"]
st.sidebar.caption(f"Sailable wind {groove_low}–{groove_high} kt")

unit = pick(
    st.sidebar.segmented_control("Units", ["knots", "m/s"], default="knots", key="unit"), "knots"
)
factor = MS_TO_KNOTS if unit == "knots" else 1.0

sessions = []
with st.sidebar.expander("When you sail", expanded=False):
    st.caption("Only count the hours you are actually on the water.")
    split_sessions = st.checkbox("Filter by sailing session", value=True)
    if split_sessions:
        morning = st.slider("Session 1", 0, 23, (8, 11), format="%d:00")
        sessions.append((f"Session 1 · {morning[0]:02d}:00–{morning[1]:02d}:00", morning))
        if st.checkbox("Second session", value=True):
            afternoon = st.slider("Session 2", 0, 23, (14, 17), format="%d:00")
            sessions.append(
                (f"Session 2 · {afternoon[0]:02d}:00–{afternoon[1]:02d}:00", afternoon)
            )
    else:
        sessions.append(("All hours", None))
st.sidebar.caption(" · ".join(label.split("· ")[-1] for label, _ in sessions))

SESSION_WINDOWS = tuple(hours for _, hours in sessions)
SESSION_TEXT = " and ".join(
    "all day" if hours is None else f"{hours[0]:02d}:00–{hours[1]:02d}:59"
    for hours in SESSION_WINDOWS
)

# Forward, or switching sport in the sidebar, can land here with a range
# that has nothing to score.
if st.session_state["mode"] in ("rating", "compare") and not can_score(activity):
    st.info(
        f"**{activity}** counts every day as sailable, so there is nothing to "
        "score. Pick what you sail in the sidebar."
    )
    st.stop()


# --- Compare two locations ------------------------------------------------

if st.session_state["mode"] == "compare":
    activity = st.session_state["activity"]
    others = {
        key: entry for key, entry in downloaded_locations().items()
        if entry["path"] != dataset_path
    }

    st.html(
        '<div class="reveal" style="font-size:1.5rem;font-weight:650">'
        + reveal("Head to head")
        + '</div><hr class="accent-rule">'
    )
    if not others:
        st.info(
            "Only one location is downloaded so far. Use **Pick another location** "
            "to choose a second spot and fetch it, then come back here."
        )
        st.stop()

    choice = st.sidebar.selectbox(
        "Compare against", list(others),
        format_func=lambda key: others[key]["name"],
    )
    rival = others[choice]

    first_year = max(data_start.year, data_end.year - RATING_YEARS + 1)
    with st.spinner("Scoring both spots..."):
        here = rate_location(
            dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ, first_year,
            activity, SESSION_WINDOWS,
        )
        there = rate_location(
            rival["path"], rival["path"].stat().st_mtime, rival["zone"],
            first_year, activity, SESSION_WINDOWS,
        )

    contenders = [(LOCATION_NAME, here), (rival["name"], there)]
    winner = max(contenders, key=lambda pair: pair[1]["overall"])[0]
    margin = abs(here["overall"] - there["overall"])

    st.caption(
        f"Judged for **{activity.lower()}** over {here['years']}, "
        f"counting {SESSION_TEXT} local at each spot."
    )

    # Scores side by side, the stronger one called out.
    for column, (name, verdict) in zip(st.columns(2), contenders):
        with column.container(border=True):
            st.markdown(f"**{name}**")
            st.metric(
                "Score", f"{verdict['overall']:.1f} / 10",
                delta="ahead" if name == winner and margin > 0.05 else None,
                delta_arrow="off",
                chart_data=verdict["month_curve"], chart_type="area",
                help="Share of days in the groove, month by month",
            )
            st.caption(
                f"{verdict['groove_pct']:.0f}% of days in the groove · "
                f"best in {calendar.month_name[verdict['best_month']]} · "
                f"wind from {verdict['prevailing']}"
            )

    if margin < 0.05:
        st.info("Too close to call — these two score the same.")
    else:
        st.html(
            '<div class="reveal" style="font-size:1.3rem;font-weight:650;color:#7bd88f">'
            + reveal(f"{winner} comes out ahead by {margin:.1f} points")
            + "</div>"
        )

    # Month by month, which spot puts you on the water more often.
    months = [calendar.month_abbr[m] for m in range(1, 13)]
    monthly = pd.DataFrame(
        [
            {"month": months[m], "order": m, "spot": name, "pct": verdict["month_curve"][m]}
            for name, verdict in contenders
            for m in range(12)
        ]
    )
    st.altair_chart(
        alt.Chart(monthly)
        .mark_line(point=True, strokeWidth=2)
        .encode(
            x=alt.X("month:N", sort=months, title=None),
            y=alt.Y("pct:Q", title="% of days in the groove"),
            color=alt.Color("spot:N", title=None,
                            scale=alt.Scale(range=["#fb8500", "#8ecae6"])),
            tooltip=["spot:N", "month:N", alt.Tooltip("pct:Q", format=".0f")],
        )
        .properties(height=260),
        width="stretch",
    )

    # Where the wind comes from at each.
    for column, (name, verdict) in zip(st.columns(2), contenders):
        with column:
            st.caption(f"**{name}** — wind direction")
            st.pyplot(
                draw_rose(verdict["direction"], verdict["knots"],
                          ACTIVITIES[activity]["ladder"], "knots"),
                width="stretch",
            )

    with st.expander("Score breakdown"):
        breakdown = pd.DataFrame(
            {name: {part: round(score, 1) for part, (score, _) in verdict["parts"].items()}
             for name, verdict in contenders}
        )
        st.dataframe(breakdown, width="stretch")
    st.stop()


# --- Rating ---------------------------------------------------------------

if st.session_state["mode"] == "rating":
    first_year = max(data_start.year, data_end.year - RATING_YEARS + 1)
    with st.spinner("Reading the last 20 years..."):
        rating = rate_location(
            dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ, first_year,
            activity, SESSION_WINDOWS,
        )

    activity = st.session_state["activity"]
    fresh = st.session_state.pop("rating_fresh", False)
    stars = round(rating["overall"] / 2)
    hero = st.empty()

    def draw_hero(value):
        hero.html(
            f'<div class="rating-hero">'
            f'<div class="rating-score">{value:.1f}<span style="font-size:1.6rem;'
            f'opacity:.55"> / 10</span></div>'
            '<div class="rating-stars">'
            + "".join(
                f"<span>{'★' if index < stars else '☆'}</span>" for index in range(5)
            )
            + "</div>"
            + "</div>"
        )

    if fresh:
        # Count the score up so the verdict lands rather than just appearing.
        for step in np.linspace(0, rating["overall"], 24):
            draw_hero(float(step))
            time.sleep(0.022)
    draw_hero(rating["overall"])

    st.html(
        '<div class="reveal" style="font-size:1.15rem;font-weight:600">'
        + reveal(f"{LOCATION_NAME} — {activity.lower()}", delay=0.15)
        + "</div>"
    )
    st.caption(
        f"Scored on {rating['years']}, counting {SESSION_TEXT} local."
    )

    expect_col, parts_col = st.columns([2, 3], gap="medium")

    with expect_col:
        with st.container(border=True):
            st.markdown("##### :material/lightbulb: What to expect")
            for line in what_to_expect(rating, activity, SESSION_TEXT):
                st.markdown(line)

    with parts_col:
        for name, (score, detail) in rating["parts"].items():
            label, bar = st.columns([2, 5], vertical_alignment="center")
            label.markdown(f"**{name}**  \n<small>{detail}</small>", unsafe_allow_html=True)
            meter = bar.empty()
            if fresh:
                for step in np.linspace(0, score, 12):
                    # st.progress rejects numpy scalars, so hand it a plain float.
                    meter.progress(float(step) / 10, text=f"{step:.1f} / 10")
                    time.sleep(0.012)
            meter.progress(float(score) / 10, text=f"{score:.1f} / 10")

    st.divider()
    highlights = st.columns(4)
    highlights[0].metric(
        "Best month", calendar.month_name[rating["best_month"]], border=True,
        icon=":material/trending_up:",
        help=f"{rating['best_month_pct']:.0f}% of days reach 9 kt",
        chart_data=rating["month_curve"], chart_type="area",
    )
    highlights[1].metric(
        "Quietest month", calendar.month_name[rating["quiet_month"]], border=True,
        icon=":material/trending_down:",
    )
    highlights[2].metric(
        "Windiest stretch", rating["best_window"], border=True,
        icon=":material/schedule:", help="Best three hours of an average day",
        chart_data=rating["hour_curve"], chart_type="area",
    )
    highlights[3].metric(
        "Wind comes from", rating["prevailing"], border=True,
        icon=":material/explore:", help=f"Typically a {rating['beaufort']}",
    )
    if rating.get("warmth"):
        warmth = rating["warmth"]
        st.metric(
            "Air on sailable days", f"{warmth['on_good_days']:.0f}°C", border=True,
            icon=":material/thermostat:", delta=kit_for(warmth["on_good_days"]),
            delta_arrow="off", chart_data=warmth["curve"], chart_type="area",
            help="Average through your sailing hours, month by month",
        )

    st.divider()
    map_col, rose_col, note_col = st.columns([3, 2, 2])
    with map_col:
        locator_map(LATITUDE, LONGITUDE, key="rating_map", height=340)
    with rose_col:
        st.pyplot(
            draw_rose(rating["direction"], rating["knots"], ACTIVITIES[activity]["ladder"], "knots"),
            width="stretch",
        )
    with note_col:
        st.markdown("**How this is scored**")
        st.caption(
            "A weighted blend of "
            + ", ".join(
                f"{name.lower()} ({weight:.0%})" for name, weight in RATING_WEIGHTS.items()
            )
            + ". Whether the wind is any good counts most; how much it wanders "
            "through the day counts least. These weights are my choice — the four "
            "parts are shown above so you can weigh them yourself."
        )
    st.stop()


# --- What period to show (sidebar, alongside the other controls) ----------

st.sidebar.subheader("What to look at")

view = st.sidebar.radio(
    "View",
    ["Windy days per month", "Date range", "Month across years"],
    key="view",
)
month_view = view == "Month across years"
windy_view = view == "Windy days per month"

lookback_options = {"5 years": 5, "10 years": 10, "15 years": 15, "20 years": 20, "All": None}

if windy_view:
    lookback_label = st.sidebar.selectbox(
        "Compare over", list(lookback_options),
        index=list(lookback_options).index("All"), key="windy_lookback",
    )
    lookback = lookback_options[lookback_label]
    day_rule = st.sidebar.radio(
        "A day counts when the wind",
        ["averages the speed", "reaches the speed"],
        key="day_rule",
    )
    how = "max" if day_rule == "reaches the speed" else "mean"

    first_year = data_start.year if lookback is None else data_end.year - lookback + 1
    first_year = max(first_year, data_start.year)
    window = df[df.index.year >= first_year]
    period_label = f"all months, {first_year}–{data_end.year}"
elif month_view:
    month = st.sidebar.selectbox(
        "Month", list(range(1, 13)),
        index=data_end.month - 1,
        format_func=lambda m: calendar.month_name[m],
        key="month",
    )
    lookback_label = st.sidebar.selectbox(
        "Compare over", list(lookback_options),
        index=list(lookback_options).index("10 years"), key="lookback",
    )
    lookback = lookback_options[lookback_label]

    first_year = data_start.year if lookback is None else data_end.year - lookback + 1
    first_year = max(first_year, data_start.year)
    window = df[(df.index.month == month) & (df.index.year >= first_year)]
    period_label = f"{calendar.month_name[month]} {first_year}–{data_end.year}"
else:
    quick_ranges = {"1 month": 30, "1 year": 365, "10 years": 3650, "Everything": None}
    chosen = st.sidebar.segmented_control(
        "Jump to", list(quick_ranges), default=None, key="quick"
    )
    if chosen is not None:
        days = quick_ranges[chosen]
        first = data_start if days is None else max(data_start, data_end - timedelta(days=days))
        st.session_state["time_range"] = (first, data_end)
    if "time_range" not in st.session_state:
        st.session_state["time_range"] = (
            max(data_start, data_end - timedelta(days=365)),
            data_end,
        )
    time_range = st.sidebar.date_input(
        "From / to", min_value=data_start, max_value=data_end, key="time_range"
    )
    if len(time_range) != 2:
        st.warning("Pick both a start and an end date.")
        st.stop()
    window = df.loc[str(time_range[0]) : str(time_range[1])]
    period_label = f"{time_range[0]} to {time_range[1]}"

if window.empty:
    st.warning("No data in the selected period.")
    st.stop()


# --- Refinements (sidebar) ------------------------------------------------

full_min = float(df["speed"].min() * factor)
full_max = float(df["speed"].max() * factor)
with st.sidebar.expander("Wind range and detail", expanded=False):
    low, high = st.slider(
        f"Min / max ({unit})",
        min_value=round(full_min, 1),
        max_value=round(full_max, 1),
        value=(round(full_min, 1), round(full_max, 1)),
        step=0.5,
        # Separate keys so switching units doesn't clamp the other unit's selection.
        key=f"speed_band_{unit}",
    )
    aggregations = {
        "Hourly": None,
        "Daily mean": ("1D", "mean"),
        "Daily max": ("1D", "max"),
        "Monthly mean": ("1MS", "mean"),
    }
    aggregation = st.selectbox("Aggregation", list(aggregations), index=1)
rule = aggregations[aggregation]
st.sidebar.caption(f"{low:g}–{high:g} {unit} · {aggregation.lower()}")


# --- Build one series per session -----------------------------------------


def prepare(hours):
    """Restrict to the session hours, resample, and convert to the chosen unit."""
    sub = window[hour_mask(window.index, hours)]
    if sub.empty:
        return sub
    if rule is not None:
        freq, how = rule
        resampled = sub.resample(freq)
        sub = pd.DataFrame({"speed": getattr(resampled["speed"], how)()}).dropna()
    return sub[["speed"]] * factor


prepared = [(label, prepare(hours)) for label, hours in sessions]
active = [(label, s) for label, s in prepared if not s.empty]

if windy_view:
    # One set of hours drives the metrics, the month chart and the rose.
    hour_choices = {label: hours for label, hours in sessions}
    hour_choices["All day"] = None
    picked_hours_label = st.sidebar.selectbox(
        "Hours to count", list(hour_choices), key="windy_hours"
    )
    picked_hours = hour_choices[picked_hours_label]
    summary_items = [(picked_hours_label, prepare(picked_hours))]
else:
    summary_items = prepared

st.caption(f"Showing **{period_label}** · {aggregation.lower()} · {unit}")


# --- Summary --------------------------------------------------------------

st.caption(
    f"Across **{period_label}**, using the {aggregation.lower()} of each period. "
    "“In wind range” is how often that value landed inside your min/max slider."
)

for label, series in summary_items:
    st.markdown(f"**{label}**")
    cols = st.columns(4)
    if series.empty:
        for i, name in enumerate(("Max", "Min", "Mean")):
            cols[i].metric(f"{name} ({unit})", "—")
        cols[3].metric("In wind range", "0")
        continue

    in_band = series["speed"].between(low, high)
    selected = series.loc[in_band, "speed"]
    if selected.empty:
        for i, name in enumerate(("Max", "Min", "Mean")):
            cols[i].metric(f"{name} ({unit})", "—")
    else:
        cols[0].metric(
            f"Max ({unit})", f"{selected.max():.1f}", help=str(selected.idxmax().date())
        )
        cols[1].metric(
            f"Min ({unit})", f"{selected.min():.1f}", help=str(selected.idxmin().date())
        )
        cols[2].metric(f"Mean ({unit})", f"{selected.mean():.1f}")
    share = in_band.sum() / len(series) * 100
    cols[3].metric(
        "In wind range", f"{share:.0f}%", help=f"{int(in_band.sum()):,} of {len(series):,} points"
    )


# --- Plots ----------------------------------------------------------------

if windy_view:
    st.html(
        '<div class="reveal" style="font-size:1.5rem;font-weight:650">'
        + reveal("Windy days per month")
        + '</div><hr class="accent-rule">'
    )

    thresholds = ladder_for(activity, unit)
    sub = window[hour_mask(window.index, picked_hours)]
    if sub.empty:
        st.warning("No data for the selected hours.")
        st.stop()

    daily = getattr(sub.resample("1D")["speed"], how)().dropna() * factor
    table = pd.DataFrame(
        {
            threshold: (daily > threshold).groupby(daily.index.month).mean().reindex(range(1, 13))
            * 100
            for threshold in thresholds
        }
    )

    chart_col, rose_col = st.columns([3, 2], gap="medium")

    with chart_col:
        month_names = [calendar.month_abbr[m] for m in range(1, 13)]
        records = [
            {
                "month": m,
                "name": calendar.month_abbr[m],
                "band": f"> {threshold:g}",
                "pct": 0.0 if pd.isna(table.loc[m, threshold]) else float(table.loc[m, threshold]),
            }
            for m in range(1, 13)
            for threshold in thresholds
        ]
        chart_data = pd.DataFrame(records)

        click = alt.selection_point(name="month_click", fields=["month"], on="click")
        layers = []
        for threshold, color in zip(thresholds, THRESHOLD_COLORS):
            layers.append(
                alt.Chart(chart_data[chart_data["band"] == f"> {threshold:g}"])
                .mark_bar(height=13, cornerRadius=2)
                .encode(
                    y=alt.Y("name:N", sort=month_names, title=None),
                    x=alt.X(
                        "pct:Q",
                        scale=alt.Scale(domain=[0, 100]),
                        title="% of days",
                        axis=alt.Axis(values=[0, 25, 50, 75, 100], format="d"),
                    ),
                    color=alt.value(color),
                    opacity=alt.condition(click, alt.value(1.0), alt.value(0.35)),
                    tooltip=[
                        alt.Tooltip("name:N", title="month"),
                        alt.Tooltip("band:N", title=f"over ({unit})"),
                        alt.Tooltip("pct:Q", title="% of days", format=".0f"),
                    ],
                )
            )
        # Invisible full-width bar on top: gives every row a click target even
        # where the coloured bars stop short.
        layers.append(
            alt.Chart(chart_data[chart_data["band"] == f"> {thresholds[0]:g}"])
            .mark_bar(height=13, opacity=0)
            .encode(y=alt.Y("name:N", sort=month_names, title=None), x=alt.value(alt.expr("width")))
            .add_params(click)
        )
        chart = alt.layer(*layers).properties(height=560)
        event = st.altair_chart(chart, width="stretch", on_select="rerun", key="windy_pick")

        selected_rows = (event.selection or {}).get("month_click") or []
        selected_month = selected_rows[0]["month"] if selected_rows else data_end.month
        st.caption("Click a month to see where its wind comes from →")

    with rose_col:
        rose_source = sub[sub.index.month == selected_month]

        if rose_source.empty:
            st.info("No readings for that month.")
        else:
            direction = wind_direction(rose_source["u10"], rose_source["v10"])
            speed = rose_source["speed"] * factor
            knots = rose_source["speed"] * MS_TO_KNOTS
            start, end, strength = best_window(knots)
            with st.container(border=True):
                st.markdown(
                    f"**{calendar.month_name[selected_month]}** · from "
                    f"**{prevailing_direction(direction)}** · "
                    f"{speed.mean():.1f} {unit} average · windiest "
                    f"**{start:02d}–{end:02d}h** at {strength:.0f} kt"
                )
                st.pyplot(draw_rose(direction, speed, thresholds, unit), width="stretch")
                # The rose says which way; the map says if that way is water.
                locator_map(
                    LATITUDE, LONGITUDE, key=f"rose_map_{selected_month}", height=210
                )

    st.caption(
        f"Share of days in {period_label} where the wind "
        f"{'reached' if how == 'max' else 'averaged above'} each speed, "
        f"counting only {picked_hours_label.lower()}."
    )

    with st.expander("Exact percentages"):
        st.dataframe(
            table.set_axis([calendar.month_name[m] for m in range(1, 13)]).round(0),
            width="stretch",
        )
elif month_view:
    st.subheader(f"{calendar.month_name[month]} compared across years")
    style = st.sidebar.radio(
        "Show as",
        ["Year by year", "Spread", "Calendar", "Time of day"],
        key="chart_style",
    )

    years = sorted({y for _, s in active for y in set(s.index.year)})

    if style == "Year by year":
        # A line per session: differences between years read far more clearly
        # than bars, and the shaded band shows the normal spread.
        fig, ax = plt.subplots(figsize=(12, 4.5))
        for (label, series), color in zip(active, SESSION_COLORS):
            grouped = series.groupby(series.index.year)["speed"]
            means = grouped.mean().reindex(years)
            spread = grouped.std().reindex(years)
            ax.fill_between(
                years, means - spread, means + spread, color=color, alpha=0.12, linewidth=0
            )
            ax.plot(years, means, color=color, marker="o", ms=4, lw=1.6, label=label)
            ax.axhline(means.mean(), color=color, ls="--", lw=0.9, alpha=0.7)
        ax.set_ylabel(unit)
        ax.set_xticks(years)
        ax.set_xticklabels(years, rotation=90 if len(years) > 20 else 0, fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize="small")
        ax.set_title("Line: average for the month · shaded: typical spread · dashed: long-run normal")

    elif style == "Spread":
        # Box plots answer "how reliable is the wind", not just the average.
        fig, ax = plt.subplots(figsize=(12, 4.5))
        count = max(len(active), 1)
        box_width = 0.8 / count
        for i, ((label, series), color) in enumerate(zip(active, SESSION_COLORS)):
            by_year = [series.loc[series.index.year == y, "speed"].values for y in years]
            offset = -0.4 + box_width * (i + 0.5)
            bp = ax.boxplot(
                by_year,
                positions=np.arange(len(years)) + offset,
                widths=box_width * 0.85,
                patch_artist=True,
                showfliers=False,
                medianprops=dict(color="black", lw=1.2),
            )
            for patch in bp["boxes"]:
                patch.set_facecolor(color)
                patch.set_alpha(0.55)
                patch.set_edgecolor(color)
            ax.plot([], [], color=color, lw=6, alpha=0.55, label=label)
        ax.axhspan(low, high, color="grey", alpha=0.08)
        ax.set_ylabel(unit)
        ax.set_xticks(np.arange(len(years)))
        ax.set_xticklabels(years, rotation=90 if len(years) > 20 else 0, fontsize=8)
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend(loc="best", fontsize="small")
        ax.set_title("Box: middle half of all readings · line: median · shaded band: your wind range")

    elif style == "Calendar":
        # Every day of the month, one row per year — good for spotting whether
        # windy spells cluster early or late in the month.
        fig, axes = plt.subplots(
            len(active), 1, figsize=(12, 2.2 + 2.4 * len(active)), squeeze=False
        )
        for ax, ((label, series), color) in zip(axes.ravel(), zip(active, SESSION_COLORS)):
            frame = pd.DataFrame(
                {
                    "year": series.index.year,
                    "day": series.index.day,
                    "speed": series["speed"].values,
                }
            )
            grid = frame.pivot_table(index="year", columns="day", values="speed", aggfunc="mean")
            grid = grid.reindex(index=years, columns=range(1, 32))
            mesh = ax.imshow(grid.values, aspect="auto", cmap="viridis", interpolation="nearest")
            ax.set_yticks(range(len(years)))
            ax.set_yticklabels(years, fontsize=7)
            ax.set_xticks(range(0, 31, 2))
            ax.set_xticklabels(range(1, 32, 2), fontsize=7)
            ax.set_xlabel("day of month")
            ax.set_title(label, fontsize=10)
            fig.colorbar(mesh, ax=ax, label=unit, pad=0.01)

    else:  # Time of day
        # Which hours actually blow, averaged over every year in the window.
        fig, ax = plt.subplots(figsize=(12, 4.5))
        hourly = window["speed"] * factor
        by_hour = hourly.groupby(hourly.index.hour)
        mean_by_hour = by_hour.mean()
        ax.fill_between(
            mean_by_hour.index,
            by_hour.quantile(0.25),
            by_hour.quantile(0.75),
            color="tab:green",
            alpha=0.15,
            linewidth=0,
            label="middle half of readings",
        )
        ax.plot(mean_by_hour.index, mean_by_hour, color="tab:green", marker="o", ms=4, lw=1.6,
                label="average")
        for (label, hours), color in zip(sessions, SESSION_COLORS):
            if hours is None:
                continue
            ax.axvspan(hours[0], hours[1], color=color, alpha=0.15, label=label)
        ax.axhspan(low, high, color="grey", alpha=0.08)
        ax.set_xticks(range(0, 24))
        ax.set_xticklabels([f"{h:02d}" for h in range(24)], fontsize=8)
        ax.set_xlabel(f"hour of day ({LOCAL_TZ})")
        ax.set_ylabel(unit)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize="small")
        ax.set_title(f"Typical day in {calendar.month_name[month]}, averaged over {len(years)} years")

    style_figure(fig, *fig.axes)
    plt.tight_layout()
    st.pyplot(fig, width="stretch")

    with st.expander("Year-by-year numbers"):
        summaries = {}
        for label, series in active:
            grouped = series.groupby(series.index.year)["speed"]
            summaries[label] = pd.DataFrame(
                {"mean": grouped.mean(), "max": grouped.max(), "points": grouped.size()}
            )
        if summaries:
            st.dataframe(pd.concat(summaries, axis=1).round(1), width="stretch")
else:
    st.subheader(f"Wind speed — {aggregation.lower()}")

    fig, ax = plt.subplots(figsize=(12, 4))
    for (label, series), color in zip(prepared, SESSION_COLORS):
        if series.empty:
            continue
        banded = series["speed"].where(series["speed"].between(low, high))
        # Faint full trace for context, solid where it falls inside the speed band.
        ax.plot(series.index, series["speed"], color=color, lw=0.6, alpha=0.25)
        ax.plot(banded.index, banded, color=color, lw=0.8, label=label)
    ax.axhspan(low, high, color="grey", alpha=0.07)
    ax.set_ylabel(unit)
    if high > low:
        margin = (high - low) * 0.05
        ax.set_ylim(low - margin, high + margin)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize="small")
    style_figure(fig, ax)
    plt.tight_layout()
    st.pyplot(fig, width="stretch")

with st.expander("View data"):
    tables = []
    for label, series in active:
        table = series[series["speed"].between(low, high)].copy()
        table.insert(0, "session", label)
        tables.append(table)
    if tables:
        combined = pd.concat(tables).sort_index().round(2)
        st.dataframe(combined, width="stretch")
        st.download_button(
            ":material/download: Download as CSV",
            combined.to_csv().encode(),
            file_name=f"wind-{LATITUDE:+.2f}_{LONGITUDE:+.2f}.csv",
            mime="text/csv",
        )
    else:
        st.write("Nothing matches the current filters.")
