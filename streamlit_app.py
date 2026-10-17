import calendar
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import altair as alt
import cdsapi
import folium
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import xarray as xr
from folium.plugins import MiniMap
from streamlit_folium import st_folium
from timezonefinder import TimezoneFinder

st.set_page_config(
    page_title="Wind Atlas · hourly wind history", layout="wide",
    initial_sidebar_state="expanded",
)

# Goes up by 0.1 with every commit pushed to GitHub (2.9 -> 3.0).
APP_VERSION = "2.2"

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
    /* Pinned to the window, so it stays put on every screen and scroll. */
    .app-version {
        position: fixed; right: .9rem; bottom: .7rem; z-index: 999;
        font-size: .72rem; font-weight: 600; letter-spacing: .04em;
        color: #8b98a5; pointer-events: none;
    }
    </style>
    <div class="app-masthead">
        <span class="mark">🌬️ Wind Atlas</span>
        <span class="tag">Eighty years of hourly wind, anywhere you sail</span>
    </div>
    """
    f'<div class="app-version">v{APP_VERSION}</div>'
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
    .hero { animation: riseIn .55s ease both; padding: .2rem 0 1.1rem; position: relative; }
    .hero-title {
        font-size: 2.9rem; font-weight: 700; line-height: 1.1; letter-spacing: -.025em;
        color: #e8eef5;
    }
    .hero-sub {
        font-size: 1.02rem; opacity: .72; max-width: 52ch; margin-top: .6rem;
        line-height: 1.55; animation: riseIn .6s ease .12s both;
    }
    .hero-eyebrow {
        text-transform: uppercase; letter-spacing: .16em; font-size: .72rem;
        font-weight: 600; margin-bottom: .3rem; color: #fb8500; opacity: .9;
    }
    .stat-strip {
        display: flex; gap: 1.6rem; flex-wrap: wrap; opacity: .8;
        font-size: .86rem; margin-top: .55rem;
    }
    .stat-strip b { font-weight: 600; opacity: 1; color: #8ecae6; }

    /* Wind drifting across the hero. */
    @keyframes drift {
        from { transform: translateX(-18%); }
        to   { transform: translateX(18%); }
    }
    @keyframes breathe {
        0%, 100% { opacity: .45; }
        50%      { opacity: .9; }
    }

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

    /* Landing: the first screen anyone sees. */
    .landing {
        position: relative; overflow: hidden; padding: 2.4rem 0 1.6rem;
        animation: riseIn .55s ease both;
    }
    .landing::after {
        content: ""; position: absolute; z-index: 0; pointer-events: none;
        right: -12%; top: -40%; width: 60%; height: 140%;
        background: radial-gradient(closest-side, rgba(251, 133, 0, .16), transparent);
    }
    .landing > *:not(.hero-streaks) { position: relative; z-index: 1; }
    .landing-grid {
        display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr);
        gap: 2.5rem; align-items: end;
    }
    @media (max-width: 900px) { .landing-grid { grid-template-columns: 1fr; gap: 1.4rem; } }
    .landing-eyebrow {
        display: flex; align-items: center; gap: .6rem;
        font-size: .72rem; font-weight: 700; letter-spacing: .18em;
        text-transform: uppercase; color: #fb8500;
    }
    .landing-eyebrow::before {
        content: ""; width: 32px; height: 2px;
        background: linear-gradient(90deg, #fb8500, #ffb703);
    }
    .landing-title {
        font-size: clamp(2.4rem, 5.2vw, 4.4rem); font-weight: 800; line-height: 1.02;
        letter-spacing: -.04em; color: #e8eef5; margin-top: .8rem; max-width: 15ch;
    }
    .landing-title .wind {
        background: linear-gradient(90deg, #8ecae6, #ffb703 55%, #fb8500);
        -webkit-background-clip: text; background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .landing-sub {
        font-size: 1.08rem; line-height: 1.6; color: #a9b6c3; max-width: 56ch;
        margin-top: 1.1rem; animation: riseIn .6s ease .12s both;
    }
    .landing-steps {
        display: flex; flex-wrap: wrap; gap: .5rem 1.4rem; margin-top: 1.4rem;
        font-size: .9rem; color: #c5d0da; animation: riseIn .6s ease .2s both;
    }
    .landing-steps span { display: inline-flex; align-items: center; gap: .55rem; }
    .landing-steps b {
        display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem;
        border: 1px solid #26333f; border-radius: 4px; background: #141c25;
        color: #ffb703; font-size: .78rem;
    }
    .landing-facts {
        display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .6rem;
        animation: riseIn .6s ease .28s both;
    }
    .landing-facts div {
        border: 1px solid #26333f; border-left: 3px solid #fb8500; border-radius: 4px;
        background: rgba(20, 28, 37, .72); padding: .75rem .9rem;
    }
    .landing-facts div:nth-child(2n) { border-left-color: #8ecae6; }
    .landing-facts b {
        display: block; font-size: 1.45rem; font-weight: 800; letter-spacing: -.02em;
        color: #e8eef5;
    }
    .landing-facts span {
        font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; color: #8b98a5;
    }
    .section-label {
        display: flex; align-items: baseline; gap: .7rem; margin: 0 0 .2rem;
        font-size: 1.15rem; font-weight: 700; color: #e8eef5;
    }
    .section-label small { font-size: .82rem; font-weight: 500; color: #8b98a5; }

    /* The spot you just clicked: the one thing to act on next. */
    .st-key-picked_panel {
        border: 1px solid rgba(251, 133, 0, .65) !important; border-radius: 6px;
        background: linear-gradient(180deg, rgba(251, 133, 0, .08), transparent 70%);
        padding: 1rem 1.1rem;
    }
    /* Saved spots as list rows: hover finds them on the map, Open reopens. */
    [class*="st-key-spot_card_"] {
        border: 1px solid #26333f; border-left: 3px solid #8ecae6; border-radius: 4px;
        background: #111922; padding: .55rem .7rem .55rem .85rem;
        transition: background .15s ease, border-color .15s ease;
    }
    [class*="st-key-spot_card_"]:hover {
        border-left-color: #fb8500; border-color: rgba(251, 133, 0, .45);
        background: #16202b;
    }
    [class*="st-key-spot_card_"] p { margin-bottom: 0; line-height: 1.45; }
    /* Text and Open button share one line; the text wraps instead. */
    [class*="st-key-spot_card_"] { flex-wrap: nowrap !important; }
    [class*="st-key-spot_card_"] > div:first-child { flex: 1 1 auto !important; min-width: 0 !important; }
    [class*="st-key-spot_card_"] > div:last-child { flex: 0 0 auto !important; }

    /* The map sits in its own rounded panel rather than running edge to edge. */
    iframe[title="streamlit_folium.st_folium"] {
        border-radius: 6px;
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

    /* Every setting lives in the sidebar. Closed, its only way back is a
       faded arrow in the corner, so turn that into a button nobody misses. */
    [data-testid="stExpandSidebarButton"] {
        display: inline-flex !important; align-items: center; gap: .35rem;
        background: #fb8500 !important; border-radius: 999px !important;
        padding: .3rem .9rem .3rem .55rem !important;
        animation: settingsPulse 2.4s ease-out infinite;
    }
    [data-testid="stExpandSidebarButton"] span { color: #0b0f14 !important; }
    [data-testid="stExpandSidebarButton"]::after {
        content: "Settings"; color: #0b0f14; font-weight: 700; font-size: .9rem;
    }
    [data-testid="stExpandSidebarButton"]:hover { background: #ffb703 !important; }
    @keyframes settingsPulse {
        0%   { box-shadow: 0 0 0 0 rgba(251, 133, 0, .55); }
        70%  { box-shadow: 0 0 0 12px rgba(251, 133, 0, 0); }
        100% { box-shadow: 0 0 0 0 rgba(251, 133, 0, 0); }
    }
    /* Open, an accent edge sets it apart from the page. */
    [data-testid="stSidebar"] { border-right: 2px solid rgba(251, 133, 0, .35); }

    /* Colour accent on the dark ground. */
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

# Top of the wind slider in explore, in knots.
WIND_TOP = 50
LOOKBACK = {"10 years": 10, "20 years": 20, "All": None}
# Explore's charts. Brand orange and blue, each stepped to sit inside the
# dark-mode lightness band and stay apart under colour blindness (checked
# in OKLab against the app background).
GOOD_COLOR = "#e37100"   # wind and air both right
COLD_COLOR = "#2b8bc4"   # the wind is there, the warmth is not
# One hue, near-ground to bright: how often, on a dark background.
CHANCE_RAMP = ["#1f1710", "#4d2c0c", "#8a4a06", "#e37100", "#ffad5c"]
WATER_SHADE = "#8b98a5"
SURFACE = "#0a0e13"   # the app background, for gaps between marks

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

# Where the wind comes from: a model cell anywhere on earth, or a MeteoSwiss
# station that measured it. Both end up in the same file layout.
SOURCES = {
    "era5": ":material/public: ERA5 · worldwide",
    "meteoswiss": ":material/sensors: MeteoSwiss · Swiss stations",
}
# MeteoSwiss open data: free, no account, semicolon CSV per station.
OGD_ROOT = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
OGD_ITEMS = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-smn/items"
SWISS_CENTRE = (46.8, 8.2)


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


def station_dataset(abbr):
    """One cached netCDF per MeteoSwiss station."""
    return data_dir / f"station_{abbr}.nc"


def ogd_csv(url, **options):
    """MeteoSwiss serves semicolon-separated CSV in Windows-1252."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return pd.read_csv(io.BytesIO(response.content), sep=";", encoding="cp1252", **options)


@st.cache_data(ttl="1d", show_spinner="Loading the MeteoSwiss stations...")
def meteoswiss_stations():
    """SwissMetNet stations that measure hourly wind, and since when."""
    stations = ogd_csv(f"{OGD_ROOT}/ogd-smn_meta_stations.csv").set_index("station_abbr")
    inventory = ogd_csv(f"{OGD_ROOT}/ogd-smn_meta_datainventory.csv")
    offered = inventory.groupby("parameter_shortname")["station_abbr"].agg(set)
    windy = offered.get("fkl010h0", set()) & offered.get("dkl010h0", set())
    since = (
        inventory[inventory["parameter_shortname"] == "fkl010h0"]
        .set_index("station_abbr")["data_since"]
    )
    table = pd.DataFrame({
        "name": stations["station_name"],
        "lat": stations["station_coordinates_wgs84_lat"],
        "lon": stations["station_coordinates_wgs84_lon"],
        "height": stations["station_height_masl"],
    })
    table = table[table.index.isin(windy)].copy()
    table["since"] = pd.to_datetime(
        since.reindex(table.index), format="%d.%m.%Y %H:%M"
    ).dt.year
    table["has_temp"] = table.index.isin(offered.get("tre200h0", set()))
    return table.sort_values("name")


def nearest_station(stations, latitude, longitude):
    """The station closest to a click, and how far away it is in km."""
    # A flat projection is plenty across a country this size.
    east = np.radians(stations["lon"] - longitude) * np.cos(np.radians(latitude))
    north = np.radians(stations["lat"] - latitude)
    km = np.hypot(east, north) * 6371
    abbr = km.idxmin()
    return abbr, float(km[abbr])


def fetch_station(spot, target, progress=None):
    """Every hourly file MeteoSwiss holds for a station (one per decade, plus
    this year up to yesterday) joined into one record. Stored in the same
    layout as an ERA5 download, so everything downstream reads both.
    `progress(done, total)` hears about each file as it lands."""
    item = requests.get(f"{OGD_ITEMS}/{spot['station'].lower()}", timeout=60)
    item.raise_for_status()
    # Historical sorts before recent, so where the two meet, recent wins.
    urls = sorted(
        asset["href"] for name, asset in item.json()["assets"].items()
        if "_h_historical_" in name or name.endswith("_h_recent.csv")
    )
    if not urls:
        raise RuntimeError("MeteoSwiss lists no hourly data for this station")
    wanted = {"reference_timestamp", "fkl010h0", "dkl010h0", "tre200h0"}
    # MeteoSwiss serves each connection slowly, so fetch the files side by side.
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        jobs = [
            pool.submit(ogd_csv, url, usecols=lambda column: column in wanted)
            for url in urls
        ]
        for done, _ in enumerate(as_completed(jobs), 1):
            if progress:
                progress(done, len(jobs))
        # Joined in URL order, not arrival order, for the recent-wins rule.
        frame = pd.concat([job.result() for job in jobs], ignore_index=True)

    stamp = pd.to_datetime(frame["reference_timestamp"], format="%d.%m.%Y %H:%M")
    # Timestamps are UTC and mark the end of the hour (16:00 = 15:10–16:00),
    # so step each back to the hour it covers. Before 2018 the hour ran
    # 15:50–16:40 and already carries the right label.
    stamp = stamp - pd.to_timedelta((stamp >= "2018-01-01").astype(int), unit="h")
    frame = (
        frame.assign(time=stamp)
        .dropna(subset=["fkl010h0", "dkl010h0"])
        .drop_duplicates("time", keep="last")
        .sort_values("time")
    )
    if frame.empty:
        raise RuntimeError("This station has no wind readings yet")

    # Stations report speed and where the wind comes from; ERA5 gives the
    # eastward and northward parts. Convert so the two read the same.
    speed = frame["fkl010h0"].to_numpy(dtype="float32")
    bearing = np.radians(frame["dkl010h0"].to_numpy(dtype="float32"))
    data = {
        "u10": ("valid_time", -speed * np.sin(bearing)),
        "v10": ("valid_time", -speed * np.cos(bearing)),
    }
    if "tre200h0" in frame and frame["tre200h0"].notna().any():
        # Kelvin, as ERA5 stores it.
        data["t2m"] = ("valid_time", frame["tre200h0"].to_numpy(dtype="float32") + 273.15)

    # The file carries its own station details, so it lists properly even
    # without data/locations.json.
    dataset = xr.Dataset(
        data,
        coords={"valid_time": frame["time"].to_numpy(dtype="datetime64[ns]")},
        attrs={
            "source": "MeteoSwiss SwissMetNet (CC BY 4.0)",
            "station": spot["station"], "name": spot["name"],
            "lat": float(spot["lat"]), "lon": float(spot["lon"]),
            "height": float(spot["height"]),
        },
    )
    partial = target.with_suffix(".part")
    dataset.to_netcdf(partial)
    partial.replace(target)
    return target


def can_score(activity):
    """A range that starts at 0 kt counts every day as sailable, so a score
    would say nothing about the spot — such a choice is for exploring only."""
    return ACTIVITIES[activity]["groove"][0] > 0


def spot_key(spot):
    """Registry key: the grid cell for ERA5, the station code for MeteoSwiss."""
    if spot.get("station"):
        return f"station_{spot['station']}"
    return f"{spot['lat']:+.2f}_{spot['lon']:+.2f}"


def remember_location(spot):
    registry = load_registry()
    registry[spot_key(spot)] = spot
    REGISTRY.write_text(json.dumps(registry, indent=2))


def load_registry():
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def downloaded_locations():
    """Every grid cell and station already on disk, named where we know the name."""
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
    for path in sorted(data_dir.glob("station_*.nc")):
        entry = registry.get(path.stem)
        if entry is None:
            with xr.open_dataset(path) as ds:
                details = ds.attrs
                entry = {
                    "lat": float(details["lat"]), "lon": float(details["lon"]),
                    "name": str(details["name"]), "station": str(details["station"]),
                    "height": float(details["height"]),
                }
            entry["zone"] = timezone_at(entry["lat"], entry["lon"])
        found[path.stem] = {**entry, "path": path}
    return found


def coords_label(latitude, longitude):
    """36.75°N · 8.25°W — how a sailor reads a position."""
    return (
        f"{abs(latitude):.2f}°{'N' if latitude >= 0 else 'S'} · "
        f"{abs(longitude):.2f}°{'E' if longitude >= 0 else 'W'}"
    )


@st.cache_data(show_spinner=False)
def record_span(path, mtime):
    """First and last year in a downloaded file, read from its time axis alone."""
    with xr.open_dataset(path) as ds:
        times = pd.DatetimeIndex(ds["valid_time"].values)
    return int(times.min().year), int(times.max().year)


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


def sticky(key, default):
    """A widget key whose value outlives the views that hide the widget.
    Streamlit forgets a widget's state once it is not drawn, so the value is
    copied aside every run and put back before the widget is drawn again."""
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get(f"kept_{key}", default)
    st.session_state[f"kept_{key}"] = st.session_state[key]
    return key


def percent(value):
    """A share as people say it: a chance that is not quite zero is not 0%."""
    return "<1%" if 0 < value < 0.5 else f"{value:.0f}%"


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
/* A station you have not downloaded: present, but quieter than your spots. */
.spot-dot.idle {
    width: 9px; height: 9px; margin: 2px; background: #5b6b7a;
    border: 1.5px solid rgba(255,255,255,.6); box-shadow: 0 1px 4px rgba(0,0,0,.4);
}
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
.spot-hover-tip {
    background: #0a0e13; color: #e8eef5; border: 1px solid #fb8500;
    border-radius: 4px; font: 700 12px/1.2 Inter, system-ui, sans-serif;
    padding: 4px 8px; box-shadow: 0 4px 14px rgba(0, 0, 0, .5);
}
.spot-hover-tip::before { border-top-color: #fb8500 !important; }
.map-hint {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    z-index: 1000; pointer-events: none; white-space: nowrap;
    background: rgba(10, 14, 19, .88); color: #e8eef5;
    border: 1px solid rgba(251, 133, 0, .7); border-radius: 4px;
    padding: 7px 14px; font: 600 12px/1.2 Inter, system-ui, sans-serif;
    letter-spacing: .02em; box-shadow: 0 6px 18px rgba(0, 0, 0, .45);
}
</style>
"""


LANDING_MAP_HEIGHT = 440

# Hovering a saved spot drops a labelled, pulsing marker on the landing map
# and flies there when the spot is off-screen. Hover never reaches Python, so
# this runs in the page and reaches into the map's frame, which streamlit_folium
# serves from the same origin with the Leaflet map at window.map. SPOTS_JSON is
# filled in per run; the listeners are installed only once.
# The script must never contain "<" followed by a letter, "/" or "!": st.html
# sanitizes with DOMPurify, which silently deletes such a script whole. Hence
# the marker is built as an element rather than from an HTML string.
SPOT_HOVER_SCRIPT = """
<script>
(() => {
  window.__windSpots = SPOTS_JSON;
  if (window.__windSpotHover) return;
  window.__windSpotHover = true;

  let activeCard = null;
  let marker = null;
  const clear = () => { if (marker) { marker.remove(); marker = null; } };
  const cardOf = (el) => el && el.closest && el.closest('[class*="st-key-spot_card_"]');

  document.addEventListener("mouseover", (event) => {
    const card = cardOf(event.target);
    if (!card || card === activeCard) return;
    activeCard = card;
    const match = card.className.match(/st-key-spot_card_(\\d+)/);
    const spot = match && window.__windSpots[Number(match[1])];
    const frame = document.querySelector('iframe[title="streamlit_folium.st_folium"]');
    const view = frame && frame.contentWindow;
    if (!spot || !view || !view.map || !view.L) return;

    clear();
    const label = view.document.createElement("span");
    label.textContent = spot.name;
    const dot = view.document.createElement("div");
    dot.className = "spot-dot picked";
    marker = view.L.marker([spot.lat, spot.lon], {
      icon: view.L.divIcon({
        className: "", html: dot, iconSize: [13, 13], iconAnchor: [6, 6],
      }),
      interactive: false, zIndexOffset: 1000,
    })
      .bindTooltip(label, {
        permanent: true, direction: "top", offset: [0, -10], className: "spot-hover-tip",
      })
      .addTo(view.map);
    if (!view.map.getBounds().pad(-0.1).contains([spot.lat, spot.lon])) {
      view.map.flyTo([spot.lat, spot.lon], Math.max(view.map.getZoom(), 5), { duration: 0.8 });
    }
  });

  document.addEventListener("mouseout", (event) => {
    if (!activeCard || !activeCard.contains(event.target)) return;
    if (event.relatedTarget && activeCard.contains(event.relatedTarget)) return;
    activeCard = null;
    clear();
  });
})();
</script>
"""


def dot_marker(latitude, longitude, tooltip, picked=False, idle=False):
    """A designed dot rather than folium's default pin."""
    classes = "spot-dot picked" if picked else "spot-dot idle" if idle else "spot-dot"
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


def locator_map(latitude, longitude, key, height=230, cell=True):
    """The cell against the coastline, so you can see whether the water you
    care about is actually water. A station is a point, so it gets no cell.

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
    if cell:
        folium.Rectangle(
            bounds=[
                [latitude - GRID / 2, longitude - GRID / 2],
                [latitude + GRID / 2, longitude + GRID / 2],
            ],
            color="#fb8500", weight=2, fill=True, fill_opacity=0.25,
        ).add_to(local)
    dot_marker(latitude, longitude, "Your cell" if cell else "Station", picked=True).add_to(local)
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


# --- Stage 1: pick a spot on the map --------------------------------------

if "spot" not in st.session_state:
    st.session_state["spot"] = None
# Kept apart from the switch's own key, which Streamlit drops while the
# landing is off screen, so Back returns you to the source you were using.
st.session_state.setdefault("data_source", "era5")


def enter_spot(spot):
    st.session_state["spot"] = spot
    remember_location(spot)


if st.session_state["spot"] is None:
    saved = downloaded_locations()
    # The switch sits below the hero it rewrites, so read its value ahead.
    source = st.session_state.get("source_pick") or st.session_state["data_source"]
    stations, stations_error = None, None
    if source == "meteoswiss":
        try:
            stations = meteoswiss_stations()
        except Exception as error:
            stations_error = error

    if source == "meteoswiss":
        pitch = (
            "Click any MeteoSwiss weather station and pull every hour it has "
            "measured. Real readings from the shore rather than a 25 km model "
            "cell, so lakes and valleys get their own wind. "
        )
        facts = [
            (f"{stations['since'].min():.0f}" if stations is not None else "1981",
             "first year on record"),
            ("Measured", "not modelled"),
            (len(stations) if stations is not None else "150+", "stations with wind"),
        ]
    else:
        pitch = (
            "Click any stretch of water and pull every hour of wind since 1940 "
            "for that spot. "
        )
        facts = [
            ("1940", "first year on record"),
            ("Hourly", "every day since"),
            ("25 km", "per grid cell"),
        ]
    facts.append((len(saved), "spots downloaded"))

    st.html(
        '<div class="landing">'
        '<div class="hero-streaks"></div>'
        '<div class="landing-grid"><div>'
        '<div class="landing-eyebrow">Wind history for sailors</div>'
        '<div class="landing-title">Find where the <span class="wind">wind</span> '
        "really blows</div>"
        f'<div class="landing-sub">{pitch}See how often it is sailable for what you '
        "ride, in which months, at what time of day — and how it stacks up against "
        "your other spots.</div>"
        '<div class="landing-steps">'
        "<span><b>1</b>Pick a spot</span>"
        "<span><b>2</b>Choose your sport</span>"
        "<span><b>3</b>Rate, explore or compare</span>"
        "</div></div>"
        '<div class="landing-facts">'
        + "".join(f"<div><b>{value}</b><span>{label}</span></div>" for value, label in facts)
        + "</div></div></div>"
    )

    preset, found = None, None
    with st.container(horizontal=True, vertical_alignment="bottom", gap="large"):
        source = st.segmented_control(
            "Data source", list(SOURCES), format_func=SOURCES.get,
            default=st.session_state["data_source"], required=True, key="source_pick",
        )
        st.session_state["data_source"] = source
        if source == "era5":
            preset = pick(
                st.segmented_control(
                    "Jump to a known spot", list(PRESETS), default=None, key="preset",
                ),
                None,
            )
        elif stations is not None:
            found = st.selectbox(
                "Find a station", list(stations.index), index=None,
                format_func=lambda abbr: f"{stations.loc[abbr, 'name']} · {abbr}",
                placeholder=f"Search {len(stations)} stations", key="station_find",
                width=320,
            )
    if preset and st.session_state.get("preset_applied") != preset:
        st.session_state["preset_applied"] = preset
        st.session_state["clicked"] = PRESETS[preset]
        st.session_state["recentre"] = PRESETS[preset]
        st.rerun()
    if found and st.session_state.get("station_find_applied") != found:
        st.session_state["station_find_applied"] = found
        st.session_state["station_pick"] = found
        st.session_state["station_click_km"] = None
        st.session_state["station_recentre"] = True
        st.rerun()
    if stations_error is not None:
        st.error(f"Could not load the MeteoSwiss stations: {stations_error}")
        st.stop()

    clicked = st.session_state.get("clicked")
    picked = st.session_state.get("station_pick")
    if stations is None or picked not in stations.index:
        picked = None
    # Map and your spots side by side, so hovering a spot can show it on the map.
    if saved:
        map_col, list_col = st.columns([3, 2], gap="medium")
    else:
        map_col, list_col = st.container(), None

    with map_col:
        if source == "era5":
            centre = clicked or (30.0, 5.0)
            world = base_map(
                location=list(centre),
                zoom_start=7 if clicked else 3,
                control_scale=True,
                min_zoom=2,
                world_copy_jump=True,
            )
            hint = "Click again to move your pick" if clicked else "Click anywhere on the water"
            world.get_root().html.add_child(folium.Element(f'<div class="map-hint">{hint}</div>'))
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
                world, height=LANDING_MAP_HEIGHT, use_container_width=True, key="picker",
                center=list(recentre) if recentre else None,
                zoom=8 if recentre else None,
                returned_objects=["last_clicked"],
            )
        else:
            focus = tuple(stations.loc[picked, ["lat", "lon"]]) if picked else SWISS_CENTRE
            world = base_map(
                location=list(focus), zoom_start=10 if picked else 8,
                control_scale=True, min_zoom=6,
            )
            hint = (
                "Click another station to switch" if picked
                else "Click a station, or anywhere for the nearest one"
            )
            world.get_root().html.add_child(folium.Element(f'<div class="map-hint">{hint}</div>'))
            # Every station is on the map; the ones you have downloaded stand out.
            on_disk = {entry.get("station") for entry in saved.values()}
            for abbr, row in stations.iterrows():
                if abbr != picked:
                    dot_marker(row["lat"], row["lon"], f"{row['name']} · {abbr}",
                               idle=abbr not in on_disk).add_to(world)
            if picked:
                dot_marker(*focus, f"{stations.loc[picked, 'name']} · {picked}",
                           picked=True).add_to(world)

            recentre = st.session_state.pop("station_recentre", False)
            result = st_folium(
                world, height=LANDING_MAP_HEIGHT, use_container_width=True,
                key="station_picker",
                center=list(focus) if recentre else None,
                zoom=10 if recentre else None,
                returned_objects=["last_clicked", "last_object_clicked",
                                  "last_object_clicked_count"],
            )

    if source == "era5":
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
    else:
        # Same replay problem, with two kinds of click: a dot reports as an
        # object click (counted, so the same dot twice still registers), open
        # ground as a map click. Whichever moved on is the click just made.
        state = result or {}
        ground = state.get("last_clicked")
        dot = state.get("last_object_clicked")
        clicks = (
            (ground["lat"], ground["lng"]) if ground else None,
            state.get("last_object_clicked_count") or 0,
        )
        seen = st.session_state.get("station_clicks_seen", (None, 0))
        if clicks != seen:
            st.session_state["station_clicks_seen"] = clicks
            if clicks[1] != seen[1]:
                fresh = (dot["lat"], dot["lng"]) if dot else None
            else:
                fresh = clicks[0]
            if fresh is not None:
                abbr, km = nearest_station(stations, *fresh)
                st.session_state["station_pick"] = abbr
                st.session_state["station_click_km"] = km
                st.rerun()

    if list_col is not None:
        with list_col:
            st.html(
                '<div class="section-label">Your spots'
                f"<small>{len(saved)} downloaded · hover to find on the map</small></div>"
            )
            # Same height as the map, scrolling once the list outgrows it.
            with st.container(height=LANDING_MAP_HEIGHT - 58, gap="xsmall"):
                for index, (key, entry) in enumerate(saved.items()):
                    first, last = record_span(entry["path"], entry["path"].stat().st_mtime)
                    if entry.get("station"):
                        where = f"MeteoSwiss {entry['station']} · {entry['height']:.0f} m"
                    else:
                        zone_name = entry["zone"].split("/")[-1].replace("_", " ")
                        where = f"ERA5 · {coords_label(entry['lat'], entry['lon'])} · {zone_name}"
                    # Keyed by position: the hover script below matches cards to
                    # spots by this number.
                    with st.container(key=f"spot_card_{index}", horizontal=True,
                                      vertical_alignment="center"):
                        st.markdown(f"**{entry['name']}**  \n:gray[{where} · {first}–{last}]")
                        if st.button("Open", key=f"open_{key}",
                                     icon=":material/arrow_forward:", width="content"):
                            enter_spot({k: v for k, v in entry.items() if k != "path"})
                            st.rerun()
        spots = [
            {"lat": entry["lat"], "lon": entry["lon"], "name": entry["name"]}
            for entry in saved.values()
        ]
        # Every "<" escaped, so no spot name can trip the sanitizer (see above).
        st.html(
            SPOT_HOVER_SCRIPT.replace("SPOTS_JSON", json.dumps(spots).replace("<", "\\u003c")),
            unsafe_allow_javascript=True,
        )

    if source == "era5" and clicked:
        raw_lat, raw_lon = clicked
        latitude, longitude = snap_to_grid(raw_lat), snap_to_grid(raw_lon)
        zone = timezone_at(latitude, longitude)
        cached = dataset_for(latitude, longitude)

        with st.container(key="picked_panel"):
            facts_row = st.columns([2, 2, 2, 3], vertical_alignment="center")
            facts_row[0].metric(
                "Your pick", f"{latitude:+.2f}, {longitude:+.2f}",
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
                    enter_spot({"lat": latitude, "lon": longitude, "zone": zone, "name": name})
                    st.rerun()

    if picked:
        row = stations.loc[picked]
        zone = timezone_at(row["lat"], row["lon"])
        cached = station_dataset(picked)
        where = f"MeteoSwiss station {picked} · {coords_label(row['lat'], row['lon'])}"
        km = st.session_state.get("station_click_km")
        if km and km >= 1:
            where += f" · nearest to your click, {km:.0f} km away"

        with st.container(key="picked_panel"):
            facts_row = st.columns([2, 2, 2, 3], vertical_alignment="center")
            facts_row[0].metric("Your station", row["name"], icon=":material/sensors:",
                                help=where)
            facts_row[1].metric(
                "Height", f"{row['height']:.0f} m", icon=":material/landscape:",
                help="Above sea level. A station up a hill feels more wind than "
                "the water below it.",
            )
            facts_row[2].metric(
                "Data", "on disk" if cached.exists() else f"since {row['since']:.0f}",
                icon=":material/database:",
                help=None if row["has_temp"] else "Wind only: this station does not "
                "record air temperature.",
            )
            with facts_row[3]:
                known = load_registry().get(f"station_{picked}", {})
                name = st.text_input(
                    "Name this spot", value=known.get("name") or row["name"],
                    key=f"name_station_{picked}",
                )
                if st.button(":material/arrow_forward: Use this station",
                             type="primary", width="stretch"):
                    enter_spot({
                        "lat": float(row["lat"]), "lon": float(row["lon"]),
                        "zone": zone, "name": name,
                        "station": picked, "height": float(row["height"]),
                    })
                    st.rerun()
    st.stop()


# Open on a sport rather than "Any wind", which cannot be rated or compared.
st.session_state.setdefault("activity", "Dinghy")
activity = st.session_state["activity"]
spot = st.session_state["spot"]
LATITUDE, LONGITUDE = spot["lat"], spot["lon"]
LOCAL_TZ, LOCATION_NAME = spot["zone"], spot["name"]
# The MeteoSwiss station code, or None for an ERA5 cell.
STATION = spot.get("station")
SOURCE_CREDIT = f"MeteoSwiss station {STATION}" if STATION else "ERA5 reanalysis"
dataset_path = station_dataset(STATION) if STATION else dataset_for(LATITUDE, LONGITUDE)

# Navigation: step back one screen without losing the location.
# One back / forward pair, plus the reset to the very beginning.
# Back steps out of a view to the options, or off the options back to the map.
# Forward returns to whichever view you last left.
current_mode = st.session_state.get("mode")
# What the map screen remembers about your last pick, cleared on the way back.
LANDING_PICKS = ("clicked", "preset_applied", "station_pick", "station_find_applied",
                 "station_click_km", "station_clicks_seen")
nav = st.container(horizontal=True)

if nav.button(":material/arrow_back: Back", type="secondary"):
    if current_mode is not None:
        st.session_state["last_mode"] = current_mode
        st.session_state["mode"] = None
    else:
        for key in ("spot", *LANDING_PICKS):
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
    for key in ("spot", "mode", "last_mode", *LANDING_PICKS):
        st.session_state.pop(key, None)
    st.rerun()


# --- Stage 2: make sure this location has data ----------------------------

if not dataset_path.exists() and STATION:
    st.subheader(f"Download the record for {LOCATION_NAME}")
    st.caption(
        f"MeteoSwiss station {STATION} · {spot['height']:.0f} m · {LOCAL_TZ}. "
        "Every hour the station has measured, from MeteoSwiss open data; "
        "no account needed. It can take a minute or two."
    )
    if st.button("Fetch from MeteoSwiss", type="primary"):
        bar = st.progress(0.0, text="Asking MeteoSwiss for the station's files...")

        def report(done, total):
            bar.progress(
                done / total,
                text=f"Downloaded {done} of {total} files" if done < total
                else "All files in, joining them into one record...",
            )

        try:
            fetch_station(spot, dataset_path, progress=report)
            st.success("Downloaded.")
            st.rerun()
        except Exception as error:
            bar.empty()
            st.error(f"Could not fetch that station: {error}")
    st.stop()

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


def what_to_expect(rating, activity, session_text, missing_temp):
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
        lines.append(f"_{missing_temp}_")
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
    # A big day reaches the third speed band. The top of the range is the
    # wrong mark: reaching it means too much wind, and ERA5's smoothed hourly
    # winds almost never get there.
    strong = ACTIVITIES[activity]["ladder"][-2]
    by_day = knots.resample("1D")
    daily = by_day.mean().dropna()
    spread = (by_day.std() / by_day.mean()).dropna()

    in_groove = daily.between(low, high)
    groove = in_groove.mean() * 100
    too_light = (daily < low).mean() * 100
    big = (by_day.max().dropna() >= strong).mean() * 100
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
        "Big days": (min(big / 3, 10), f"{big:.0f}% of days touch {strong} kt"),
    }
    overall = sum(score * RATING_WEIGHTS[name] for name, (score, _) in parts.items())

    start_hour, end_hour, _ = best_window(knots)

    warmth = None
    if "temp_c" in usable.columns and usable["temp_c"].notna().any():
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


def lasting_days(flags, length):
    """Per day: do the flagged hours run unbroken for at least `length` hours?"""
    stamps = flags.index.to_series()
    # The local calendar day, taken from wall-clock time: normalising the
    # zoned stamps fails where a clock change fell at midnight (Portugal 1976).
    day = flags.index.tz_localize(None).normalize()
    # A run breaks on a miss, a gap in the readings or a new day.
    fresh = (
        stamps.diff().ne(pd.Timedelta(hours=1))
        | day.to_series(index=flags.index).diff().ne(pd.Timedelta(0))
    )
    run = (fresh | ~flags).cumsum()
    streak = flags.groupby(run).transform("sum")
    return streak.where(flags, 0).groupby(day).max().ge(length)


@st.cache_data(show_spinner=False, max_entries=64)
def condition_odds(path, mtime, zone, first_year, wind, air_min, lasting, hours):
    """How often the conditions you want turn up: by day, month, year and
    hour. A day counts when your hours on the water hold them for `lasting`
    hours in a row. `air_min` None leaves the temperature out."""
    frame = load_wind(path, mtime, zone)
    frame = frame[frame.index.year >= first_year]
    windy = (frame["speed"] * MS_TO_KNOTS).between(*wind)
    good = windy if air_min is None else windy & (frame["temp_c"] >= air_min)
    on_water = hour_mask(frame.index, hours)

    days = pd.DataFrame({
        "good": lasting_days(good[on_water], lasting),
        "windy": lasting_days(windy[on_water], lasting),
    })
    when_on = frame[good & on_water]
    return {
        "days": days,
        "month": days.groupby(days.index.month).mean().reindex(range(1, 13)) * 100,
        "year": days["good"].groupby(
            [days.index.year.rename("year"), days.index.month.rename("month")]
        ).mean() * 100,
        # Every hour of the day, not only yours, so you can see what you miss.
        "hour": (
            good.groupby([frame.index.month, frame.index.hour]).mean()
            .unstack().reindex(index=range(1, 13), columns=range(24)) * 100
        ),
        "best_window": best_window(good.astype(float)),
        "direction": wind_direction(when_on["u10"], when_on["v10"]).to_numpy(),
        "knots": (when_on["speed"] * MS_TO_KNOTS).to_numpy(),
        "on_month": when_on.index.month.to_numpy(),
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
        + (
            f"<span>MeteoSwiss station <b>{STATION}</b> · {spot['height']:.0f} m</span>"
            if STATION else "<span>source <b>ERA5</b></span>"
        )
        + "</div></div>"
    )
    locator_map(LATITUDE, LONGITUDE, key="start_map", height=300, cell=not STATION)

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
            "Your chances of the wind and warmth you want, by month, by hour "
            "of the day and year by year.",
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
# inside a view, and then it carries everything that changes what you see,
# in the order you would decide it: what you sail, what you want, when.
# Shared by every view, so the rating cannot quietly score different hours
# than the ones you picked. sticky() keeps a choice while another view hides
# its widget.

EXPLORING = st.session_state["mode"] == "explore"
HAS_TEMP = "temp_c" in df.columns and bool(df["temp_c"].notna().any())

st.sidebar.header("Settings")
if EXPLORING:
    st.sidebar.subheader("Your conditions")

activity = st.sidebar.selectbox(
    "What you sail", list(ACTIVITIES),
    index=list(ACTIVITIES).index(st.session_state["activity"]),
    key="activity_side",
)
st.session_state["activity"] = activity
groove_low, groove_high = ACTIVITIES[activity]["groove"]

if EXPLORING:
    # One range per sport, so switching sport brings back its own wind.
    wind_low, wind_high = st.sidebar.slider(
        "Wind", 0, WIND_TOP, format="%d kn",
        key=sticky(f"wind_{activity}", ACTIVITIES[activity]["groove"]),
    )
    air_pick = st.sidebar.select_slider(
        "Air at least", ["Any", *range(10, 36)],
        format_func=lambda value: "any" if value == "Any" else f"{value} °C",
        key=sticky("air_min", "Any"), disabled=not HAS_TEMP,
    )
    if not HAS_TEMP:
        st.sidebar.caption(
            "This station does not record air temperature." if STATION else
            "No temperature in this download; refetch the spot to include it."
        )
    air_min = air_pick if HAS_TEMP and air_pick != "Any" else None
    lasting = st.sidebar.segmented_control(
        "Lasting at least", [1, 2, 3], format_func=lambda h: f"{h} h",
        required=True, key=sticky("lasting", 2),
    )
else:
    st.sidebar.caption(f"Sailable wind {groove_low}–{groove_high} kt")

if EXPLORING:
    st.sidebar.subheader("When")
hours = st.sidebar.slider(
    "On the water", 0, 23, format="%d:00", key=sticky("hours", (10, 17)),
)
SESSION_WINDOWS = (hours,)
SESSION_TEXT = "all day" if hours == (0, 23) else f"{hours[0]:02d}:00–{hours[1]:02d}:59"
st.sidebar.caption("Every hour of the day" if hours == (0, 23) else f"{SESSION_TEXT} local time")

if EXPLORING:
    span = st.sidebar.segmented_control(
        "Years", list(LOOKBACK), required=True, key=sticky("years", "20 years"),
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
            "Only one location is downloaded so far. Press **Start over**, pick a "
            "second spot on the map and fetch it, then come back here."
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
    if bool(STATION) != bool(rival.get("station")):
        st.caption(
            ":material/info: One of these is a MeteoSwiss station and the other an "
            "ERA5 cell. A station measures the wind where it stands; ERA5 averages "
            "a 25 km cell, which smooths out local effects. Read the gap with that "
            "in mind."
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
        f"Scored on {rating['years']}, counting {SESSION_TEXT} local. "
        f"Data: {SOURCE_CREDIT}."
    )

    expect_col, parts_col = st.columns([2, 3], gap="medium")

    with expect_col:
        with st.container(border=True):
            st.markdown("##### :material/lightbulb: What to expect")
            missing_temp = (
                "This station does not record air temperature." if STATION else
                "No temperature in this download — refetch the location to include it."
            )
            for line in what_to_expect(rating, activity, SESSION_TEXT, missing_temp):
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
        help=f"{rating['best_month_pct']:.0f}% of days sit in {groove_low}–{groove_high} kt",
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
        locator_map(LATITUDE, LONGITUDE, key="rating_map", height=340, cell=not STATION)
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


# --- Explore: your chances of the conditions you want ---------------------

years_back = LOOKBACK[span]
first_year = (
    data_start.year if years_back is None
    else max(data_start.year, data_end.year - years_back + 1)
)
with st.spinner("Counting your days..."):
    odds = condition_odds(
        dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ, first_year,
        (wind_low, wind_high), air_min, lasting, hours,
    )
by_month, days = odds["month"], odds["days"]

wind_text = (
    f"{wind_low}+ kn" if wind_high >= WIND_TOP else
    f"up to {wind_high} kn" if wind_low == 0 else
    f"{wind_low}–{wind_high} kn"
)
wanted = wind_text + ("" if air_min is None else f" and {air_min} °C+")
within = (
    "at any hour" if hours == (0, 23)
    else f"between {hours[0]:02d}:00 and {hours[1]:02d}:59"
)

st.html(
    '<div class="reveal" style="font-size:1.5rem;font-weight:650">'
    + reveal(f"How often you get {wanted}")
    + '</div><hr class="accent-rule">'
)
st.caption(
    f"A day counts when that lasts {lasting} h in a row {within} local time · "
    f"{first_year}–{data_end.year} · data: {SOURCE_CREDIT}"
)

overall = float(days["good"].mean() * 100) if len(days) else 0.0
if overall == 0:
    st.info(
        f"Not a single day like that between {first_year} and {data_end.year}. "
        "Try less wind, cooler air, a shorter stretch or more hours on the water."
    )
    st.stop()

month_names = [calendar.month_abbr[m] for m in range(1, 13)]
month_days = [calendar.monthrange(2001, m)[1] for m in range(1, 13)]
best_month = int(by_month["good"].idxmax())
best_pct = float(by_month.loc[best_month, "good"])
best_days = round(best_pct / 100 * month_days[best_month - 1])
best_days_text = (
    "under a day" if best_days < 1 else f"about {best_days} day{'s' if best_days > 1 else ''}"
)
start_hour, end_hour, _ = odds["best_window"]

kpis = st.columns(4)
kpis[0].metric(
    "Any day of the year", percent(overall), border=True,
    icon=":material/event_available:",
    help=f"Share of all days {first_year}–{data_end.year} that give you this",
)
kpis[1].metric(
    "Best month", calendar.month_name[best_month], border=True,
    icon=":material/trending_up:", delta_arrow="off", delta_color="off",
    delta=f"{percent(best_pct)} · {best_days_text}",
)
kpis[2].metric(
    "Best time of day", f"{start_hour:02d}:00–{end_hour:02d}:59", border=True,
    icon=":material/schedule:", help="The three hours that most often have it, all year",
)
kpis[3].metric(
    "Wind comes from", prevailing_direction(odds["direction"]), border=True,
    icon=":material/explore:", help="When it is on, during your hours",
)

# Month by month. With a temperature set, the blue cap shows the days that
# bring the wind but not the warmth.
GOOD_LABEL, COLD_LABEL = "Wind and warm air", "Wind, but colder"
month_data = pd.DataFrame({
    "month": range(1, 13),
    "name": month_names,
    "good": by_month["good"].fillna(0).to_numpy(),
    "windy": by_month["windy"].fillna(0).to_numpy(),
})
month_data["days"] = month_data["good"] / 100 * month_days
month_tooltip = [
    alt.Tooltip("name:N", title="month"),
    alt.Tooltip("good:Q", title="% of days with it", format=".0f"),
    alt.Tooltip("days:Q", title="days in a typical month", format=".1f"),
]
if air_min is not None:
    month_tooltip.insert(2, alt.Tooltip("windy:Q", title="% with the wind alone", format=".0f"))

# Defaults to the best month; a click anywhere in a column picks another.
pick_month = alt.selection_point(
    name="month_click", fields=["month"], on="click", value=[{"month": best_month}],
)
x_month = alt.X("name:N", sort=month_names, title=None, axis=alt.Axis(labelAngle=0))
bar_style = dict(size=22, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
# The picked month gets a soft band behind it rather than dimming the rest,
# so every bar keeps its true colour.
layers = [
    alt.Chart(month_data)
    .mark_bar(size=44, color=WATER_SHADE)
    .encode(x=x_month, y=alt.value(0), y2=alt.value(alt.expr("height")),
            opacity=alt.condition(pick_month, alt.value(0.14), alt.value(0.0)))
]
if air_min is not None:
    series = alt.Scale(domain=[GOOD_LABEL, COLD_LABEL], range=[GOOD_COLOR, COLD_COLOR])
    layers.append(
        alt.Chart(month_data.assign(series=COLD_LABEL))
        .mark_bar(**bar_style)
        .encode(x=x_month, y=alt.Y("windy:Q", title="% of days"),
                color=alt.Color("series:N", scale=series, title=None,
                                legend=alt.Legend(orient="top")))
    )
    good_color = alt.Color("series:N", scale=series, title=None)
else:
    good_color = alt.value(GOOD_COLOR)
layers.append(
    alt.Chart(month_data.assign(series=GOOD_LABEL))
    # The surface-coloured ring keeps the orange apart from the blue behind it.
    .mark_bar(**bar_style, stroke=SURFACE, strokeWidth=2)
    .encode(x=x_month, y=alt.Y("good:Q", title="% of days"), color=good_color)
)
# Invisible full-height columns on top: the click and hover target, so a
# month is easy to hit even where its bar is short.
layers.append(
    alt.Chart(month_data)
    .mark_bar(size=44, opacity=0)
    .encode(x=x_month, y=alt.value(0), y2=alt.value(alt.expr("height")),
            tooltip=month_tooltip)
    .add_params(pick_month)
)

chart_col, hour_col = st.columns([3, 2], gap="medium")
with chart_col:
    st.markdown("**Month by month**")
    event = st.altair_chart(
        alt.layer(*layers).properties(height=300), width="stretch",
        on_select="rerun", key="month_pick",
    )
    clicked = (event.selection or {}).get("month_click") or []
    selected = clicked[0]["month"] if clicked else best_month
    st.caption("Click a month to see its day and its wind →")

with hour_col:
    st.markdown(f"**{calendar.month_name[selected]} through the day**")
    hour_data = pd.DataFrame({
        "mid": np.arange(24) + 0.5,
        "label": [f"{h:02d}:00–{h:02d}:59" for h in range(24)],
        "pct": odds["hour"].loc[selected].fillna(0).to_numpy(),
    })
    x_hour = alt.X(
        "mid:Q", scale=alt.Scale(domain=[0, 24]), title=None,
        axis=alt.Axis(values=list(range(0, 25, 3)), labelExpr="datum.value + 'h'"),
    )
    hover = alt.selection_point(
        on="pointerover", nearest=True, fields=["mid"], empty=False, clear="pointerout",
    )
    line = alt.Chart(hour_data).encode(x=x_hour, y=alt.Y("pct:Q", title="% of hours"))
    st.altair_chart(
        alt.layer(
            alt.Chart(pd.DataFrame({"start": [hours[0]], "end": [hours[1] + 1]}))
            .mark_rect(color=WATER_SHADE, opacity=0.14)
            .encode(x="start:Q", x2="end:Q"),
            line.mark_area(color=GOOD_COLOR, opacity=0.1, interpolate="monotone"),
            line.mark_line(color=GOOD_COLOR, strokeWidth=2, interpolate="monotone"),
            alt.Chart(hour_data).mark_rule(color=WATER_SHADE).encode(x=x_hour)
            .transform_filter(hover),
            line.mark_point(filled=True, size=80, color=GOOD_COLOR,
                            stroke=SURFACE, strokeWidth=2)
            .encode(
                opacity=alt.condition(hover, alt.value(1.0), alt.value(0.0)),
                tooltip=[alt.Tooltip("label:N", title="hour"),
                         alt.Tooltip("pct:Q", title="% of hours with it", format=".0f")],
            )
            .add_params(hover),
        ).properties(height=300),
        width="stretch",
    )
    st.caption("Share of each hour that has it. Shaded: your hours on the water.")

st.divider()
year_col, rose_col = st.columns([3, 2], gap="medium")

with year_col:
    st.markdown("**Year by year**")
    year_data = odds["year"].rename("pct").reset_index()
    year_data["name"] = [calendar.month_abbr[m] for m in year_data["month"]]
    years_shown = year_data["year"].nunique()
    st.altair_chart(
        alt.Chart(year_data)
        .mark_rect(stroke=SURFACE, strokeWidth=2 if years_shown <= 30 else 1)
        .encode(
            x=alt.X("name:N", sort=month_names, title=None,
                    axis=alt.Axis(labelAngle=0, orient="top")),
            y=alt.Y("year:O", sort="descending", title=None,
                    axis=alt.Axis(labelOverlap="greedy")),
            color=alt.Color(
                "pct:Q", title="% of days",
                scale=alt.Scale(range=CHANCE_RAMP, domain=[0, max(1.0, year_data["pct"].max())]),
                legend=alt.Legend(orient="bottom", gradientLength=220),
            ),
            tooltip=[alt.Tooltip("year:O"), alt.Tooltip("name:N", title="month"),
                     alt.Tooltip("pct:Q", title="% of days with it", format=".0f")],
        )
        .properties(height=min(max(16 * years_shown, 180), 520)),
        width="stretch",
    )
    st.caption("One square per month of each year: the brighter, the more days had it.")

with rose_col:
    in_month = odds["on_month"] == selected
    if in_month.any():
        st.markdown(
            f"**{calendar.month_name[selected]}** · when it is on, the wind comes "
            f"from **{prevailing_direction(odds['direction'][in_month])}**"
        )
        st.pyplot(
            draw_rose(odds["direction"][in_month], odds["knots"][in_month],
                      ACTIVITIES[activity]["ladder"], "knots"),
            width="stretch",
        )
    else:
        st.info(f"No hours like that in {calendar.month_name[selected]}.")
    # The rose says which way; the map says if that way is water.
    locator_map(LATITUDE, LONGITUDE, key="explore_map", height=210, cell=not STATION)


# --- The numbers behind the charts (sidebar) ------------------------------
# Kept out of the main page so the charts end cleanly.

with st.sidebar.expander("The numbers"):
    table = pd.DataFrame({
        "Month": month_names,
        "With it %": month_data["good"].round(0),
        "Days": month_data["days"].round(1),
    })
    if air_min is not None:
        table.insert(2, "Wind alone %", month_data["windy"].round(0))
    st.dataframe(table, hide_index=True, width="stretch")
    per_day = days.rename(columns={"good": "with_it", "windy": "wind_alone"})
    per_day.index = per_day.index.date
    st.download_button(
        ":material/download: Every day as CSV",
        per_day.rename_axis("date").to_csv().encode(),
        file_name=(
            f"days-{STATION}.csv" if STATION
            else f"days-{LATITUDE:+.2f}_{LONGITUDE:+.2f}.csv"
        ),
        mime="text/csv",
    )
