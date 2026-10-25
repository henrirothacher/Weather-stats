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
APP_VERSION = "2.4"

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
    .step-label b, .answered b, .asking b, .upcoming b {
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

    /* The three ways in. Each is drawn as a small instrument that idles cool
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
    [class*="st-key-choice_"]:has(button:disabled) :is(.dial-needle > span, .grid i, .days-frame > span) {
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
    .grid small, .day small {
        font-size: .62rem; font-weight: 600; line-height: 1; color: #5b6b7a;
    }
    .grid small { margin-top: 3px; }
    @keyframes cellWash {
        0%, 46%, 100% { background: #17212b; }
        18%           { background: var(--tone); }
        28%           { background: color-mix(in srgb, var(--tone) 55%, #17212b); }
    }

    /* Plan a trip: two weeks with a frame stepping along them. */
    .days { position: relative; display: flex; gap: 4px; }
    .day { display: flex; flex-direction: column; align-items: center; gap: 5px; }
    .day i { width: 16px; height: 28px; border-radius: 3px; background: #17212b; }
    .day:nth-child(7n) i, .day:nth-child(7n+6) i { background: #1e2a36; }
    .days-frame { position: absolute; left: 0; bottom: 0; width: 100%; height: 28px; }
    .days-frame > span {
        position: absolute; left: -3px; top: -3px; width: 102px; height: 34px;
        box-sizing: border-box; border: 2px solid var(--glow); border-radius: 5px;
        background: color-mix(in srgb, var(--tone) 24%, transparent);
        animation: frameScan 7s infinite;
    }
    @keyframes frameScan {
        0%   { transform: translateX(0); animation-timing-function: steps(9, end); }
        60%  { transform: translateX(180px); animation-timing-function: steps(9, end); }
        100% { transform: translateX(0); }
    }
    @media (prefers-reduced-motion: reduce) {
        .dial-needle > span, .grid i, .days-frame > span { animation: none; }
    }

    /* Explore and the trip planner: one question at a time. */
    .view-title { font-size: 1.9rem; font-weight: 800; letter-spacing: -.03em; line-height: 1.15; }
    .view-sub { color: #a9b6c3; font-size: .98rem; margin: .3rem 0 .9rem; }
    .answered, .upcoming {
        display: flex; align-items: center; gap: .75rem; font-size: .95rem; padding: .2rem 0;
    }
    .answered b { border-color: rgba(251, 133, 0, .55); color: #fb8500; }
    .answered span { color: #8b98a5; min-width: 5.5rem; }
    .answered strong { color: #e8eef5; font-weight: 700; }
    .upcoming { color: #5b6b7a; }
    .upcoming b { color: #5b6b7a; }
    .st-key-asking {
        border: 1px solid rgba(251, 133, 0, .55); border-radius: 10px;
        padding: 1.1rem 1.3rem 1.2rem; margin: .35rem 0; background: #0f161e;
        animation: riseIn .4s ease both;
    }
    .asking {
        display: flex; align-items: center; gap: .75rem;
        font-size: 1.2rem; font-weight: 800; color: #e8eef5;
    }
    .asking b { background: #fb8500; border-color: #fb8500; color: #0b0f14; }
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
# Where explore and the trip planner start, before you change anything.
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

# Where the wind comes from: a model cell anywhere on earth, or a station
# that measured it, from MeteoSwiss or an airport's METAR reports. All end
# up in the same file layout.
SOURCES = {
    "era5": ":material/public: ERA5 · worldwide",
    "meteoswiss": ":material/sensors: MeteoSwiss · Swiss stations",
    "metar": ":material/flight: METAR · airports worldwide",
}
# Each kind of station: what to call it, and the prefix of its file in data/.
STATION_NAMES = {"meteoswiss": "MeteoSwiss", "metar": "METAR"}
STATION_FILES = {"meteoswiss": "station", "metar": "metar"}
# MeteoSwiss open data: free, no account, semicolon CSV per station.
OGD_ROOT = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn"
OGD_ITEMS = "https://data.geo.admin.ch/api/stac/v1/collections/ch.meteoschweiz.ogd-smn/items"
# METARs as the Iowa Environmental Mesonet archives them: free, no account,
# one request a second at most.
IEM = "https://mesonet.agron.iastate.edu"
# Where a station map opens before you pick one, and how many of the
# stations around what it looks at it draws.
SWISS_CENTRE = (46.8, 8.2)
STATION_ZOOM = {"meteoswiss": 8, "metar": 5}
NEARBY_STATIONS = 200
# A station this close to the sea or a lake, in km, is by the water. The
# shores are Natural Earth's (public domain): coasts and lakes at 1:10m,
# with the extra lakes it maps for Europe and North America.
SHORE_KM = 5
SHORES = data_dir / "shores.npy"
NATURAL_EARTH = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
SHORE_LAYERS = ["coastline", "lakes", "lakes_europe", "lakes_north_america"]


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
    # A summit station looks down on the water rather than standing by it.
    summit = stations["station_exposition_en"].reindex(table.index) == "crest"
    table["by_water"] = by_a_shore(table) & ~summit
    return table.sort_values("name")


@st.cache_data(ttl="1d", show_spinner="Loading the METAR stations...")
def metar_stations():
    """Every airport the IEM archives METARs for, and since when. The list of
    all its stations is one file; the airports are the networks ending ASOS."""
    response = requests.get(
        f"{IEM}/sites/networks.php",
        params={"network": "_ALL_", "format": "csv", "nohtml": "on"}, timeout=120,
    )
    response.raise_for_status()
    table = pd.read_csv(io.BytesIO(response.content), keep_default_na=False, na_values=[""])
    table = table[table["iem_network"].str.endswith("ASOS")].dropna(subset=["begints"])
    table = table.drop_duplicates("stid").set_index("stid")
    # American names come in capitals: BANGOR INTL ARPT.
    names = table["station_name"].where(
        ~table["station_name"].str.isupper(), table["station_name"].str.title()
    )
    table = pd.DataFrame({
        "name": names, "lat": table["lat"], "lon": table["lon"], "height": table["elev"],
        "since": pd.to_datetime(table["begints"]).dt.year,
        # Every METAR carries the air temperature.
        "has_temp": True,
    })
    table["by_water"] = by_a_shore(table)
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


def spot_names(spots):
    """Each spot's title, with its key added where two share one."""
    titles = {key: spot_title(entry) for key, entry in spots.items()}
    names = list(titles.values())
    return {
        key: title if names.count(title) == 1 else f"{title} · {key}"
        for key, title in titles.items()
    }


def km_from(latitude, longitude, lats, lons):
    """How far each of many points is from one, in km."""
    # A flat projection around the point is plenty to tell near from far;
    # the longitude wraps so the date line is no wall.
    east = np.radians((lons - longitude + 180) % 360 - 180) * np.cos(np.radians(latitude))
    north = np.radians(lats - latitude)
    return np.hypot(east, north) * 6371


def coordinates(nested):
    """Every position in a GeoJSON geometry's coordinates, however deep."""
    if isinstance(nested[0], (int, float)):
        yield nested
    else:
        for part in nested:
            yield from coordinates(part)


@st.cache_resource(show_spinner="Mapping the world's shores...")
def shores():
    """Every point along the world's coasts and lake shores to the nearest
    hundredth of a degree, about a kilometre, as rows of latitude and
    longitude sorted by latitude. Built from Natural Earth the first time
    and kept in data/."""
    if not SHORES.exists():
        points = set()
        for layer in SHORE_LAYERS:
            response = requests.get(f"{NATURAL_EARTH}/ne_10m_{layer}.geojson", timeout=120)
            response.raise_for_status()
            for feature in response.json()["features"]:
                for lon, lat, *_ in coordinates(feature["geometry"]["coordinates"]):
                    points.add((round(lat * 100), round(lon * 100)))
        np.save(SHORES, np.array(sorted(points), dtype=np.int16))
    return np.load(SHORES) / 100


def by_a_shore(stations):
    """Which stations lie within SHORE_KM of the sea or a lake shore. Each
    looks only at the shore in its own band of latitude."""
    points = shores()
    reach = SHORE_KM / 111  # in degrees of latitude
    near = []
    for latitude, longitude in zip(stations["lat"], stations["lon"]):
        low, high = np.searchsorted(points[:, 0], [latitude - reach, latitude + reach])
        band = points[low:high]
        near.append(bool((km_from(latitude, longitude, band[:, 0], band[:, 1]) <= SHORE_KM).any()))
    return pd.Series(near, index=stations.index)


def save_station(spot, target, times, speed, bearing, celsius, credit, **details):
    """A station's hourly readings stored in the layout of an ERA5 download,
    so everything downstream reads both. `speed` is in m/s, `bearing` where
    the wind comes from in degrees, `celsius` the air, if recorded. Any
    `details` go into the file beside the station's own."""
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
            "height": float(spot["height"]), **details,
        },
    )
    partial = target.with_suffix(".part")
    dataset.to_netcdf(partial)
    partial.replace(target)
    return target


def hourly_files(abbr):
    """Every hourly file MeteoSwiss holds for a station: one per decade, plus
    this year up to yesterday. Historical sorts before recent, so where the
    two meet, recent wins."""
    item = requests.get(f"{OGD_ITEMS}/{abbr.lower()}", timeout=60)
    item.raise_for_status()
    return sorted(
        asset["href"] for name, asset in item.json()["assets"].items()
        if "_h_historical_" in name or name.endswith("_h_recent.csv")
    )


def by_hour(frame):
    """MeteoSwiss rows by the UTC hour they cover, one row an hour."""
    stamp = pd.to_datetime(frame["reference_timestamp"], format="%d.%m.%Y %H:%M")
    # Timestamps are UTC and mark the end of the hour (16:00 = 15:10–16:00),
    # so step each back to the hour it covers. Before 2018 the hour ran
    # 15:50–16:40 and already carries the right label.
    stamp = stamp - pd.to_timedelta((stamp >= "2018-01-01").astype(int), unit="h")
    return frame.assign(time=stamp).drop_duplicates("time", keep="last").set_index("time").sort_index()


def fetch_station(spot, target, progress=None):
    """Every hourly reading MeteoSwiss holds for a station, joined into one
    record. A station that measures no air temperature borrows it from the
    nearest one that does, 0.65 °C warmer for every 100 m it stands lower.
    `progress(done, total)` hears about each file as it lands."""
    stations = meteoswiss_stations()
    lender = None
    if not stations["has_temp"].get(spot["station"], True):
        others = stations[stations["has_temp"]]
        lender = km_from(spot["lat"], spot["lon"], others["lat"], others["lon"]).idxmin()
    files = [(abbr, url) for abbr in filter(None, (spot["station"], lender))
             for url in hourly_files(abbr)]
    if not any(abbr == spot["station"] for abbr, _ in files):
        raise RuntimeError("MeteoSwiss lists no hourly data for this station")
    wanted = {"reference_timestamp", "fkl010h0", "dkl010h0", "tre200h0"}
    # MeteoSwiss serves each connection slowly, so fetch the files side by side.
    with ThreadPoolExecutor(max_workers=len(files)) as pool:
        jobs = [
            pool.submit(ogd_csv, url, usecols=lambda column: column in wanted)
            for _, url in files
        ]
        for done, _ in enumerate(as_completed(jobs), 1):
            if progress:
                progress(done, len(jobs))

    def joined(station):
        """A station's files in URL order, not arrival order, for the recent-wins rule."""
        return pd.concat([job.result() for (abbr, _), job in zip(files, jobs) if abbr == station],
                         ignore_index=True)

    frame = by_hour(joined(spot["station"]).dropna(subset=["fkl010h0", "dkl010h0"]))
    if frame.empty:
        raise RuntimeError("This station has no wind readings yet")
    details = {}
    if lender:
        air = by_hour(joined(lender).dropna(subset=["tre200h0"]))["tre200h0"]
        rise = stations.loc[lender, "height"] - spot["height"]
        frame["tre200h0"] = air.reindex(frame.index) + rise * 0.0065
        details["air_from"] = (
            f"{stations.loc[lender, 'name']} ({lender}, {stations.loc[lender, 'height']:.0f} m)"
        )
    return save_station(
        spot, target, frame.index, frame["fkl010h0"], frame["dkl010h0"],
        frame.get("tre200h0"), "MeteoSwiss SwissMetNet (CC BY 4.0)", **details,
    )


def fetch_metar(spot, target):
    """Every METAR the IEM holds for an airport, as one report an hour: the
    one nearest the top of the hour. One request; it can take a minute."""
    response = requests.get(f"{IEM}/cgi-bin/request/asos.py", params={
        "station": spot["station"], "data": "drct,sknt,tmpc", "tz": "Etc/UTC",
        "sts": "1900-01-01T00:00Z", "ets": f"{date.today()}T00:00Z",
        # Routine reports and the specials sent between them.
        "report_type": "3,4", "missing": "empty",
    }, timeout=600)
    response.raise_for_status()
    frame = pd.read_csv(io.BytesIO(response.content)).dropna(subset=["sknt"])
    stamp = pd.to_datetime(frame["valid"], format="%Y-%m-%d %H:%M")
    hour = stamp.dt.round("h")
    frame = (
        frame.assign(time=hour, off=(stamp - hour).abs())
        .sort_values("off").drop_duplicates("time").sort_values("time")
    )
    # Early years can hold as few as two reports a day, too few to tell a
    # day's wind by, so the record starts in the first year with hourly ones.
    year = frame["time"].dt.year
    per_day = year.value_counts() / frame["time"].dt.normalize().groupby(year).nunique()
    frame = frame[year >= per_day[per_day >= 10].index.min()]
    if frame.empty:
        raise RuntimeError("The IEM holds no hourly reports for this airport")
    # A variable wind (VRB) has a speed but no direction. It is light, a
    # knot or two, so it counts as calm rather than losing the hour.
    variable = frame["drct"].isna()
    return save_station(
        spot, target, frame["time"], frame["sknt"].mask(variable, 0) / MS_TO_KNOTS,
        frame["drct"].fillna(0), frame["tmpc"], "METAR, Iowa Environmental Mesonet",
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


def percent(value):
    """A share as people say it: a chance that is not quite zero is not 0%."""
    return "<1%" if 0 < value < 0.5 else f"{value:.0f}%"


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


# A window of dates is ((month, day), (month, day)) and holds in every year:
# 1–15 June is ((6, 1), (6, 15)). None is the whole year.

def in_dates(index, dates):
    """True on the calendar days inside the window, whatever the year. One
    that runs past New Year, 20 Dec to 10 Jan, wraps round."""
    if dates is None:
        return np.ones(len(index), dtype=bool)
    stamp = np.asarray(index.month * 100 + index.day)
    start, end = (month * 100 + day for month, day in dates)
    if start <= end:
        return (stamp >= start) & (stamp <= end)
    return (stamp >= start) | (stamp <= end)


def window_year(index, dates):
    """The year each day's window opened in, so 20 Dec – 10 Jan is one window."""
    years = np.asarray(index.year)
    if dates is None or dates[0] <= dates[1]:
        return years
    return years - (np.asarray(index.month * 100 + index.day) < dates[0][0] * 100 + dates[0][1])


def window_days(dates):
    """Every calendar day of the window in order, as (month, day). 29 Feb is
    left out: it comes round one year in four, too seldom to chart."""
    common = pd.date_range("2001-01-01", "2001-12-31")
    pairs = [(int(month), int(day)) for month, day in zip(common.month, common.day)]
    start, end = pairs.index(dates[0]), pairs.index(dates[1])
    return pairs[start:end + 1] if start <= end else pairs[start:] + pairs[:end + 1]


def dates_label(dates):
    """1–15 June, or 28 May – 10 June across a month end."""
    (first_month, first_day), (last_month, last_day) = dates
    first, last = calendar.month_name[first_month], calendar.month_name[last_month]
    if dates[0] == dates[1]:
        return f"{first_day} {first}"
    if first_month == last_month and first_day < last_day:
        return f"{first_day}–{last_day} {first}"
    return f"{first_day} {first} – {last_day} {last}"


def as_dates(picked):
    """The range from the date picker as a window, or None for a year or more.
    With only the first end picked so far, that one day is the window."""
    if not picked:
        return None
    start, end = picked[0], picked[-1]
    if (end - start).days >= 365:
        return None
    # Not every year has a 29 Feb; the 28th stands in for it.
    return tuple(
        (2, 28) if (day.month, day.day) == (2, 29) else (day.month, day.day)
        for day in (start, end)
    )


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
    # The switches sit below the hero they rewrite, so read their values ahead.
    source = st.session_state.get("source_pick") or st.session_state["data_source"]
    by_water = st.session_state.get("by_water", True)
    stations, stations_error = None, None
    if source != "era5":
        try:
            stations = meteoswiss_stations() if source == "meteoswiss" else metar_stations()
        except Exception as error:
            stations_error = error
    if stations is not None and by_water:
        stations = stations[stations["by_water"]]

    if source == "metar":
        pitch = (
            "Click near any airport in the world and pull every weather report it "
            "has sent: wind measured beside the runway, most of it every half hour. "
        )
        facts = [
            ("Hourly", "most since the 1970s"),
            ("Measured", "not modelled"),
            (f"{len(stations):,}" if stations is not None else "1,400+",
             "airports by the water" if by_water else "airports"),
        ]
    elif source == "meteoswiss":
        pitch = (
            "Click any MeteoSwiss weather station and pull every hour it has "
            "measured. Real readings from the shore rather than a 25 km model "
            "cell, so lakes and valleys get their own wind. "
        )
        facts = [
            (f"{stations['since'].min():.0f}" if stations is not None else "1981",
             "first year on record"),
            ("Measured", "not modelled"),
            (len(stations) if stations is not None else "25+",
             "stations by the water" if by_water else "stations with wind"),
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
        "ride, in which months, at what time of day — and what to expect on the dates "
        "you are going, next to your other spots.</div>"
        '<div class="landing-steps">'
        "<span><b>1</b>Pick a spot</span>"
        "<span><b>2</b>Choose your sport</span>"
        "<span><b>3</b>Rate it, explore it or plan a trip</span>"
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
            st.toggle(
                "By the water only", value=True, key="by_water",
                help=f"Stations within {SHORE_KM} km of the sea or a lake. Small lakes "
                "may be missing from the map of shores: switch this off to see them all.",
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
            # The stations nearest where the map looks (all of MeteoSwiss's, the
            # airports around you); the ones you have downloaded stand out.
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
                help=None if row["has_temp"] else "This station measures no air "
                "temperature, so it borrows it from the nearest one that does.",
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

if not dataset_path.exists() and STATION and spot["source"] == "metar":
    st.subheader(f"Download the record for {LOCATION_NAME}")
    st.caption(
        f"METAR station {STATION} · {spot['height']:.0f} m · {LOCAL_TZ}. "
        "Every report the airport has sent, from the Iowa Environmental Mesonet's "
        "archive; no account needed. It can take a minute."
    )
    if st.button("Fetch the METARs", type="primary"):
        with st.spinner("The Iowa Environmental Mesonet is gathering every report..."):
            try:
                fetch_metar(spot, dataset_path)
                st.rerun()
            except Exception as error:
                st.error(f"Could not fetch that airport: {error}")
    st.stop()

if not dataset_path.exists() and STATION:
    st.subheader(f"Download the record for {LOCATION_NAME}")
    st.caption(
        f"MeteoSwiss station {STATION} · {spot['height']:.0f} m · {LOCAL_TZ}. "
        "Every hour the station has measured, from MeteoSwiss open data; "
        "no account needed. It can take a minute or two."
        + ("" if meteoswiss_stations()["has_temp"].get(STATION, True) else
           " It measures no air temperature, so it borrows it from the nearest station "
           "that does, corrected for the difference in height.")
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
HAS_TEMP = "temp_c" in df.columns and bool(df["temp_c"].notna().any())


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


def flag_hours(frame, first_year, wind, dates):
    """The record from `first_year` on, cut to your dates (None for all of
    them), with each hour flagged when its wind is in your range. A window
    over New Year starts with the first one to open in `first_year`, so none
    is counted with only its January half."""
    opened = window_year(frame.index, dates)
    frame = frame[(opened >= first_year) & in_dates(frame.index, dates)]
    return frame, (frame["speed"] * MS_TO_KNOTS).between(*wind)


@st.cache_data(show_spinner=False, max_entries=256)
def good_days(path, mtime, zone, years_back, wind, lasting, hours):
    """Per local day over a spot's own last `years_back` years: did your
    hours on the water hold your wind `lasting` hours in a row?"""
    frame = load_wind(path, mtime, zone)
    frame, good = flag_hours(frame, lookback_start(frame, years_back), wind, None)
    return lasting_days(good[hour_mask(frame.index, hours)], lasting)


if "mode" not in st.session_state:
    st.session_state["mode"] = None


# --- Start screen ---------------------------------------------------------
# Your spot beside its map, then the sport, then three ways in, and the
# screen builds up in that order when you arrive. The cards only say what
# each view answers, and their drawings show nothing about this spot: the
# answers wait until you open one.

CHOICES = [
    ("rating", "One number", "Rate this spot",
     "One score out of ten for what you sail.", "Rate it"),
    ("explore", "The whole picture", "Explore this spot",
     "Windy days month by month and the wind rose, with more to switch on in the settings.",
     "Explore"),
    ("window", "Your dates", "Plan a trip",
     "Off to a regatta or a week away? See what to expect on your dates, beside other spots.",
     "Plan a trip"),
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
    .st-key-choice_window { animation-delay: 1.49s; }
    /* Then each instrument puts itself together. */
    .dial i { animation: fadeIn .2s ease both; animation-delay: calc(1.6s + var(--n) * .045s); }
    .dial-needle, .dial::after, .dial-read { animation: fadeIn .4s ease 2.1s both; }
    .grid > span { animation: fadeIn .3s ease both; animation-delay: calc(1.7s + var(--c) * .04s); }
    .day { animation: riseIn .3s ease both; animation-delay: calc(1.8s + var(--d) * .035s); }
    .days-frame { animation: fadeIn .4s ease 2.4s both; }
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


def days_face():
    """Plan a trip: two weeks, a frame stepping along them."""
    return '<div class="days">' + "".join(
        f'<span class="day" style="--d:{day}"><small>{letter}</small><i></i></span>'
        for day, letter in enumerate("MTWTFSS" * 2)
    ) + '<span class="days-frame"><span></span></span></div>'


FACES = {"rating": dial_face, "explore": grid_face, "window": days_face}


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
        st.columns(3, gap="medium"), CHOICES
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
                # The trip planner starts from its first question every time.
                st.session_state["trip_step"] = 0
                st.rerun()
    st.stop()


# --- Questions, one at a time --------------------------------------------
# The trip planner opens with a few questions: answered ones shrink to a
# line you can reopen, the one you are on gets the room, the ones to come
# wait greyed out, and the answer only once all are in. Then the sidebar
# carries every answer, to change while you look. Explore goes straight to
# its overview with the same settings in the sidebar, and the answers share
# their keys, so one given in either view is already there in the other
# and in the rating.

COUNT_WORDS = ["No", "One", "Two", "Three", "Four", "Five"]
# Where the trip's dates start before you pick: these days of next month.
TRIP_DAYS = (10, 16)
# A trip is at most this many days: drawn year by year, a dot a day.
YEAR_ROWS_UP_TO = 62


def coming_dates(first_day, last_day):
    """Those days of next month: where a date picker starts."""
    today = date.today()
    coming = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    return coming.replace(day=first_day), coming.replace(day=last_day)


def ask_wind(activity):
    """The range you would go out in, kept per sport."""
    remembered(
        st.slider, "Wind", 0, WIND_TOP, format="%d kn", label_visibility="collapsed",
        key=f"wind_{activity}", default=ACTIVITIES[activity]["groove"],
    )


def ask_hours():
    remembered(
        st.slider, "On the water", 0, 23, format="%d:00", label_visibility="collapsed",
        key="hours", default=DEFAULT_HOURS,
    )
    remembered(
        st.segmented_control, "In a row", [1, 2, 3],
        format_func=lambda h: f"{h} h in a row", required=True,
        label_visibility="collapsed", key="lasting", default=DEFAULT_LASTING, start="default",
    )


# The dates as a calendar you click: your first day, then your last, the
# range following the pointer in between. A component of our own, as
# Streamlit's date picker is a small field with a pop-up. It draws as many
# months side by side as there is room for, and only builds them anew when
# you turn the page: hovering and clicking just repaint the days.
CALENDAR_CSS = """
.cal { --orange: #fb8500; color: #e8eef5; font-variant-numeric: tabular-nums; }
.cal-top { display: flex; align-items: center; gap: .8rem; margin-bottom: 1rem; }
.cal-summary { flex: 1; min-width: 0; text-align: center; }
.cal-summary b {
    display: block; font-size: clamp(1.25rem, 2.6vw, 1.7rem); font-weight: 800;
    letter-spacing: -.03em; line-height: 1.2;
}
.cal-summary span { display: block; margin-top: .15rem; font-size: .85rem; color: #8b98a5; }
.cal-summary.far span { color: #ffb703; }
.nav {
    display: grid; place-items: center; width: 2.6rem; height: 2.6rem; flex: none;
    border: 1px solid #26333f; border-radius: 50%; background: #0f161e; color: #e8eef5;
    cursor: pointer; transition: border-color .2s ease, background .2s ease;
}
.nav:hover:not(:disabled) { border-color: var(--orange); background: #161f29; }
.nav:disabled { opacity: .3; cursor: default; }
.nav svg { width: 1.1rem; height: 1.1rem; }
.months { display: grid; grid-template-columns: repeat(var(--count), minmax(0, 1fr)); gap: 1.6rem; }
.month { min-width: 0; }
.month-name { margin: 0 0 .6rem .2rem; font-size: 1.05rem; font-weight: 800; letter-spacing: -.01em; }
.month-name small { margin-left: .3rem; font-size: .8rem; font-weight: 600; color: #5b6b7a; }
.dow, .days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
.dow span {
    padding-bottom: .4rem; text-align: center; font-size: .66rem; font-weight: 700;
    letter-spacing: .08em; text-transform: uppercase; color: #5b6b7a;
}
.day {
    position: relative; height: 2.75rem; padding: 0; border: 0; background: none;
    color: inherit; font: inherit; font-size: .92rem; cursor: pointer;
}
.day span {
    position: relative; z-index: 1; display: grid; place-items: center;
    width: 2.4rem; height: 2.4rem; margin: auto; border-radius: 50%;
    transition: background .15s ease, color .15s ease, box-shadow .15s ease;
}
.day:hover span { box-shadow: inset 0 0 0 1.5px var(--orange); }
.day.weekend { color: #c5d0da; }
.day.today span::after {
    content: ""; position: absolute; bottom: .3rem; left: 50%; width: 4px; height: 4px;
    margin-left: -2px; border-radius: 50%; background: #8ecae6;
}
.day:disabled { color: #2b3a48; cursor: default; }
.day:disabled span { box-shadow: none; }
/* The range as one band across each week, ends rounded at the picked days. */
.day.in::before {
    content: ""; position: absolute; inset: .2rem 0; background: rgba(251, 133, 0, .17);
}
.day.in.rs::before, .day.lo::before { border-top-left-radius: 1.2rem; border-bottom-left-radius: 1.2rem; }
.day.in.re::before, .day.hi::before { border-top-right-radius: 1.2rem; border-bottom-right-radius: 1.2rem; }
.day.lo::before { left: .25rem; }
.day.hi::before { right: .25rem; }
.day.lo span, .day.hi span { background: var(--orange); color: #0b0f14; font-weight: 800; }
.day.lo.today span::after, .day.hi.today span::after { background: #0b0f14; }
.cal.picking .day.in::before { background: rgba(251, 133, 0, .1); }
.cal.picking .day.hi:not(.lo) span { background: rgba(251, 133, 0, .55); }
.day.far::before { background: rgba(139, 152, 165, .12) !important; }
.day.far span { background: none !important; color: #5b6b7a !important; }
.cal-hint { margin-top: .9rem; text-align: center; font-size: .82rem; color: #8b98a5; }
@keyframes rise { from { opacity: 0; transform: translateY(10px); } }
@keyframes slideFrom { from { opacity: 0; transform: translateX(var(--from)); } }
.months.enter .month { animation: rise .5s cubic-bezier(.2, .7, .3, 1) both; }
.months.enter .month:nth-child(2) { animation-delay: .08s; }
.months.enter .month:nth-child(3) { animation-delay: .16s; }
.months.turn .month { animation: slideFrom .35s cubic-bezier(.2, .7, .3, 1) both; }
@media (prefers-reduced-motion: reduce) { .month { animation: none !important; } }
"""

CALENDAR_JS = """
const DAY = 86400000;
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];
const SHORT = MONTHS.map((name) => name.slice(0, 3));
const ARROW = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
  stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
const parse = (text) => { const [y, m, d] = text.split("-").map(Number); return Date.UTC(y, m - 1, d); };
const iso = (t) => new Date(t).toISOString().slice(0, 10);
const monthOf = (t) => { const d = new Date(t); return d.getUTCFullYear() * 12 + d.getUTCMonth(); };
const said = (t) => { const d = new Date(t); return `${d.getUTCDate()} ${SHORT[d.getUTCMonth()]}`; };
const span = (a, b) => Math.round(Math.abs(b - a) / DAY) + 1;

export default function (component) {
  const { data, setStateValue, parentElement } = component;
  const root = parentElement.querySelector(".cal");
  let s = root.__state;
  if (!s) {
    s = root.__state = { pending: null, hover: null, view: null, sent: null, built: null };
    root.innerHTML = `<div class="cal-top">
        <button class="nav prev" aria-label="Earlier months">${ARROW("M15 5l-7 7 7 7")}</button>
        <div class="cal-summary" aria-live="polite"><b></b><span></span></div>
        <button class="nav next" aria-label="Later months">${ARROW("M9 5l7 7-7 7")}</button>
      </div><div class="months"></div>
      <div class="cal-hint">Click your first day, then your last. One day? Click it twice.</div>`;
    root.addEventListener("click", (event) => {
      const nav = event.target.closest(".nav");
      if (nav) {
        const step = nav.classList.contains("next") ? 1 : -1;
        s.view += step;
        build(step > 0 ? "28px" : "-28px");
        return;
      }
      const day = event.target.closest(".day");
      if (!day || day.disabled) return;
      const t = Number(day.dataset.t);
      if (s.pending === null) {
        s.pending = t;
        s.hover = t;
      } else if (span(s.pending, t) > s.data.longest) {
        // Too long to be a trip: start again from here.
        s.pending = t;
        s.hover = t;
      } else {
        s.start = Math.min(s.pending, t);
        s.end = Math.max(s.pending, t);
        s.pending = null;
        s.sent = `${iso(s.start)}|${iso(s.end)}`;
        s.send("range", { start: iso(s.start), end: iso(s.end) });
      }
      paint();
    });
    root.addEventListener("mouseover", (event) => {
      const day = event.target.closest(".day");
      if (s.pending === null || !day || day.disabled) return;
      s.hover = Number(day.dataset.t);
      paint();
    });
    // As many months as fit; checked again when the window changes size.
    const resize = () => {
      if (!root.isConnected) return window.removeEventListener("resize", resize);
      if (count() !== s.count) build(null);
    };
    window.addEventListener("resize", resize);
  }
  s.data = data;
  s.send = setStateValue;
  s.low = parse(data.min);
  s.high = parse(data.max);
  s.today = parse(data.today);
  // Take what Python holds, unless a pick is half made.
  const held = `${data.start}|${data.end}`;
  if (held !== s.sent) {
    s.sent = held;
    if (s.pending === null) {
      s.start = parse(data.start);
      s.end = parse(data.end);
      s.view = null;
    }
  }
  if (s.view === null) s.view = monthOf(s.start);
  build(s.built === null ? "enter" : null);

  function count() {
    const width = root.getBoundingClientRect().width || 900;
    return width >= 860 ? 3 : width >= 540 ? 2 : 1;
  }

  function build(motion) {
    s.count = count();
    s.view = Math.max(monthOf(s.low), Math.min(s.view, monthOf(s.high) - s.count + 1));
    const months = root.querySelector(".months");
    months.style.setProperty("--count", s.count);
    let markup = "";
    for (let m = s.view; m < s.view + s.count; m++) {
      const year = Math.floor(m / 12), month = m % 12;
      const first = Date.UTC(year, month, 1);
      const length = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
      const offset = (new Date(first).getUTCDay() + 6) % 7;
      let cells = "<span></span>".repeat(offset);
      for (let d = 1; d <= length; d++) {
        const t = first + (d - 1) * DAY;
        const weekday = (offset + d - 1) % 7;
        const kinds = ["day"];
        if (weekday >= 5) kinds.push("weekend");
        if (weekday === 0 || d === 1) kinds.push("rs");
        if (weekday === 6 || d === length) kinds.push("re");
        if (t === s.today) kinds.push("today");
        const off = t < s.low || t > s.high;
        cells += `<button class="${kinds.join(" ")}" data-t="${t}" ${off ? "disabled" : ""}
          aria-label="${d} ${MONTHS[month]} ${year}"><span>${d}</span></button>`;
      }
      markup += `<div class="month"><div class="month-name">${MONTHS[month]}<small>${year}</small></div>
        <div class="dow">${["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((n) => `<span>${n}</span>`).join("")}</div>
        <div class="days">${cells}</div></div>`;
    }
    months.innerHTML = markup;
    months.classList.remove("enter", "turn");
    if (motion) {
      void months.offsetWidth;
      if (motion === "enter") months.classList.add("enter");
      else { months.classList.add("turn"); months.style.setProperty("--from", motion); }
    }
    s.built = s.view;
    root.querySelector(".prev").disabled = s.view <= monthOf(s.low);
    root.querySelector(".next").disabled = s.view + s.count - 1 >= monthOf(s.high);
    paint();
  }

  function paint() {
    const picking = s.pending !== null;
    const lo = picking ? Math.min(s.pending, s.hover) : s.start;
    const hi = picking ? Math.max(s.pending, s.hover) : s.end;
    const days = span(lo, hi);
    const far = picking && days > s.data.longest;
    root.classList.toggle("picking", picking);
    for (const day of root.querySelectorAll(".day")) {
      const t = Number(day.dataset.t);
      const inside = t >= lo && t <= hi;
      day.classList.toggle("in", inside);
      day.classList.toggle("lo", t === lo);
      day.classList.toggle("hi", t === hi);
      day.classList.toggle("far", far && inside);
    }
    const summary = root.querySelector(".cal-summary");
    summary.classList.toggle("far", far);
    summary.querySelector("b").textContent = lo === hi ? said(lo) : `${said(lo)} → ${said(hi)}`;
    summary.querySelector("span").textContent = far
      ? `Up to ${s.data.longest} days: pick a nearer last day`
      : picking && lo === hi ? "Now click your last day"
      : `${days} day${days === 1 ? "" : "s"}`;
  }
}
"""

calendar_picker = st.components.v2.component(
    "date_range_calendar", html='<div class="cal"></div>', css=CALENDAR_CSS, js=CALENDAR_JS,
)


def ask_dates(key, default, longest=365):
    """A range of days on the calendar, at most `longest` long, kept under
    `key` as (first, last). The calendar's own state only lives while it is
    drawn, so it starts from the kept range each time it comes back."""
    held = kept(key, default)
    first, last = held[0], held[-1]
    today = date.today()
    result = calendar_picker(
        key=f"{key}_calendar",
        data={
            "start": first.isoformat(), "end": last.isoformat(), "longest": longest,
            "min": date(today.year, 1, 1).isoformat(),
            "max": date(today.year + 1, 12, 31).isoformat(), "today": today.isoformat(),
        },
        on_range_change=lambda: None,
    )
    picked = (result or {}).get("range")
    if picked:
        first, last = date.fromisoformat(picked["start"]), date.fromisoformat(picked["end"])
    st.session_state[f"kept_{key}"] = (first, last)
    return first, last


def wind_answer(activity):
    return wind_words(kept(f"wind_{activity}", ACTIVITIES[activity]["groove"]))


def hours_answer():
    start, end = kept("hours", DEFAULT_HOURS)
    when = "all day" if (start, end) == (0, 23) else f"{start:02d}:00–{end:02d}:59"
    return f"{when}, {kept('lasting', DEFAULT_LASTING)} h in a row"


def wind_hint(activity):
    low, high = ACTIVITIES[activity]["groove"]
    return f"{activity} counts {low}–{high} kt as worth going out." if can_score(activity) else ""


HOURS_HINT = "A day counts when your wind holds this long without a break, inside these hours."


def ask_in_turn(name, title, then, questions, ask, answer, hint):
    """Draw `questions` one at a time and stop the run until all are in.
    `ask(question)` draws its widgets and returns False while the answer
    will not do; `answer(question)` says a kept one in a few words."""
    asked = len(questions)
    step = st.session_state.setdefault(f"{name}_step", 0)
    if step >= asked:
        return

    with st.container(key=f"{name}_page"):
        st.html(
            f'<div class="view-title reveal">{reveal(title)}</div>'
            f'<div class="view-sub">{COUNT_WORDS[asked]} questions, then {then}.</div>'
        )
        for question, (label, prompt) in enumerate(questions):
            if question < step:
                with st.container(horizontal=True, vertical_alignment="center",
                                  key=f"{name}_asked_{question}"):
                    st.html(
                        f'<div class="answered"><b>✓</b><span>{label}</span>'
                        f"<strong>{html.escape(answer(question))}</strong></div>"
                    )
                    if st.button("Change", key=f"{name}_change_{question}", type="tertiary"):
                        st.session_state[f"{name}_step"] = question
                        st.rerun()
            elif question == step:
                with st.container(key="asking"):
                    st.html(f'<div class="asking"><b>{question + 1}</b>{prompt}</div>')
                    usable = ask(question) is not False
                    if hint(question):
                        st.caption(hint(question))
                    if st.button("Show me" if question == asked - 1 else "Next",
                                 type="primary", icon=":material/arrow_forward:",
                                 key=f"{name}_next", disabled=not usable):
                        st.session_state[f"{name}_step"] = question + 1
                        st.rerun()
            else:
                st.html(f'<div class="upcoming"><b>{question + 1}</b>{prompt}</div>')
    st.stop()


def side_dates(key, default, longest):
    """The range ask_dates keeps under `key`, as a date field for the
    sidebar, where the calendar has no room. A range half picked, or longer
    than `longest` days, leaves the kept one as it was."""
    today = date.today()
    picked = st.sidebar.date_input(
        "Dates", value=kept(key, default), format="DD.MM.YYYY", key=f"{key}_field",
        min_value=date(today.year, 1, 1), max_value=date(today.year + 1, 12, 31),
        help="Only the day and month count: every year on record is checked on these dates.",
    )
    if len(picked) == 2 and (picked[1] - picked[0]).days < longest:
        st.session_state[f"kept_{key}"] = tuple(picked)
    elif len(picked) == 2:
        st.sidebar.warning(f"Up to {longest} days: pick a nearer last day.")


def settings(mode):
    """The sidebar inside a view: what you sail, then everything else the
    view counts. Its widgets share their keys with the questions, so an
    answer and its setting are one value. Returns what you sail."""
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
    if mode == "window":
        side_dates("trip_dates", coming_dates(*TRIP_DAYS), YEAR_ROWS_UP_TO)
    remembered(side.segmented_control, "Years", list(LOOKBACK), required=True,
               key="years", default=DEFAULT_YEARS, start="default")
    return activity


# --- Shared by explore and the trip planner ------------------------------

POINT_WORDS = {"N": "north", "E": "east", "S": "south", "W": "west"}

# The styles of explore and the trip planner, sent with either view.
ANSWER_CSS = """
<style>
.st-key-trip_page { max-width: 760px; }
/* Room for the calendar: three months side by side when it is open. */
.st-key-trip_page:has([class*="_calendar"]) { max-width: 1180px; }
.st-key-trip_result { max-width: 1000px; }
.answer-eyebrow {
    font-size: .7rem; font-weight: 700; letter-spacing: .16em; text-transform: uppercase;
    color: #fb8500;
}

/* The one number, counting up to itself. "\\3c" is "<": see --tone. */
@property --num { syntax: "\\3c integer>"; inherits: false; initial-value: 0; }
@keyframes countUp { from { --num: 0; } to { --num: var(--to); } }
.answer-head { margin: 1.1rem 0 1.3rem; animation: riseIn .5s ease both; }
.answer-big { display: flex; align-items: baseline; gap: .2rem .8rem; flex-wrap: wrap; margin-top: .4rem; }
.answer-num {
    font-size: clamp(3.4rem, 8vw, 5.6rem); font-weight: 800; line-height: .95;
    letter-spacing: -.05em; color: #fb8500; font-variant-numeric: tabular-nums;
}
.answer-num.count { counter-reset: num var(--num); animation: countUp 1.6s cubic-bezier(.16, .8, .3, 1) .15s both; }
.answer-num.count::after { content: counter(num); }
.answer-unit { font-size: clamp(1.3rem, 2.6vw, 1.9rem); font-weight: 700; letter-spacing: -.02em; color: #e8eef5; }
.answer-sub { max-width: 64ch; margin-top: .6rem; font-size: .98rem; line-height: 1.5; color: #a9b6c3; }
.answer-sub b { color: #e8eef5; }

.chapter-lead {
    margin-top: 1.2rem; font-size: clamp(1.45rem, 2.8vw, 2rem); font-weight: 800;
    line-height: 1.15; letter-spacing: -.03em; color: #e8eef5; animation: riseIn .45s ease both;
}
.chapter-sub {
    max-width: 66ch; margin: .45rem 0 1.3rem; font-size: .95rem; line-height: 1.55;
    color: #a9b6c3; animation: riseIn .45s ease .08s both;
}
.chapter-sub b { color: #e8eef5; }
/* Orange and blue stepped to sit in the dark-mode lightness band and stay
   apart under colour blindness (checked in OKLab on the background). */
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; margin-top: 1rem; font-size: .8rem; color: #8b98a5; }
.legend span::before {
    content: ""; display: inline-block; width: .6rem; height: .6rem; margin-right: .45rem;
    border-radius: 50%; background: var(--c);
}
@keyframes dotOn { from { background: #17212b; transform: scale(.3); } }

/* Days as dots: a row a month, or a row a year. They light up in order. */
/* Dots stay day-sized however few there are, and shrink only to fit. */
.dots { display: grid; gap: 5px; max-width: 760px; }
.dot-row {
    display: grid; grid-template-columns: 2.6rem minmax(0, max-content) 2rem;
    justify-content: start; align-items: center; gap: .7rem;
    animation: riseIn .4s ease both; animation-delay: calc(var(--r) * 30ms);
}
.dot-label { font-size: .78rem; font-weight: 600; color: #8b98a5; font-variant-numeric: tabular-nums; }
.dot-row.marked .dot-label, .dot-row.marked .dot-count { color: #fb8500; }
.dot-cells { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 16px)); gap: 3px; }
.dot-cells i { aspect-ratio: 1; border-radius: 50%; background: #17212b; }
/* --lag holds the dots back until the block they sit in has arrived. */
.dot-cells i.g {
    animation: dotOn .4s ease both; animation-delay: calc(var(--k) + var(--lag, .45s));
}
.dot-cells i.g { background: #e37100; }
.dot-count { font-size: .8rem; font-weight: 700; text-align: right; color: #e8eef5; font-variant-numeric: tabular-nums; }

.rose-drawing { display: block; width: 100%; max-width: 330px; margin: 0 auto; }

/* Your spots side by side. */
.spots { display: grid; gap: .55rem; max-width: 820px; }
.spot-row {
    display: grid; grid-template-columns: minmax(0, 13rem) minmax(0, 1fr) 8.5rem;
    align-items: center; gap: .9rem; font-size: .88rem;
    animation: riseIn .4s ease both; animation-delay: calc(var(--r) * 45ms);
}
.spot-name { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: #c5d0da; }
.spot-row.here .spot-name { font-weight: 700; color: #fb8500; }
.spot-bar { height: 10px; border-radius: 5px; background: #141c25; overflow: hidden; }
.spot-bar i {
    display: block; width: var(--v); height: 100%; border-radius: 5px; background: #5b6b7a;
    transform-origin: left; animation: bandGrow .7s cubic-bezier(.2, .7, .3, 1) both;
    animation-delay: calc(var(--r) * 45ms + .2s);
}
.spot-row.here .spot-bar i { background: #e37100; }
.spot-num { text-align: right; font-weight: 600; color: #e8eef5; font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
    .spot-row { grid-template-columns: minmax(0, 1fr) auto; gap: .3rem .8rem; }
    .spot-bar { grid-column: 1 / -1; }
}

/* The trip planner: blocks arrive one after another, then the others. */
.trip-title {
    margin-top: .45rem; font-size: clamp(2.1rem, 4.6vw, 3.3rem); font-weight: 800;
    line-height: 1.05; letter-spacing: -.04em;
}
.trip-block { margin-top: 1.5rem; animation: riseIn .55s ease both; }
.trip-rose .rose-drawing { max-width: 250px; }
.band {
    display: flex; gap: 2px; max-width: 620px; height: 14px; margin-top: 1rem;
    border-radius: 7px; overflow: hidden; background: #141c25;
}
.band i {
    display: block; height: 100%; transform-origin: left;
    animation: bandGrow .55s cubic-bezier(.2, .7, .3, 1) both;
    animation-delay: calc(var(--lag, 0s) + var(--d) * .3s);
}
.legend b { color: #e8eef5; }
.st-key-trip_compare_pick { max-width: 620px; animation: riseIn .5s ease 2.6s both; }
.cmp-verdict { margin: .4rem 0 .2rem; font-size: .98rem; color: #a9b6c3; animation: riseIn .4s ease both; }
.cmp-verdict b { color: #fb8500; }
.cmp-card {
    box-sizing: border-box; height: 100%; padding: 1rem 1.1rem 1.1rem;
    border: 1px solid #26333f; border-radius: 8px; background: #0d1319;
    animation: riseIn .5s cubic-bezier(.2, .7, .3, 1) both; animation-delay: calc(var(--r) * 90ms);
}
.cmp-card.top {
    border-color: rgba(251, 133, 0, .6);
    background: linear-gradient(180deg, rgba(251, 133, 0, .08), transparent 60%), #0d1319;
}
.cmp-tags { display: flex; gap: .6rem; min-height: 1rem; }
.cmp-tags span {
    font-size: .62rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
    color: #8b98a5;
}
.cmp-card.top .cmp-tags span:last-child { color: #fb8500; }
.cmp-name {
    margin-top: .3rem; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
    font-size: 1.05rem; font-weight: 700; color: #e8eef5;
}
.cmp-big { margin-top: .45rem; font-size: .9rem; color: #a9b6c3; }
.cmp-big b {
    margin-right: .15rem; font-size: 2.2rem; font-weight: 800; letter-spacing: -.04em;
    color: #e8eef5; font-variant-numeric: tabular-nums;
}
.cmp-card.top .cmp-big b { color: #fb8500; }
.cmp-bar { height: 6px; margin: .5rem 0 .85rem; border-radius: 3px; background: #141c25; overflow: hidden; }
.cmp-bar i {
    display: block; width: var(--v); height: 100%; border-radius: 3px; background: #e37100;
    transform-origin: left; animation: bandGrow .7s cubic-bezier(.2, .7, .3, 1) both;
    animation-delay: calc(var(--r) * 90ms + .25s);
}
.cmp-facts { display: grid; gap: .3rem; font-size: .84rem; color: #a9b6c3; }
.cmp-facts b { color: #e8eef5; }

@media (prefers-reduced-motion: reduce) {
    .dot-cells i, .spot-bar i, .band i, .cmp-bar i { animation: none !important; }
    .answer-num.count { animation: none; counter-reset: num var(--to); }
}
</style>
"""


def compass_words(point):
    """ENE as said aloud: east-northeast."""
    words = [POINT_WORDS[letter] for letter in point]
    return f"{words[0]}-{''.join(words[1:])}" if len(words) == 3 else "".join(words)


def joined(words):
    """May; May and July; May, June and July."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def years_named(years, total):
    """2019 and 2021, or past three of them, how many of the `total`."""
    if len(years) > 3:
        return f"{len(years)} of the {total} years"
    return joined([str(year) for year in sorted(years)])


def ordinal(number):
    suffix = "th" if 11 <= number % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def year_rows(days, dates):
    """Your dates in every year that has all of them, newest first: per day,
    'g' when it brought your wind, '' when not."""
    cells = days[~((days.index.month == 2) & (days.index.day == 29))]
    length = len(window_days(dates))
    rows = []
    for year, group in cells.groupby(window_year(cells.index, dates)):
        if len(group) >= length:
            rows.append((int(year), list(np.where(group["good"], "g", ""))))
    return rows[::-1]


def dot_rows(rows):
    """Days as dots that light up in reading order. Each row: its label, the
    kind of each day ('g' or ''), its count, a hover note, and whether
    to mark it out."""
    lit = sum(bool(kind) for _, kinds, *_ in rows for kind in kinds)
    pace = min(4.0, 1400 / max(lit, 1))   # ms between dots, so it never drags
    widest = max(len(kinds) for _, kinds, *_ in rows)
    order, drawn = 0, []
    for index, (label, kinds, count, note, marked) in enumerate(rows):
        cells = []
        for kind in kinds:
            if kind:
                cells.append(f'<i class="{kind}" style="--k:{order * pace:.0f}ms"></i>')
                order += 1
            else:
                cells.append("<i></i>")
        drawn.append(
            f'<div class="dot-row{" marked" if marked else ""}" style="--r:{index}" '
            f'title="{html.escape(note)}"><span class="dot-label">{label}</span>'
            f'<span class="dot-cells">{"".join(cells)}</span>'
            f'<span class="dot-count">{count}</span></div>'
        )
    return f'<div class="dots" style="--cols:{widest}">{"".join(drawn)}</div>'


def water_hours(hours):
    start, end = hours
    return [(start + step) % 24 for step in range((end - start) % 24 + 1)]


def best_hours(pct, hours, length=3):
    """The `length` hours in a row on the water that most often have it."""
    span = water_hours(hours)
    length = min(length, len(span))
    runs = [span[i:i + length] for i in range(len(span) - length + 1)]
    return max(runs, key=lambda run: pct.reindex(run).fillna(0).mean())


def rose_drawing(degrees):
    """Where the wind comes from as sixteen petals, each as long as its share
    of the hours, growing out from the middle."""
    sectors = np.floor(((np.asarray(degrees) + 11.25) % 360) / 22.5).astype(int)
    share = np.bincount(sectors, minlength=16) / len(sectors)
    top = share.max()

    def at(radius, angle):
        return f"{100 + radius * np.sin(angle):.1f} {100 - radius * np.cos(angle):.1f}"

    petals = []
    for sector, part in enumerate(share):
        if part == 0:
            continue
        outer = 20 + 72 * part / top
        first, last = np.radians(sector * 22.5 - 9.5), np.radians(sector * 22.5 + 9.5)
        colour = "#ffb703" if part == top else blend("#4d2c0c", "#e37100", part / top)
        petals.append(
            f'<path d="M{at(16, first)} L{at(outer, first)} A{outer:.1f} {outer:.1f} 0 0 1 '
            f'{at(outer, last)} L{at(16, last)} A16 16 0 0 0 {at(16, first)} Z" '
            f'fill="{colour}" style="animation-delay:{.15 + sector * .04:.2f}s"/>'
        )
    rings = "".join(
        f'<circle cx="100" cy="100" r="{radius}" fill="none" stroke="#26333f"/>'
        for radius in (16, 56, 92)
    )
    letters = "".join(
        f'<text x="{x}" y="{y}" fill="#8b98a5" font-size="11" font-weight="700" '
        f'font-family="system-ui, sans-serif" text-anchor="middle" '
        f'dominant-baseline="central">{letter}</text>'
        for letter, x, y in (("N", 100, -4), ("E", 204, 100), ("S", 100, 204), ("W", -4, 100))
    )
    motion = (
        "<style>path{transform-origin:100px 100px;"
        "animation:grow .8s cubic-bezier(.2,.7,.3,1) both}"
        "@keyframes grow{from{transform:scale(.15);opacity:0}}"
        "@media (prefers-reduced-motion:reduce){path{animation:none}}</style>"
    )
    return svg_image(motion + rings + "".join(petals) + letters, "-16 -16 232 232", "rose-drawing")


# --- Explore: the spot at a glance -----------------------------------------
# What the wind does here all year on one screen: four figures, the windy
# days month by month, and the wind rose of the month you click beside the
# map. Everything else waits in the sidebar under "Show more", a switch a
# view, so the page starts simple and grows only as far as you ask.

EXTRAS = {
    "day": "Through the day",
    "years": "Year by year",
    "air": "Air temperature",
    "spots": "Your other spots",
    "numbers": "The numbers",
}
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


def day_chart(share, hours):
    """Each hour of the day as nested areas, one per speed band, with your
    hours on the water shaded."""
    start, end = hours
    spans = [(start, end + 1)] if start <= end else [(start, 24), (0, end + 1)]
    x = alt.X("hour:Q", scale=alt.Scale(domain=[0, 24]), title=None,
              axis=alt.Axis(values=list(range(0, 25, 3)), labelExpr="datum.value + 'h'",
                            ticks=False, domain=False, grid=False))
    layers = [
        alt.Chart(pd.DataFrame(spans, columns=["hour", "until"]))
        .mark_rect(color="#8ecae6", opacity=0.08).encode(x=x, x2="until:Q")
    ]
    for threshold, colour in zip(share.columns, THRESHOLD_COLORS):
        rows = pd.DataFrame({
            "hour": np.arange(24) + 0.5,
            "pct": share[threshold].reindex(range(24)).fillna(0).to_numpy(),
        })
        layers.append(
            alt.Chart(rows).mark_area(color=colour, opacity=0.9, interpolate="monotone")
            .encode(x=x, y=alt.Y("pct:Q", title="% of hours", scale=alt.Scale(domain=[0, 100])))
        )
    return alt.layer(*layers).properties(height=280)


def year_chart(days):
    """A square per month of each year: the brighter, the more of its days
    brought your wind."""
    grid = (
        days.groupby([days.index.year.rename("year"), days.index.month.rename("month")]).mean()
        .mul(100).rename("pct").reset_index()
    )
    grid["name"] = [MONTHS[month - 1] for month in grid["month"]]
    return alt.Chart(grid).mark_rect(cornerRadius=2, stroke="#0a0e13", strokeWidth=1.5).encode(
        x=alt.X("name:N", sort=MONTHS, title=None,
                axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False)),
        y=alt.Y("year:O", sort="descending", title=None, axis=alt.Axis(ticks=False, domain=False)),
        color=alt.Color(
            "pct:Q", title="% of days", scale=alt.Scale(
                domain=[0, 100], range=["#141c25", "#7a3d05", "#fb8500", "#ffb703"]),
            legend=alt.Legend(orient="bottom", gradientLength=240),
        ),
        tooltip=[alt.Tooltip("year:O"), alt.Tooltip("name:N", title="month"),
                 alt.Tooltip("pct:Q", title="% of days with your wind", format=".0f")],
    ).properties(height=alt.Step(22))


def air_chart(by_month):
    """The air month by month, each point carrying its degrees."""
    rows = pd.DataFrame({
        "name": MONTHS, "celsius": by_month.reindex(range(1, 13)).to_numpy(),
    }).dropna()
    rows["wear"] = [kit_for(celsius) for celsius in rows["celsius"]]
    line = alt.Chart(rows).encode(
        x=alt.X("name:N", sort=MONTHS, title=None,
                axis=alt.Axis(labelAngle=0, ticks=False, domain=False)),
        y=alt.Y("celsius:Q", title="°C", scale=alt.Scale(zero=False)),
    )
    return alt.layer(
        line.mark_line(color="#fb8500", strokeWidth=2.5, interpolate="monotone"),
        line.mark_circle(color="#fb8500", size=80, opacity=1).encode(
            tooltip=[alt.Tooltip("name:N", title="month"),
                     alt.Tooltip("celsius:Q", title="°C", format=".0f"),
                     alt.Tooltip("wear:N", title="what to wear")]),
        line.mark_text(dy=-15, color="#e8eef5", fontSize=12)
        .encode(text=alt.Text("celsius:Q", format=".0f")),
    ).properties(height=260)


if st.session_state["mode"] == "explore":
    st.html(ANSWER_CSS)
    activity = settings("explore")
    st.sidebar.subheader("Show more")
    shown = {
        key for key, label in EXTRAS.items()
        if (key != "air" or HAS_TEMP)
        and remembered(st.sidebar.toggle, label, key=f"show_{key}", default=False)
    }
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
        f"data: {SOURCE_CREDIT} · more under Show more, in the settings on the left"
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

    if "day" in shown:
        st.divider()
        hourly = knots if month is None else knots[recent.index.month == month]
        section(f"{when} through the day",
                "the share of hours that top each speed; shaded, your hours on the water")
        st.html(band_legend(ladder))
        st.altair_chart(day_chart(over_bands(hourly, hourly.index.hour, ladder), hours),
                        width="stretch")

    if "years" in shown and len(days):
        st.divider()
        section("Year by year", f"the share of days with {wind_words(wind)}, month by month")
        st.altair_chart(year_chart(days), width="stretch")

    if "air" in shown:
        st.divider()
        air = recent.loc[water, "temp_c"]
        by_month = air.groupby(air.index.month).mean()
        section("Air temperature", "on average in your hours on the water: air, not water")
        st.altair_chart(air_chart(by_month), width="stretch")
        warm, cool = int(by_month.idxmax()), int(by_month.idxmin())
        with xr.open_dataset(dataset_path) as ds:
            lender = ds.attrs.get("air_from")
        st.caption(
            f"Warmest in {calendar.month_name[warm]}, about {by_month[warm]:.0f} °C: "
            f"{kit_for(by_month[warm])}. Coolest in {calendar.month_name[cool]}, about "
            f"{by_month[cool]:.0f} °C: {kit_for(by_month[cool])}."
            + (f" This station measures no air temperature: it comes from {lender}, the "
               "nearest one that does, corrected for the difference in height." if lender else "")
        )

    if "spots" in shown:
        st.divider()
        spots = downloaded_locations()
        names = spot_names(spots)
        rows = []
        with st.spinner("Counting days at your other spots..."):
            for key, entry in spots.items():
                path = entry["path"]
                found = good_days(path, path.stat().st_mtime, entry["zone"], years_back,
                                  wind, lasting, hours)
                rows.append((float(found.mean() * 100) if len(found) else 0.0,
                             names[key], path == dataset_path))
        rows.sort(reverse=True)
        rank = next((place for place, row in enumerate(rows, 1) if row[2]), None)
        section(
            "The best of your spots" if rank == 1
            else f"{ordinal(rank)} of your {len(rows)} spots" if rank
            else f"Your {len(rows)} spots side by side",
            "the same wind and hours at every spot you have downloaded, each in its own local time",
        )
        st.html('<div class="spots">' + "".join(
            f'<div class="spot-row{" here" if here_spot else ""}" style="--r:{index}">'
            f'<span class="spot-name" title="{html.escape(name)}">{html.escape(name)}</span>'
            f'<span class="spot-bar"><i style="--v:{pct:.1f}%"></i></span>'
            f'<span class="spot-num">{pct / 100 * 365:.0f} days a year</span></div>'
            for index, (pct, name, here_spot) in enumerate(rows)
        ) + "</div>")

    if "numbers" in shown:
        st.divider()
        section("The numbers", "month by month, over your hours on the water")
        table = pd.DataFrame({
            "Month": MONTHS,
            "Days with your wind": [
                per_month.get(number, 0) * calendar.monthrange(2001, number)[1]
                for number in range(1, 13)
            ],
            **{f"% over {threshold} kt": month_share[threshold].reindex(range(1, 13)).to_numpy()
               for threshold in ladder},
        })
        # Tall enough for all twelve months without scrolling.
        st.dataframe(table.round(0), hide_index=True, width="stretch", height=460)
        st.download_button(
            "Every day as CSV",
            days.rename("with_your_wind").rename_axis("date").to_csv().encode(),
            file_name=f"days-{STATION}.csv" if STATION
            else f"days-{LATITUDE:+.2f}_{LONGITUDE:+.2f}.csv",
            mime="text/csv", type="tertiary", icon=":material/download:",
        )
    st.stop()


# --- Plan a trip: three questions, then what to expect ---------------------
# Your spot on set dates, a regatta or a week away: when, the wind you need
# and your hours on the water, one at a time. Where is the spot you picked.
# Then what those dates have brought in the years on record, built up a
# piece at a time, and the same dates at up to three of your other spots,
# should this one disappoint.

TRIP_QUESTIONS = [
    ("When", "When are you there?"),
    ("Wind", "How much wind do you need?"),
    ("On the water", "When are you on the water?"),
]
COMPARE_UP_TO = 3
# Your hours on the water as too light, right and too strong.
BANDS = {
    "light": ("Too light", "#3a4a5a"),
    "good": ("In your range", "#e37100"),
    "strong": ("Too strong", "#9d0208"),
}


@st.cache_data(show_spinner=False, max_entries=128)
def trip_outlook(path, mtime, zone, years_back, wind, lasting, hours, dates):
    """What a spot's record says about your dates: per day whether your wind
    held `lasting` hours in a row on the water, and over your hours on the
    water how often it was too light, right or too strong, how hard it blew,
    where it came from when it was right, and how warm the air was."""
    frame = load_wind(path, mtime, zone)
    frame, good = flag_hours(frame, lookback_start(frame, years_back), wind, dates)
    on_water = hour_mask(frame.index, hours)
    knots = frame.loc[on_water, "speed"] * MS_TO_KNOTS
    flags = lasting_days(good[on_water], lasting)
    right = frame[good & on_water]
    air = frame.loc[on_water, "temp_c"].dropna() if "temp_c" in frame else pd.Series(dtype=float)
    return {
        "days": flags.to_frame("good"),
        "light": float((knots < wind[0]).mean() * 100) if len(knots) else 0.0,
        "strong": float((knots > wind[1]).mean() * 100) if len(knots) else 0.0,
        # The middle half of your hours on the water, and its midpoint.
        "knots": np.percentile(knots, [25, 50, 75]).tolist() if len(knots) else None,
        "direction": wind_direction(right["u10"], right["v10"]).to_numpy(),
        "hour": good.groupby(frame.index.hour).mean().reindex(range(24)) * 100,
        "air": float(air.mean()) if len(air) else None,
    }


def typical_days(days, length):
    """Days with your wind in a typical year: whole from five days up, to a
    tenth below, where a whole number would round a trip away."""
    expected = float(days["good"].mean()) * length if len(days) else 0.0
    return round(expected) if length >= 5 else round(expected, 1)


def wind_bands(outlook):
    """Too light, right and too strong as whole percents that add up to 100."""
    light, strong = round(outlook["light"]), round(outlook["strong"])
    return {"light": light, "good": 100 - light - strong, "strong": strong}


def band_bar(bands):
    """Your hours on the water as one bar, too light to too strong."""
    parts = "".join(
        f'<i style="width:{share}%;background:{BANDS[kind][1]};--d:{order}"></i>'
        for order, (kind, share) in enumerate(bands.items()) if share > 0
    )
    legend = "".join(
        f'<span style="--c:{BANDS[kind][1]}">{BANDS[kind][0]} <b>{share}%</b></span>'
        for kind, share in bands.items()
    )
    return f'<div class="band">{parts}</div><div class="legend">{legend}</div>'


def swap_trip(entry, key, where, chosen):
    """Go to a compared spot instead, the whole app with you: the one you
    leave joins the others. A callback, as only one can reach the picker
    before it is drawn."""
    enter_spot({k: v for k, v in entry.items() if k != "path"})
    st.session_state["trip_compare"] = [where, *[other for other in chosen if other != key]]


def compare_card(name, outlook, length, tags, index):
    """One spot on your dates, to set beside the others."""
    days = outlook["days"]
    if not len(days) or outlook["knots"] is None:
        body = '<div class="cmp-facts"><span>No record on these dates.</span></div>'
    else:
        count = typical_days(days, length)
        bands = wind_bands(outlook)
        low, _, high = outlook["knots"]
        facts = [
            f"Usually <b>{low:.0f}–{high:.0f} kt</b>",
            f"<b>{bands['good']}%</b> in range · {bands['light']}% too light",
            f"From the <b>{prevailing_direction(outlook['direction'])}</b> when right"
            if len(outlook["direction"]) else "Never in your range",
            f"<b>{outlook['air']:.0f} °C</b> air" if outlook["air"] is not None
            else "No air temperature",
        ]
        body = (
            f'<div class="cmp-big"><b>{count}</b> of {length} days</div>'
            f'<div class="cmp-bar"><i style="--v:{count / length * 100:.0f}%"></i></div>'
            f'<div class="cmp-facts">{"".join(f"<span>{fact}</span>" for fact in facts)}</div>'
        )
    return (
        f'<div class="cmp-card{" top" if "Most days" in tags else ""}" style="--r:{index}">'
        f'<div class="cmp-tags">{"".join(f"<span>{tag}</span>" for tag in tags)}</div>'
        f'<div class="cmp-name" title="{html.escape(name)}">{html.escape(name)}</div>{body}</div>'
    )


if st.session_state["mode"] == "window":
    st.html(ANSWER_CSS)
    activity = st.session_state["activity"]
    spots = downloaded_locations()
    # The spot you picked, by its key among the others on disk.
    where = next((key for key, entry in spots.items() if entry["path"] == dataset_path), None)
    if where is None:
        where, spots = "here", {"here": {**spot, "path": dataset_path}, **spots}
    entry = spots[where]
    names = spot_names(spots)
    default_dates = coming_dates(*TRIP_DAYS)

    def trip_ask(question):
        if question == 0:
            dates = as_dates(ask_dates("trip_dates", default_dates, YEAR_ROWS_UP_TO))
            # A dot a day and a row a year: two months is as wide as it goes.
            if dates is None or len(window_days(dates)) > YEAR_ROWS_UP_TO:
                st.warning("Pick up to two months: a regatta, a trip or a season.")
                return False
        elif question == 1:
            ask_wind(activity)
        else:
            ask_hours()

    def trip_answer(question):
        if question == 0:
            dates = as_dates(kept("trip_dates", default_dates))
            return dates_label(dates) if dates else "–"
        return wind_answer(activity) if question == 1 else hours_answer()

    hints = [
        "Only the day and month count: every year on record is checked on these dates.",
        wind_hint(activity),
        HOURS_HINT,
    ]
    ask_in_turn("trip", f"Plan a trip to {html.escape(names[where])}",
                "what to expect on your dates", TRIP_QUESTIONS,
                trip_ask, trip_answer, lambda question: hints[question])

    # --- The answer, built up: your days, the wind, the air, then others ---
    activity = settings("window")
    picked = kept("trip_dates", default_dates)
    dates = as_dates(picked)
    length = len(window_days(dates))
    wind = tuple(kept(f"wind_{activity}", ACTIVITIES[activity]["groove"]))
    hours = tuple(kept("hours", DEFAULT_HOURS))
    lasting = kept("lasting", DEFAULT_LASTING)
    years_back = LOOKBACK[kept("years", DEFAULT_YEARS)]
    within = (
        "at any hour" if hours == (0, 23)
        else f"between {hours[0]:02d}:00 and {hours[1]:02d}:59"
    )

    with st.container(key="trip_result"):
        def outlook_at(key):
            path = spots[key]["path"]
            return trip_outlook(path, path.stat().st_mtime, spots[key]["zone"], years_back,
                                wind, lasting, hours, dates)

        with st.spinner("Reading every year on record..."):
            outlook = outlook_at(where)
        days = outlook["days"]
        st.html(
            '<div class="answer-head"><div class="answer-eyebrow">What to expect</div>'
            '<div class="trip-title reveal">'
            + reveal(f"{html.escape(names[where])} · {dates_label(dates)}") + "</div></div>"
        )
        if not len(days) or outlook["knots"] is None:
            st.info(f"{names[where]} has no record on {dates_label(dates)} yet. Pick other "
                    "dates, or another spot.")
            st.stop()

        # Your days: how many bring your wind, and in every year on record.
        typical = typical_days(days, length)
        counted = (
            f'<span class="answer-num count" style="--to:{typical}"></span>'
            if isinstance(typical, int) else f'<span class="answer-num">{typical}</span>'
        )
        years_text = f"{days.index.min().year}–{days.index.max().year}"
        rows = year_rows(days, dates)
        record = ""
        if rows:
            tally = {year: sum(kind == "g" for kind in kinds) for year, kinds in rows}
            most, least = max(tally.values()), min(tally.values())
            tops = [year for year in tally if tally[year] == most]
            lows = [year for year in tally if tally[year] == least]
            record = (
                '<div class="chapter-lead">'
                + (f"{most} of {length} days, every year" if most == least else
                   f"Between {least} and {most} of {length} days a year")
                + '</div><div class="chapter-sub">One row per year, a dot per day, lit when '
                "your wind came."
                + ("" if most == least else
                   f" <b>{most}</b> in {years_named(tops, len(tally))}; the leanest, "
                   f"{years_named(lows, len(tally))}, had <b>{least}</b>.")
                + "</div>"
                + dot_rows([
                    (year, kinds, tally[year], f"{year}: {tally[year]} of {length} days",
                     most != least and year in tops and len(tops) <= 3)
                    for year, kinds in rows
                ])
            )
        st.html(
            '<div class="trip-block" style="--lag:.8s">'
            f'<div class="answer-big">{counted}<span class="answer-unit">of {length} days</span>'
            f'</div><div class="answer-sub">with <b>{wind_words(wind)}</b> for {lasting} h in a '
            f"row {within}, in a typical year: {html.escape(percent(days['good'].mean() * 100))}"
            f" of days, counted over {years_text}.</div>{record}</div>"
        )

        # The wind: how hard, how often right, from where, best when.
        bands = wind_bands(outlook)
        low, middle, high = outlook["knots"]
        best = best_hours(outlook["hour"], hours)
        direction = outlook["direction"]
        text_col, rose_col = st.columns([3, 2], gap="large", vertical_alignment="center")
        text_col.html(
            '<div class="trip-block" style="animation-delay:1.5s;--lag:1.8s">'
            f'<div class="chapter-lead">Usually {low:.0f}–{high:.0f} kt, '
            f"a {beaufort(middle)}</div>"
            f'<div class="chapter-sub">Of your hours on the water, <b>{bands["good"]}%</b> '
            f"have wind in your range, {bands['light']}% are too light and "
            f"{bands['strong']}% too strong"
            + (f". In range, it mostly comes from the <b>"
               f"{compass_words(prevailing_direction(direction))}</b>" if len(direction) else "")
            + f". Best between <b>{best[0]:02d}:00 and {best[-1]:02d}:59</b>.</div>"
            + band_bar(bands) + "</div>"
        )
        if len(direction):
            rose_col.html(
                '<div class="trip-block trip-rose" style="animation-delay:1.7s">'
                + rose_drawing(direction) + "</div>"
            )

        # The air, and what that means you wear.
        if outlook["air"] is not None:
            st.html(
                '<div class="trip-block" style="animation-delay:2.1s">'
                f'<div class="chapter-lead">Around {outlook["air"]:.0f} °C in your hours</div>'
                '<div class="chapter-sub">Air, not water, on average over your dates. What to '
                f"wear: <b>{kit_for(outlook['air'])}</b>.</div></div>"
            )

        # Should it disappoint: the same dates at up to three other spots.
        others = [key for key in spots if key != where]
        if others:
            st.html(
                '<div class="trip-block" style="animation-delay:2.5s">'
                f'<div class="chapter-lead">Not sure about {html.escape(names[where])}?</div>'
                '<div class="chapter-sub">Put it next to up to three of your other spots: the '
                "same dates, wind and hours at each.</div></div>"
            )
            # Set through its own key rather than a default, which the picker
            # ignores once drawn: a swap has to hand it the list it leaves.
            # Only when it must change, as setting it resets what is typed.
            showing = st.session_state.get("trip_compare")
            wanted = [key for key in (kept("trip_compare", []) if showing is None else showing)
                      if key in others]
            if showing != wanted:
                st.session_state["trip_compare"] = wanted
            with st.container(key="trip_compare_pick"):
                chosen = st.multiselect(
                    "Compare with", others, max_selections=COMPARE_UP_TO,
                    format_func=names.get, key="trip_compare",
                    label_visibility="collapsed", placeholder="Pick up to three spots",
                )
            st.session_state["kept_trip_compare"] = chosen
            if chosen:
                with st.spinner("Reading your other spots..."):
                    outlooks = {key: outlook_at(key) for key in [where, *chosen]}
                totals = {
                    key: typical_days(found["days"], length)
                    for key, found in outlooks.items() if len(found["days"])
                }
                top = max(totals.values())
                leaders = [key for key, total in totals.items() if total == top]
                if len(leaders) == len(outlooks):
                    leaders = []
                if leaders and where not in leaders:
                    st.html(
                        '<div class="cmp-verdict">'
                        + joined([f"<b>{html.escape(names[key])}</b>" for key in leaders])
                        + f" {'brings' if len(leaders) == 1 else 'bring'} the most: {top} of "
                        f"{length} days, against {totals.get(where, 0)} at "
                        f"{html.escape(names[where])}.</div>"
                    )
                columns = st.columns(len(outlooks), gap="small")
                for index, (column, (key, found)) in enumerate(zip(columns, outlooks.items())):
                    tags = (["Your pick"] if key == where else []) + (
                        ["Most days"] if key in leaders else [])
                    column.html(compare_card(names[key], found, length, tags, index))
                    if key != where:
                        column.button(
                            "Go here instead", key=f"trip_swap_{key}", type="tertiary",
                            icon=":material/swap_horiz:", on_click=swap_trip,
                            args=(spots[key], key, where, chosen),
                        )

        st.divider()
        st.caption(f"Local time at each spot · data at {names[where]}: {source_credit(entry)}")
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
            missing_temp = "No air temperature in this download."
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
