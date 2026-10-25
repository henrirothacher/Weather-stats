import base64
import calendar
import html
import io
import json
import re
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
APP_VERSION = "2.3"

def svg_image(markup, view_box, css_class=""):
    """A drawing for st.html. Its sanitiser drops <svg> outright but lets an
    image through, so the drawing goes in as one. CSS cannot reach inside an
    image, so the markup carries its own colours."""
    drawing = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">{markup}</svg>'
    data = base64.b64encode(drawing.encode()).decode()
    return f'<img class="{css_class}" src="data:image/svg+xml;base64,{data}" alt="">'


# The mark: three gusts, cool to warm like the wind bands on the charts.
# Drawn rather than an emoji, which some systems render as a stray glyph.
# The gradient is in page units: a straight stroke has no height to stretch
# a box-relative one over, and would vanish.
LOGO = svg_image(
    '<defs><linearGradient id="tint" gradientUnits="userSpaceOnUse" '
    'x1="3" y1="4" x2="29" y2="26">'
    '<stop offset="0" stop-color="#8ecae6"/><stop offset=".55" stop-color="#ffb703"/>'
    '<stop offset="1" stop-color="#fb8500"/></linearGradient></defs>'
    '<g fill="none" stroke="url(#tint)" stroke-width="2.6" stroke-linecap="round">'
    '<path d="M3 11h15.5a3.5 3.5 0 1 0-3.5-3.5"/>'
    '<path d="M3 17h22a3.5 3.5 0 1 1-3.5 3.5"/>'
    '<path d="M3 23h10"/></g>',
    "0 0 32 32", "mark-icon",
)

st.html(
    """
    <style>
    /* One bar across the top of every screen: the mark, and inside a spot
       the way back. Streamlit's deploy button has no place in a finished app. */
    [data-testid="stAppDeployButton"] { display: none; }
    [data-testid="stMainBlockContainer"] { padding-top: 3.8rem; }
    /* Wide screens get used: narrower margins than Streamlit's own. */
    @media (min-width: 1000px) {
        [data-testid="stMainBlockContainer"] { padding-left: 3rem; padding-right: 3rem; }
    }
    .st-key-topbar {
        padding-bottom: .75rem; margin-bottom: .35rem;
        border-bottom: 1px solid rgba(38, 51, 63, .85);
    }
    .app-masthead { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
    .app-masthead .mark-icon { width: 30px; height: 30px; flex: none; }
    .app-masthead .mark {
        font-size: 1.3rem; font-weight: 800; letter-spacing: -.025em; color: #e8eef5;
    }
    .app-masthead .tag {
        font-size: .8rem; color: #8b98a5; padding-left: .7rem; margin-left: .1rem;
        border-left: 1px solid #26333f;
    }
    @media (max-width: 640px) { .app-masthead .tag { display: none; } }
    .st-key-nav button {
        border-radius: 999px; min-height: 2.15rem; padding: .2rem .95rem; font-size: .86rem;
    }
    /* Pinned to the window, so it stays put on every screen and scroll. */
    .app-version {
        position: fixed; right: .9rem; bottom: .7rem; z-index: 999;
        font-size: .72rem; font-weight: 600; letter-spacing: .04em;
        color: #8b98a5; pointer-events: none;
    }
    </style>
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

    /* Figures as cards: a quiet label over a large, even-width value. */
    [data-testid="stMetric"] {
        background: linear-gradient(180deg, #111922, #0d131a);
    }
    [data-testid="stMetricLabel"] p { font-size: .84rem; font-weight: 600; color: #a9b6c3; }
    [data-testid="stMetricValue"] {
        font-size: clamp(1.4rem, 2.1vw, 2rem); font-weight: 700;
        letter-spacing: -.02em; font-variant-numeric: tabular-nums;
    }

    /* The one button that moves you on. */
    [data-testid="stBaseButton-primary"] {
        background: linear-gradient(90deg, #fb8500, #ffa31a); border: 0;
        color: #0b0f14; font-weight: 700;
        box-shadow: 0 6px 18px rgba(251, 133, 0, .22);
    }
    [data-testid="stBaseButton-primary"]:hover {
        background: linear-gradient(90deg, #ff9a1f, #ffb703); color: #0b0f14;
    }
    [data-testid="stBaseButton-primary"] p { font-weight: 700; }

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
        display: flex; gap: .45rem; flex-wrap: wrap;
        font-size: .82rem; margin-top: .7rem; color: #a9b6c3;
    }
    .stat-strip span {
        border: 1px solid #26333f; border-radius: 999px; background: #0f161e;
        padding: .28rem .75rem;
    }
    .stat-strip b { font-weight: 600; color: #8ecae6; }

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

    /* Numbered steps on the spot screen, the same badges as the landing's. */
    .step-label {
        display: flex; align-items: center; gap: .6rem;
        font-size: 1.02rem; font-weight: 700; color: #e8eef5; margin: .1rem 0 .15rem;
    }
    .step-label b {
        display: inline-grid; place-items: center; width: 1.6rem; height: 1.6rem; flex: none;
        border: 1px solid #26333f; border-radius: 4px; background: #141c25;
        color: #ffb703; font-size: .78rem;
    }
    .st-key-activity_pick button { min-height: 2.6rem; padding: 0 1.05rem; }
    /* All four sports on one line on a phone, wrapping only if that fails. */
    .st-key-activity_pick [data-testid="stButtonGroup"] > div:last-child { flex-wrap: wrap; }
    @media (max-width: 640px) { .st-key-activity_pick button { padding: 0 .6rem; } }
    /* Step 1 is done by the time you are here: your spot. */
    .spot-eyebrow { display: flex; align-items: center; gap: .55rem; }
    .spot-eyebrow b {
        display: inline-grid; place-items: center; width: 1.35rem; height: 1.35rem;
        border: 1px solid rgba(251, 133, 0, .55); border-radius: 4px;
        font-size: .68rem; letter-spacing: 0;
    }
    /* Arriving draws the rule in (see START_BUILD_CSS); nothing else moves it. */
    .spot-hero .accent-rule { animation: none; }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    @keyframes bandGrow { from { transform: scaleX(0); } to { transform: none; } }

    /* The sport's range on a knot ruler. It grows into place each time. */
    .groove { margin: .35rem 0 0; padding-top: 1.3rem; }
    .groove-track, .groove-scale { max-width: 440px; }
    .groove-track { position: relative; height: 6px; border-radius: 3px; background: #17212b; }
    .groove-track::before {
        content: ""; position: absolute; inset: -3px 0; border-right: 1px solid #2b3a48;
        background: repeating-linear-gradient(90deg, #2b3a48 0 1px, transparent 1px 12.5%);
    }
    .groove-band {
        position: absolute; top: -1px; bottom: -1px; border-radius: 4px;
        background: linear-gradient(90deg, #ffb703, #fb8500);
        box-shadow: 0 0 0 3px rgba(251, 133, 0, .14);
        transform-origin: left center;
        animation: bandGrow .65s cubic-bezier(.2, .7, .3, 1) both;
    }
    .groove em {
        position: absolute; bottom: calc(100% + 7px); transform: translateX(-50%);
        font-style: normal; font-size: .76rem; font-weight: 700; color: #e8eef5;
        white-space: nowrap; animation: fadeIn .3s ease .45s both;
    }
    .groove-scale { position: relative; height: .9rem; margin-top: .45rem; }
    .groove-scale span {
        position: absolute; transform: translateX(-50%);
        font-size: .66rem; color: #5b6b7a; font-variant-numeric: tabular-nums;
    }
    .groove-note { font-size: .86rem; color: #8b98a5; margin-top: .55rem; }

    /* The two ways in. Each is drawn as a small instrument that idles cool
       and warms under the pointer, cool to warm like the wind bands. The
       whole card is the button: its label sits along the bottom edge.
       Registered as colours so they fade rather than jump; "\\3c" is "<",
       spelt out because the sanitiser drops a block holding "<" and a letter. */
    @property --tone { syntax: "\\3c color>"; inherits: true; initial-value: #3f6b84; }
    @property --glow { syntax: "\\3c color>"; inherits: true; initial-value: #8ecae6; }
    [data-testid="stLayoutWrapper"]:has(> [class*="st-key-choice_"]) { flex: 1 1 auto; }
    [class*="st-key-choice_"] {
        position: relative; overflow: hidden;
        padding: 0 0 5.4rem !important; background: #0d1319;
        border: 1px solid #26333f !important; border-radius: 8px !important;
        transition: border-color .3s ease, --tone .35s ease, --glow .35s ease;
    }
    [class*="st-key-choice_"]:hover:not(:has(button:disabled)) {
        --tone: #fb8500; --glow: #ffb703; border-color: rgba(251, 133, 0, .6) !important;
    }
    [class*="st-key-choice_"]:has(button:focus-visible) {
        border-color: #fb8500 !important; box-shadow: 0 0 0 1px #fb8500;
    }
    /* The cards fill the screen: each drawing scaled up as a whole. */
    .choice-face {
        height: 200px; display: flex; align-items: center; justify-content: center;
        gap: 1.5rem; background: #0a0f14; border-bottom: 1px solid #1a2430;
    }
    .choice-face > * { zoom: 1.35; }
    .choice-text { padding: 1.4rem 1.6rem 0; }
    .choice-kicker {
        font-size: .74rem; font-weight: 700; letter-spacing: .16em;
        text-transform: uppercase; color: #8b98a5;
    }
    .choice-title {
        font-size: 1.8rem; font-weight: 800; letter-spacing: -.025em;
        color: #e8eef5; margin-top: .35rem;
    }
    .choice-blurb { font-size: 1.04rem; line-height: 1.55; color: #a9b6c3; margin-top: .4rem; }
    .choice-lock { font-size: .82rem; font-weight: 600; color: #ffb703; margin-top: .55rem; }
    [class*="st-key-choice_"] [class*="st-key-pick_"] {
        position: absolute; inset: 0; z-index: 2; width: auto !important;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] .stButton,
    [class*="st-key-choice_"] [class*="st-key-pick_"] button { width: 100%; height: 100%; }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button {
        display: flex; align-items: flex-end; padding: 0 1.6rem 1.3rem;
        background: transparent !important; border: 0 !important; border-radius: 0;
        box-shadow: none !important;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button > div,
    [class*="st-key-choice_"] [class*="st-key-pick_"] button > div > span,
    [class*="st-key-choice_"] [class*="st-key-pick_"] [data-testid="stMarkdownContainer"] {
        width: 100%;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button p {
        display: flex; justify-content: space-between; align-items: center;
        padding-top: 1rem; border-top: 1px solid #1f2b37;
        font-size: 1.02rem; font-weight: 700; color: #e8eef5; transition: color .25s ease;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button p::after {
        content: "→"; font-size: 1.05rem; color: #5b6b7a;
        transition: transform .25s ease, color .25s ease;
    }
    [class*="st-key-choice_"]:hover [class*="st-key-pick_"] button:not(:disabled) p { color: #fb8500; }
    [class*="st-key-choice_"]:hover [class*="st-key-pick_"] button:not(:disabled) p::after {
        color: #fb8500; transform: translateX(5px);
    }
    [class*="st-key-choice_"] button:disabled { cursor: not-allowed; }
    [class*="st-key-choice_"] button:disabled p { color: #5b6b7a !important; }
    [class*="st-key-choice_"]:has(button:disabled) .choice-face { opacity: .35; }
    [class*="st-key-choice_"]:has(button:disabled) :is(.dial-needle > span, .grid i) {
        animation-play-state: paused;
    }

    /* Rate: a meter from 0 to 10 whose needle hunts and never settles. */
    /* Ticks every 22 degrees round a 220 degree arc, 0 bottom left. */
    .dial { position: relative; width: 150px; height: 108px; flex: none; }
    .dial i {
        position: absolute; left: calc(50% - 1px); bottom: 30px;
        width: 2px; height: 8px; border-radius: 1px; transform-origin: 50% 100%;
        transform: rotate(calc(var(--n) * 22deg - 110deg)) translateY(-62px);
        background: color-mix(in srgb, var(--tone) 60%, #1a2430);
    }
    .dial i:nth-of-type(5n+1) { height: 13px; background: var(--glow); }
    .dial-needle { position: absolute; left: calc(50% - 1.5px); bottom: 30px; width: 3px; height: 54px; }
    .dial-needle > span {
        display: block; height: 100%; border-radius: 3px; transform-origin: 50% 100%;
        transform: rotate(-110deg);
        background: linear-gradient(to top, var(--tone), var(--glow));
        animation: needleHunt 6.5s ease-in-out infinite;
    }
    .dial::after {
        content: ""; position: absolute; left: calc(50% - 6px); bottom: 24px;
        width: 12px; height: 12px; border-radius: 50%; box-sizing: border-box;
        background: #0a0f14; border: 2px solid var(--glow);
    }
    @keyframes needleHunt {
        0%, 100% { transform: rotate(-110deg); }
        26%      { transform: rotate(40deg); }
        38%      { transform: rotate(14deg); }
        60%      { transform: rotate(88deg); }
        78%      { transform: rotate(-30deg); }
    }
    .dial-read { display: flex; flex-direction: column; line-height: 1; }
    .dial-read b { font-size: 2.7rem; font-weight: 800; letter-spacing: -.04em; color: var(--glow); }
    .dial-read small {
        margin-top: .35rem; font-size: .66rem; font-weight: 600; letter-spacing: .14em;
        text-transform: uppercase; color: #8b98a5;
    }

    /* Explore: a month-by-hour grid with a wash passing over it. */
    .grid { display: flex; gap: 3px; }
    .grid > span { display: flex; flex-direction: column; align-items: center; gap: 3px; }
    .grid i {
        width: 10px; height: 10px; border-radius: 2px; background: #17212b;
        animation: cellWash 4s ease-in-out infinite;
        animation-delay: calc(var(--c) * .12s + var(--r) * .08s - 4s);
    }
    .grid small {
        font-size: .62rem; font-weight: 600; line-height: 1; color: #5b6b7a;
    }
    .grid small { margin-top: 3px; }
    @keyframes cellWash {
        0%, 46%, 100% { background: #17212b; }
        18%           { background: var(--tone); }
        28%           { background: color-mix(in srgb, var(--tone) 55%, #17212b); }
    }

    @media (prefers-reduced-motion: reduce) {
        .dial-needle > span, .grid i { animation: none; }
    }
    </style>
    """
)

# One bar across the top of every screen. Inside a spot the way back joins it.
topbar = st.container(horizontal=True, vertical_alignment="center", key="topbar")
topbar.html(
    f'<div class="app-masthead">{LOGO}<span class="mark">Wind Atlas</span>'
    '<span class="tag">Eighty years of hourly wind, anywhere you sail</span></div>'
    f'<div class="app-version">v{APP_VERSION}</div>'
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
# Where the settings start, before you change anything.
DEFAULT_HOURS = (10, 17)
DEFAULT_LASTING = 2
DEFAULT_YEARS = "20 years"
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
# Each kind of station: what to call it, and the prefix of its file in data/.
STATION_NAMES = {"meteoswiss": "MeteoSwiss"}
STATION_FILES = {"meteoswiss": "station"}
# MeteoSwiss open data: free, no account, semicolon CSV per station.
OGD_ROOT = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
OGD_ITEMS = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-smn/items"
# Where a station map opens before you pick one, and how many of the
# stations around what it looks at it draws.
SWISS_CENTRE = (46.8, 8.2)
STATION_ZOOM = {"meteoswiss": 8}
NEARBY_STATIONS = 200


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


def spot_key(spot):
    """Registry key: the grid cell for ERA5, the file name for a station."""
    if spot.get("station"):
        return f"{STATION_FILES[spot['source']]}_{spot['station']}"
    return f"{spot['lat']:+.2f}_{spot['lon']:+.2f}"


def spot_path(spot):
    """One cached netCDF per grid cell or station."""
    key = spot_key(spot)
    return data_dir / (f"{key}.nc" if spot.get("station") else f"wind_{key}.nc")


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


PLACES = data_dir / "places.csv"


@st.cache_resource(show_spinner=False)
def town_table():
    """GeoNames towns of 15,000 people or more (CC BY 4.0): the table, then
    each town's position in radians and its size as a power of ten. Read
    without NA handling, or Namibia and a town called Nan would go missing."""
    towns = pd.read_csv(PLACES, keep_default_na=False)
    return (towns, np.radians(towns["lat"].to_numpy()), np.radians(towns["lon"].to_numpy()),
            np.log10(towns["pop"].clip(lower=1).to_numpy()))


@st.cache_data(show_spinner=False)
def place_name(latitude, longitude):
    """Where a point is, as people would say it: 'Zürich, Switzerland' in a
    town, '33 km E of Valencia, Spain' near one. Within 250 km a big town is
    worth going a little further for than a small one (ten times the people
    for each 40 km); past that, the nearest of any size."""
    if not PLACES.exists():
        return coords_label(latitude, longitude)
    towns, lat, lon, size = town_table()
    here, east = np.radians(latitude), np.radians(longitude)
    km = 2 * 6371 * np.arcsin(np.sqrt(
        np.sin((lat - here) / 2) ** 2 + np.cos(here) * np.cos(lat) * np.sin((lon - east) / 2) ** 2
    ))
    near = km < 250
    index = int(np.argmax(np.where(near, size - km / 40, -np.inf)) if near.any() else np.argmin(km))
    town = towns.iloc[index]
    where = f"{town['name']}, {town['country']}"
    if km[index] < 10:
        return where
    # Which way the point lies from the town, on eight points of the compass.
    bearing = np.degrees(np.arctan2(
        np.sin(east - lon[index]) * np.cos(here),
        np.cos(lat[index]) * np.sin(here)
        - np.sin(lat[index]) * np.cos(here) * np.cos(east - lon[index]),
    ))
    return f"{km[index]:,.0f} km {COMPASS[::2][round(bearing % 360 / 45) % 8]} of {where}"


def spot_title(spot):
    """A spot's name, or where it is when its name is only its coordinates,
    which is what a spot nobody named is called."""
    name = spot["name"]
    if re.fullmatch(r"[+-]?\d+(\.\d+)?, *[+-]?\d+(\.\d+)?", name.strip()):
        return place_name(float(spot["lat"]), float(spot["lon"]))
    return name


def km_from(latitude, longitude, lats, lons):
    """How far each of many points is from one, in km."""
    # A flat projection around the point is plenty to tell near from far;
    # the longitude wraps so the date line is no wall.
    east = np.radians((lons - longitude + 180) % 360 - 180) * np.cos(np.radians(latitude))
    north = np.radians(lats - latitude)
    return np.hypot(east, north) * 6371


def save_station(spot, target, times, speed, bearing, celsius, credit):
    """A station's hourly readings stored in the layout of an ERA5 download,
    so everything downstream reads both. `speed` is in m/s, `bearing` where
    the wind comes from in degrees, `celsius` the air, if recorded."""
    # Stations report speed and where the wind comes from; ERA5 gives the
    # eastward and northward parts. Convert so the two read the same.
    speed = speed.to_numpy(dtype="float32")
    bearing = np.radians(bearing.to_numpy(dtype="float32"))
    data = {
        "u10": ("valid_time", -speed * np.sin(bearing)),
        "v10": ("valid_time", -speed * np.cos(bearing)),
    }
    if celsius is not None and celsius.notna().any():
        # Kelvin, as ERA5 stores it.
        data["t2m"] = ("valid_time", celsius.to_numpy(dtype="float32") + 273.15)

    # The file carries its own station details, so it lists properly even
    # without data/locations.json.
    dataset = xr.Dataset(
        data,
        coords={"valid_time": times.to_numpy(dtype="datetime64[ns]")},
        attrs={
            "source": credit, "station": spot["station"], "name": spot["name"],
            "lat": float(spot["lat"]), "lon": float(spot["lon"]),
            "height": float(spot["height"]),
        },
    )
    partial = target.with_suffix(".part")
    dataset.to_netcdf(partial)
    partial.replace(target)
    return target


def fetch_station(spot, target, progress=None):
    """Every hourly file MeteoSwiss holds for a station (one per decade, plus
    this year up to yesterday) joined into one record.
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
    return save_station(
        spot, target, frame["time"], frame["fkl010h0"], frame["dkl010h0"],
        frame.get("tre200h0"), "MeteoSwiss SwissMetNet (CC BY 4.0)",
    )


def source_credit(spot):
    """Where a spot's wind comes from, for the small print."""
    if spot.get("station"):
        return f"{STATION_NAMES[spot['source']]} station {spot['station']}"
    return "ERA5 reanalysis"


def can_score(activity):
    """A range that starts at 0 kt counts every day as sailable, so a score
    would say nothing about the spot — such a choice is for exploring only."""
    return ACTIVITIES[activity]["groove"][0] > 0


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
    for source, prefix in STATION_FILES.items():
        for path in sorted(data_dir.glob(f"{prefix}_*.nc")):
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
            found[path.stem] = {**entry, "source": source, "path": path}
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


def remembered(widget, label, *args, key, default, start="value", memory=None, **options):
    """Draw a widget whose choice outlives the views that hide it.

    Streamlit forgets a widget's state once it is not drawn, so the value is
    copied aside every run and handed back as the widget's own starting value
    (`start` names that argument). Not put back through session state: a
    widget drawn from there alone shows its blank default, and a range slider
    even falls back to its full span and then reports that span back.
    `memory` lets two widgets share one value; an emptied choice keeps it."""
    memory = memory or f"kept_{key}"
    kept_value = st.session_state.get(memory, default)
    value = widget(label, *args, key=key, **{start: kept_value}, **options)
    if value is None:
        value = kept_value
    st.session_state[memory] = value
    return value


def kept(key, default):
    """A remembered setting's value on a screen that does not draw its widget.
    Read from the copy remembered() keeps, never from the widget's own state,
    which a redrawn widget can leave empty."""
    return st.session_state.get(f"kept_{key}", default)


def wind_words(wind):
    """6–25 kn, 15+ kn or up to 12 kn: a wind range as a sailor says it."""
    low, high = wind
    if high >= WIND_TOP:
        return f"{low}+ kn"
    return f"up to {high} kn" if low == 0 else f"{low}–{high} kn"


def sessions_mask(index, windows):
    """True where the hour falls in any of the chosen sailing windows."""
    if not windows or any(window is None for window in windows):
        return pd.Series(True, index=index)
    combined = pd.Series(False, index=index)
    for window in windows:
        combined |= hour_mask(index, window)
    return combined


def lookback_start(frame, years_back):
    """First year of the last `years_back` years on record; None for all of them."""
    first, last = frame.index.min().year, frame.index.max().year
    return first if years_back is None else max(first, last - years_back + 1)


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


def fresh_click(result, seen_key):
    """The click just made on a map, or None. A dot catches its click before
    the map does, so it reports as an object click (counted, so the same dot
    twice still registers) and open ground as a map click: whichever moved on
    is the new one. st_folium replays both on every rerun, hence comparing
    against the clicks already used rather than the current pick, which let
    a stale click overwrite a preset chosen a moment before."""
    state = result or {}
    ground, dot = state.get("last_clicked"), state.get("last_object_clicked")
    clicks = (
        (ground["lat"], ground["lng"]) if ground else None,
        state.get("last_object_clicked_count") or 0,
    )
    seen = st.session_state.get(seen_key, (None, 0))
    if clicks == seen:
        return None
    st.session_state[seen_key] = clicks
    if clicks[1] != seen[1]:
        return (dot["lat"], dot["lng"]) if dot else None
    return clicks[0]


def switch_source():
    """A station picked from one source is no station in another."""
    for key in ("station_pick", "station_find", "station_find_applied", "station_click_km"):
        st.session_state.pop(key, None)


if st.session_state["spot"] is None:
    saved = downloaded_locations()
    # The switch sits below the hero it rewrites, so read its value ahead.
    source = st.session_state.get("source_pick") or st.session_state["data_source"]
    stations, stations_error = None, None
    if source != "era5":
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
        "ride, in which months and at what time of day.</div>"
        '<div class="landing-steps">'
        "<span><b>1</b>Pick a spot</span>"
        "<span><b>2</b>Choose your sport</span>"
        "<span><b>3</b>Rate it or explore it</span>"
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
            on_change=switch_source,
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
            labels = (stations["name"] + " · " + stations.index.to_series()).to_dict()
            found = st.selectbox(
                "Find a station", list(stations.index), index=None, format_func=labels.get,
                placeholder=f"Search {len(stations):,} stations", key="station_find",
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
        st.error(f"Could not load the {STATION_NAMES[source]} stations: {stations_error}")
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
                dot_marker(entry["lat"], entry["lon"], spot_title(entry)).add_to(world)
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
                returned_objects=["last_clicked", "last_object_clicked",
                                  "last_object_clicked_count"],
            )
        else:
            focus = tuple(stations.loc[picked, ["lat", "lon"]]) if picked else SWISS_CENTRE
            world = base_map(
                location=list(focus), zoom_start=10 if picked else STATION_ZOOM[source],
                control_scale=True, min_zoom=2, world_copy_jump=True,
            )
            hint = (
                "Click another station to switch" if picked
                else "Click a station, or anywhere for the nearest one"
            )
            world.get_root().html.add_child(folium.Element(f'<div class="map-hint">{hint}</div>'))
            # The stations nearest where the map looks; the ones you have
            # downloaded stand out.
            on_disk = {entry["station"] for entry in saved.values() if entry.get("source") == source}
            near = km_from(*focus, stations["lat"], stations["lon"]).nsmallest(NEARBY_STATIONS).index
            for abbr, row in stations.loc[near].iterrows():
                if abbr != picked:
                    dot_marker(row["lat"], row["lon"], labels[abbr],
                               idle=abbr not in on_disk).add_to(world)
            if picked:
                dot_marker(*focus, labels[picked], picked=True).add_to(world)

            recentre = st.session_state.pop("station_recentre", False)
            result = st_folium(
                world, height=LANDING_MAP_HEIGHT, use_container_width=True,
                key=f"{source}_picker",
                center=list(focus) if recentre else None,
                zoom=10 if recentre else None,
                returned_objects=["last_clicked", "last_object_clicked",
                                  "last_object_clicked_count"],
            )

    fresh = fresh_click(result, "click_seen" if source == "era5" else "station_clicks_seen")
    if fresh is not None and source == "era5":
        # A dot is one of your spots: its own cell, so it opens as itself.
        st.session_state["clicked"] = fresh
        st.rerun()
    elif fresh is not None:
        km = km_from(*fresh, stations["lat"], stations["lon"])
        st.session_state["station_pick"] = km.idxmin()
        st.session_state["station_click_km"] = float(km.min())
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
                        where = (f"{STATION_NAMES[entry['source']]} {entry['station']} · "
                                 f"{entry['height']:.0f} m")
                    else:
                        where = f"ERA5 · {coords_label(entry['lat'], entry['lon'])}"
                    # Keyed by position: the hover script below matches cards to
                    # spots by this number.
                    with st.container(key=f"spot_card_{index}", horizontal=True,
                                      vertical_alignment="center"):
                        st.markdown(f"**{spot_title(entry)}**  \n:gray[{where} · {first}–{last}]")
                        if st.button("Open", key=f"open_{key}",
                                     icon=":material/arrow_forward:", width="content"):
                            enter_spot({k: v for k, v in entry.items() if k != "path"})
                            st.rerun()
        spots = [
            {"lat": entry["lat"], "lon": entry["lon"], "name": spot_title(entry)}
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
        cached = spot_path({"lat": latitude, "lon": longitude})

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
                    value=preset or (spot_title(known) if known else place_name(latitude, longitude)),
                    key=f"name_{latitude:+.2f}_{longitude:+.2f}",
                )
                if st.button(":material/arrow_forward: Use this location",
                             type="primary", width="stretch"):
                    enter_spot({"lat": latitude, "lon": longitude, "zone": zone, "name": name})
                    st.rerun()

    if picked:
        row = stations.loc[picked]
        station = {
            "lat": float(row["lat"]), "lon": float(row["lon"]),
            "zone": timezone_at(row["lat"], row["lon"]),
            "source": source, "station": picked, "height": float(row["height"]),
        }
        cached = spot_path(station)
        where = f"{source_credit(station)} · {coords_label(row['lat'], row['lon'])}"
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
                known = load_registry().get(spot_key(station), {})
                name = st.text_input(
                    "Name this spot", value=known.get("name") or row["name"],
                    key=f"name_{spot_key(station)}",
                )
                if st.button(":material/arrow_forward: Use this station",
                             type="primary", width="stretch"):
                    enter_spot({**station, "name": name})
                    st.rerun()
    st.stop()


# Open on a sport rather than "Any wind", which cannot be rated.
st.session_state.setdefault("activity", "Dinghy")
activity = st.session_state["activity"]
spot = st.session_state["spot"]
LATITUDE, LONGITUDE = spot["lat"], spot["lon"]
LOCAL_TZ, LOCATION_NAME = spot["zone"], spot_title(spot)
# The station's code, or None for an ERA5 cell.
STATION = spot.get("station")
SOURCE_CREDIT = source_credit(spot)
dataset_path = spot_path(spot)

# Navigation: step back one screen without losing the location.
# One back / forward pair, plus the reset to the very beginning.
# Back steps out of a view to the options, or off the options back to the map.
# Forward returns to whichever view you last left.
current_mode = st.session_state.get("mode")
# What the map screen remembers about your last pick, cleared on the way back.
LANDING_PICKS = ("clicked", "preset_applied", "station_pick", "station_find_applied",
                 "station_click_km", "station_clicks_seen")
nav = topbar.container(horizontal=True, width="content", vertical_alignment="center",
                       gap="small", key="nav")

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


def flag_hours(frame, first_year, wind):
    """The record from `first_year` on, with each hour flagged when its wind
    is in your range."""
    frame = frame[frame.index.year >= first_year]
    return frame, (frame["speed"] * MS_TO_KNOTS).between(*wind)


@st.cache_data(show_spinner=False, max_entries=256)
def good_days(path, mtime, zone, years_back, wind, lasting, hours):
    """Per local day over a spot's own last `years_back` years: did your
    hours on the water hold your wind `lasting` hours in a row?"""
    frame = load_wind(path, mtime, zone)
    frame, good = flag_hours(frame, lookback_start(frame, years_back), wind)
    return lasting_days(good[hour_mask(frame.index, hours)], lasting)


if "mode" not in st.session_state:
    st.session_state["mode"] = None


# --- Start screen ---------------------------------------------------------
# Your spot beside its map, then the sport, then two ways in, and the
# screen builds up in that order when you arrive. The cards only say what
# each view answers, and their drawings show nothing about this spot: the
# answers wait until you open one.

CHOICES = [
    ("rating", "One number", "Rate this spot",
     "One score out of ten for what you sail.", "Rate it"),
    ("explore", "The whole picture", "Explore this spot",
     "Windy days month by month, and the wind rose of each month.",
     "Explore"),
]
# Knots across the sport's ruler: enough for the kite's top end.
GROOVE_SCALE = 40

# The order the screen builds up in. Sent only when you arrive, so a sport
# change redraws at once. Only these rules animate these elements: one left
# behind would restart when the sheet goes away.
START_BUILD_CSS = """
<style>
@keyframes drawRule { from { transform: scaleX(0); } to { transform: none; } }
@media (prefers-reduced-motion: no-preference) {
    .spot-hero .hero-eyebrow { animation: riseIn .45s ease both; }
    .spot-hero .accent-rule {
        transform-origin: left; animation: drawRule .9s cubic-bezier(.65, 0, .35, 1) .25s both;
    }
    .spot-hero .stat-strip span { animation: riseIn .4s ease both; }
    .spot-hero .stat-strip span:nth-child(1) { animation-delay: .5s; }
    .spot-hero .stat-strip span:nth-child(2) { animation-delay: .57s; }
    .spot-hero .stat-strip span:nth-child(3) { animation-delay: .64s; }
    .spot-hero .stat-strip span:nth-child(4) { animation-delay: .71s; }
    .step-2 { animation: riseIn .45s ease .8s both; }
    .st-key-activity_pick { animation: riseIn .45s ease .88s both; }
    .groove { animation: riseIn .45s ease .96s both; }
    .groove-band { animation-delay: 1.1s; }
    .groove em { animation-delay: 1.55s; }
    .step-3 { animation: riseIn .45s ease 1.15s both; }
    [class*="st-key-choice_"] { animation: riseIn .6s cubic-bezier(.2, .7, .3, 1) both; }
    .st-key-choice_rating { animation-delay: 1.25s; }
    .st-key-choice_explore { animation-delay: 1.37s; }
    /* Then each instrument puts itself together. */
    .dial i { animation: fadeIn .2s ease both; animation-delay: calc(1.6s + var(--n) * .045s); }
    .dial-needle, .dial::after, .dial-read { animation: fadeIn .4s ease 2.1s both; }
    .grid > span { animation: fadeIn .3s ease both; animation-delay: calc(1.7s + var(--c) * .04s); }
}
</style>
"""


def dial_face():
    """Rate: a 0-10 meter whose needle hunts, beside a readout still to come."""
    ticks = "".join(f'<i style="--n:{n}"></i>' for n in range(11))
    return (
        f'<div class="dial">{ticks}<span class="dial-needle"><span></span></span></div>'
        '<div class="dial-read"><b>?</b><small>out of 10</small></div>'
    )


def grid_face():
    """Explore: twelve months by six times of day."""
    return '<div class="grid">' + "".join(
        f'<span style="--c:{column}">'
        + "".join(f'<i style="--r:{row}"></i>' for row in range(6))
        + f"<small>{letter}</small></span>"
        for column, letter in enumerate("JFMAMJJASOND")
    ) + "</div>"


FACES = {"rating": dial_face, "explore": grid_face}


def groove_ruler(low, high, note):
    """The sport's range of wind, marked on a 0-40 kt ruler."""
    def at(knots):
        return f"{knots / GROOVE_SCALE * 100:g}%"

    marks = "".join(
        f'<span style="left:{at(knots)}">{knots}</span>'
        for knots in range(0, GROOVE_SCALE + 1, 10)
    )
    return (
        '<div class="groove"><div class="groove-track">'
        f'<i class="groove-band" style="left:{at(low)};width:{at(high - low)}"></i>'
        f'<em style="left:{at(low)}">{low}</em><em style="left:{at(high)}">{high} kt</em>'
        f'</div><div class="groove-scale">{marks}</div>'
        f'<div class="groove-note">{note}</div></div>'
    )


def step_label(number, text):
    """A numbered step heading, with the landing's badges."""
    st.html(f'<div class="step-label step-{number}"><b>{number}</b>{text}</div>')


def sport_changed():
    """A new sport redraws the screen in place, without the build-up."""
    st.session_state["start_quiet"] = True


if st.session_state["mode"] is None:
    if not st.session_state.pop("start_quiet", False):
        st.html(START_BUILD_CSS)
    spot_col, map_col = st.columns([7, 5], gap="large", vertical_alignment="center")
    with spot_col:
        st.html(
            '<div class="hero spot-hero">'
            '<div class="hero-eyebrow spot-eyebrow"><b>1</b>Your spot</div>'
            f'<div class="hero-title reveal">{reveal(html.escape(LOCATION_NAME))}</div>'
            '<hr class="accent-rule">'
            '<div class="stat-strip">'
            f"<span><b>{abs(LATITUDE):.2f}°{'N' if LATITUDE >= 0 else 'S'}, "
            f"{abs(LONGITUDE):.2f}°{'E' if LONGITUDE >= 0 else 'W'}</b></span>"
            f"<span><b>{data_start.year}–{data_end.year}</b> hourly</span>"
            f"<span>local time <b>{LOCAL_TZ}</b></span>"
            + (
                f"<span>{STATION_NAMES[spot['source']]} <b>{STATION}</b> · "
                f"{spot['height']:.0f} m</span>"
                if STATION else "<span>source <b>ERA5</b></span>"
            )
            + "</div></div>"
        )
        step_label(2, "What are you sailing?")
        st.session_state["activity"] = pick(
            st.segmented_control(
                "What are you sailing?", list(ACTIVITIES),
                default=st.session_state["activity"],
                format_func=lambda name: f"{ACTIVITIES[name]['icon']} {name}",
                key="activity_pick", label_visibility="collapsed",
                on_change=sport_changed,
            ),
            st.session_state["activity"],
        )
        chosen = st.session_state["activity"]
        groove_low, groove_high = ACTIVITIES[chosen]["groove"]
        if can_score(chosen):
            note = (
                f"Scoring and the speed bands use {groove_low}–{groove_high} kt as the "
                "range worth going out in."
            )
        else:
            note = (
                f"{chosen} counts every day from {groove_low} to {groove_high} kt, so "
                "it is for exploring only. Pick a sport to rate the spot."
            )
        st.html(groove_ruler(groove_low, groove_high, note))
    with map_col:
        locator_map(LATITUDE, LONGITUDE, key="start_map", height=340, cell=not STATION)

    step_label(3, "What do you want to know?")
    for column, (target, kicker, title, blurb, action) in zip(
        st.columns(2, gap="medium"), CHOICES
    ):
        locked = target == "rating" and not can_score(chosen)
        lock_note = (
            '<div class="choice-lock">Pick a sport above to rate the spot.</div>'
            if locked else ""
        )
        with column.container(border=True, key=f"choice_{target}"):
            st.html(
                f'<div class="choice-face" aria-hidden="true">{FACES[target]()}</div>'
                f'<div class="choice-text"><div class="choice-kicker">{kicker}</div>'
                f'<div class="choice-title">{title}</div>'
                f'<div class="choice-blurb">{blurb}</div>{lock_note}</div>'
            )
            if st.button(action, key=f"pick_{target}", width="stretch", disabled=locked):
                st.session_state["mode"] = target
                st.session_state.pop("last_mode", None)
                st.session_state["rating_fresh"] = target == "rating"
                st.rerun()
    st.stop()


# --- Settings ------------------------------------------------------------
# Inside a view the sidebar carries everything it counts, what you sail
# first. Explore and the rating share it, so the two cannot quietly count
# different hours.


def settings(mode):
    """The sidebar inside a view: what you sail, then everything else the
    view counts. Returns what you sail."""
    side = st.sidebar
    side.header("Settings")
    activity = side.selectbox(
        "What you sail", list(ACTIVITIES),
        index=list(ACTIVITIES).index(st.session_state["activity"]), key="activity_side",
    )
    st.session_state["activity"] = activity
    groove = ACTIVITIES[activity]["groove"]
    if mode == "rating":
        side.caption(f"Sailable wind {groove[0]}–{groove[1]} kt")
    else:
        remembered(side.slider, "Wind", 0, WIND_TOP, format="%d kn",
                   key=f"wind_{activity}", default=groove)
    hours = remembered(side.slider, "On the water", 0, 23, format="%d:00",
                       key="hours", default=DEFAULT_HOURS)
    side.caption("Every hour of the day" if hours == (0, 23)
                 else f"{hours[0]:02d}:00–{hours[1]:02d}:59 local time")
    if mode == "rating":
        return activity
    remembered(side.segmented_control, "In a row", [1, 2, 3], format_func=lambda h: f"{h} h",
               required=True, key="lasting", default=DEFAULT_LASTING, start="default")
    remembered(side.segmented_control, "Years", list(LOOKBACK), required=True,
               key="years", default=DEFAULT_YEARS, start="default")
    return activity


# --- Explore: the spot at a glance -----------------------------------------
# What the wind does here all year on one screen: four figures, the windy
# days month by month, and the wind rose of the month you click beside the
# map, with your settings in the sidebar.

POINT_WORDS = {"N": "north", "E": "east", "S": "south", "W": "west"}

# The styles of explore's overview, sent with it.
ANSWER_CSS = """
<style>
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; margin-top: 1rem; font-size: .8rem; color: #8b98a5; }
.legend span::before {
    content: ""; display: inline-block; width: .6rem; height: .6rem; margin-right: .45rem;
    border-radius: 50%; background: var(--c);
}

</style>
"""


def compass_words(point):
    """ENE as said aloud: east-northeast."""
    words = [POINT_WORDS[letter] for letter in point]
    return f"{words[0]}-{''.join(words[1:])}" if len(words) == 3 else "".join(words)


def water_hours(hours):
    start, end = hours
    return [(start + step) % 24 for step in range((end - start) % 24 + 1)]


def best_hours(pct, hours, length=3):
    """The `length` hours in a row on the water that most often have it."""
    span = water_hours(hours)
    length = min(length, len(span))
    runs = [span[i:i + length] for i in range(len(span) - length + 1)]
    return max(runs, key=lambda run: pct.reindex(run).fillna(0).mean())


MONTHS = [calendar.month_abbr[month] for month in range(1, 13)]


def section(title, note):
    """A heading for one part of the overview, with a quiet note beside it."""
    st.html(f'<div class="section-label">{title}<small>{note}</small></div>')


def band_legend(ladder):
    """The speed bands in the colours the rose gives them."""
    return '<div class="legend">' + "".join(
        f'<span style="--c:{colour}">over {threshold} kt</span>'
        for threshold, colour in zip(ladder, THRESHOLD_COLORS)
    ) + "</div>"


def over_bands(knots, groups, ladder):
    """Per group, the share of `knots` above each speed of the ladder, in %."""
    return pd.DataFrame({
        threshold: (knots > threshold).groupby(groups).mean() * 100 for threshold in ladder
    })


def month_chart(share):
    """Each month as nested bars, one per speed band and the lightest
    longest, drawn first. A click on a month picks it for the wind rose."""
    pick = alt.selection_point(name="month", fields=["month"], on="click")
    y = alt.Y("name:N", sort=MONTHS, title=None,
              axis=alt.Axis(ticks=False, domain=False, labelPadding=8, labelFontSize=12))
    x = alt.X("pct:Q", scale=alt.Scale(domain=[0, 100]), title="% of days",
              axis=alt.Axis(values=[0, 25, 50, 75, 100], ticks=False, domain=False, grid=False))
    months = pd.DataFrame({"month": range(1, 13), "name": MONTHS})
    layers = [
        alt.Chart(months.assign(pct=share[threshold].reindex(range(1, 13)).fillna(0).to_numpy()))
        .mark_bar(height=20, cornerRadiusEnd=3, color=colour)
        .encode(y=y, x=x, opacity=alt.condition(pick, alt.value(1.0), alt.value(0.25)))
        for threshold, colour in zip(share.columns, THRESHOLD_COLORS)
    ]
    # An invisible bar across each row on top, so a month is easy to hit
    # even where its own bars stop short. It carries the month's numbers.
    bands = {f"over {threshold} kt": share[threshold].reindex(range(1, 13)).fillna(0).to_numpy()
             for threshold in share.columns}
    layers.append(
        alt.Chart(months.assign(**bands)).mark_bar(height=20, opacity=0)
        .encode(y=y, x=alt.value(0), x2=alt.value(alt.expr("width")), tooltip=[
            alt.Tooltip("name:N", title="month"),
            *[alt.Tooltip(f"{band}:Q", title=f"% of days {band}", format=".0f") for band in bands],
        ])
        .add_params(pick)
    )
    return alt.layer(*layers).properties(height=470)


if st.session_state["mode"] == "explore":
    st.html(ANSWER_CSS)
    activity = settings("explore")
    ladder = ACTIVITIES[activity]["ladder"]
    wind = tuple(kept(f"wind_{activity}", ACTIVITIES[activity]["groove"]))
    hours = tuple(kept("hours", DEFAULT_HOURS))
    lasting = kept("lasting", DEFAULT_LASTING)
    years_back = LOOKBACK[kept("years", DEFAULT_YEARS)]

    recent = df[df.index.year >= lookback_start(df, years_back)]
    knots = recent["speed"] * MS_TO_KNOTS
    direction = wind_direction(recent["u10"], recent["v10"])
    water = hour_mask(recent.index, hours)
    with st.spinner("Counting your days..."):
        days = good_days(dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ,
                         years_back, wind, lasting, hours)
    years_text = f"{recent.index.min().year}–{recent.index.max().year}"
    hours_text = "all day" if hours == (0, 23) else f"{hours[0]:02d}:00–{hours[1]:02d}:59"

    st.html(
        f'<div class="hero"><div class="hero-eyebrow">Explore · {activity}</div>'
        f'<div class="hero-title reveal">{reveal(html.escape(LOCATION_NAME))}</div>'
        '<hr class="accent-rule"></div>'
    )
    st.caption(
        f"{years_text} · your hours on the water {hours_text}, local time · "
        f"data: {SOURCE_CREDIT}"
    )

    # Four figures: how often, when in the year, when in the day, from where.
    per_month = days.groupby(days.index.month).mean()
    in_range = knots.between(*wind)
    best = int(per_month.idxmax()) if days.any() else None
    stretch = best_hours(in_range.groupby(recent.index.hour).mean(), hours)
    figures = st.columns(4)
    figures[0].metric(
        "Days with your wind", f"{days.mean() * 365:.0f} a year" if len(days) else "–",
        border=True, icon=":material/event_available:",
        help=f"Days when {wind_words(wind)} holds {lasting} h in a row, {hours_text}, "
        f"in {years_text}",
    )
    figures[1].metric(
        "Best month", calendar.month_name[best] if best else "–", border=True,
        icon=":material/trending_up:", delta_arrow="off", delta_color="off",
        delta=f"about {per_month[best] * calendar.monthrange(2001, best)[1]:.0f} days with it"
        if best else None,
    )
    figures[2].metric(
        "Best time of day",
        f"{stretch[0]:02d}:00–{stretch[-1]:02d}:59" if in_range[water].any() else "–",
        border=True, icon=":material/schedule:",
        help="The three hours in a row, of your hours on the water, that most often have your wind",
    )
    figures[3].metric(
        "Wind comes from", prevailing_direction(direction[water]), border=True,
        icon=":material/explore:", help=f"In your hours, typically a {beaufort(knots[water].mean())}",
    )

    # The month by month bands beside the rose of the month you click.
    daily = knots[water].resample("1D").mean().dropna()
    month_share = over_bands(daily, daily.index.month, ladder)
    chart_col, rose_col = st.columns([3, 2], gap="large")
    with chart_col:
        section("Windy days, month by month", "days whose wind over your hours tops each speed")
        st.html(band_legend(ladder))
        event = st.altair_chart(month_chart(month_share), on_select="rerun", key="month_pick",
                                width="stretch")
        picked = (event.selection or {}).get("month") or []
        month = picked[0]["month"] if picked else None
        st.caption("Click a month for its wind rose, double-click for the whole year.")
    when = "All year" if month is None else calendar.month_name[month]
    here = water & (recent.index.month == month) if month else water
    with rose_col:
        section(when, "where the wind comes from in your hours")
        if here.any():
            rose = draw_rose(direction[here], knots[here], ladder, "knots")
            st.pyplot(rose, width="stretch")
            plt.close(rose)
            st.caption(
                f"Mostly from the {compass_words(prevailing_direction(direction[here]))}, "
                f"{knots[here].mean():.0f} kt on average. Check the map: is that way open water?"
            )
        else:
            st.info("No readings in your hours.")
        locator_map(LATITUDE, LONGITUDE, key="explore_map", height=220, cell=not STATION)

    st.stop()


# --- Rating ---------------------------------------------------------------

if st.session_state["mode"] == "rating":
    activity = settings("rating")
    # Forward, or switching sport in the sidebar, can land here with a range
    # that has nothing to score.
    if not can_score(activity):
        st.info(
            f"**{activity}** counts every day as sailable, so there is nothing to "
            "score. Pick what you sail in the sidebar."
        )
        st.stop()
    groove_low, groove_high = ACTIVITIES[activity]["groove"]
    hours = tuple(kept("hours", DEFAULT_HOURS))
    SESSION_WINDOWS = (hours,)
    SESSION_TEXT = "all day" if hours == (0, 23) else f"{hours[0]:02d}:00–{hours[1]:02d}:59"

    first_year = max(data_start.year, data_end.year - RATING_YEARS + 1)
    with st.spinner("Reading the last 20 years..."):
        rating = rate_location(
            dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ, first_year,
            activity, SESSION_WINDOWS,
        )

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
        rose = draw_rose(rating["direction"], rating["knots"], ACTIVITIES[activity]["ladder"], "knots")
        st.pyplot(rose, width="stretch")
        plt.close(rose)
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
