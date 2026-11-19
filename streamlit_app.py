import base64
import calendar
import html
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import altair as alt
import cdsapi
import folium
import matplotlib.pyplot as plt
from matplotlib.path import Path as Outline
import numpy as np
import pandas as pd
import requests
import streamlit as st
import xarray as xr
from streamlit_folium import st_folium
from timezonefinder import TimezoneFinder

st.set_page_config(
    page_title="Wind Atlas · hourly wind history", layout="wide",
    # Open beside the page on a computer, closed over it on a phone.
    initial_sidebar_state="auto",
)

# Goes up by 0.1 with every commit pushed to GitHub (3.0 -> 3.1).
APP_VERSION = "3.5"

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
    '<stop offset="0" stop-color="#7dd3fc"/><stop offset=".55" stop-color="#fbbf24"/>'
    '<stop offset="1" stop-color="#2dd4bf"/></linearGradient></defs>'
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
        border-bottom: 1px solid rgba(39, 48, 67, .85);
    }
    .app-masthead { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
    .app-masthead .mark-icon { width: 30px; height: 30px; flex: none; }
    .app-masthead .mark {
        font-size: 1.3rem; font-weight: 800; letter-spacing: -.025em; color: #e7ebf3;
    }
    .st-key-nav button {
        border-radius: 999px; min-height: 2.15rem; padding: .2rem .95rem; font-size: .86rem;
    }
    /* Beside the name: Streamlit Cloud's badge covers the bottom corners. */
    .app-version {
        align-self: center; padding: .12rem .45rem; border-radius: 999px;
        border: 1px solid #273043; font-size: .7rem; font-weight: 600;
        letter-spacing: .04em; color: #8a94a8;
    }
    </style>
    """
)

# --- House styles ---------------------------------------------------------
# Up here so every screen gets them, the map screen included.

st.html(
    """
    <style>
    /* Figures: a quiet label over a large, even-width value. */
    [data-testid="stMetricLabel"] p { font-size: .84rem; font-weight: 600; color: #a7b1c4; }
    [data-testid="stMetricValue"] {
        font-size: clamp(1.4rem, 2.1vw, 2rem); font-weight: 700;
        letter-spacing: -.02em; font-variant-numeric: tabular-nums;
    }

    /* The one button that moves you on. */
    [data-testid="stBaseButton-primary"] { border: 0; color: #0a0d14; font-weight: 700; }
    [data-testid="stBaseButton-primary"]:hover { background: #5eead4; color: #0a0d14; }
    [data-testid="stBaseButton-primary"] p { font-weight: 700; }

    .rating-hero { text-align: center; padding: .4rem 0 1rem; }
    .rating-score { font-size: 4.2rem; font-weight: 700; line-height: 1; }

    /* The spot at the top of its screens. */
    .hero { padding: .2rem 0 1.1rem; }
    .hero-title {
        font-size: 2.6rem; font-weight: 700; line-height: 1.1; letter-spacing: -.02em;
        color: #e7ebf3;
    }
    .hero-facts { font-size: .88rem; margin-top: .5rem; color: #8a94a8; }

    /* Landing: the first screen anyone sees. On a wide screen the map fills
       the window edge to edge, wind drifting over all of it, and the rest
       floats on it: the top bar, the title, its four figures and the sources
       top left with no box around them, your spots on the right, the spot
       you clicked along the bottom. A shade over the map's top and left
       keeps the type readable. Below 1100px they stack around the map. */
    .st-key-landing_stage {
        position: relative; overflow: hidden;
        animation: stageIn 1s cubic-bezier(.2, .7, .2, 1) both;
    }
    .st-key-landing_stage iframe { display: block; }
    .st-key-landing_spots, .st-key-picked_panel {
        background: rgba(10, 13, 20, .78); border: 1px solid rgba(231, 235, 243, .09);
        border-radius: 14px; box-shadow: 0 12px 36px rgba(0, 0, 0, .45);
        -webkit-backdrop-filter: blur(12px) saturate(1.2); backdrop-filter: blur(12px) saturate(1.2);
    }
    .st-key-landing_hero { gap: 1.1rem; }
    .st-key-landing_spots { padding: 1rem 1rem .8rem; }
    .st-key-landing_spots .section-label { flex-direction: column; gap: .1rem; }
    /* Under 1100px the panels stack around the map, so the stage is just
       the map's frame and the panels sit outside its rounded edge. */
    @media (max-width: 1099px) {
        .st-key-landing_stage { overflow: visible; }
        .st-key-landing_stage iframe[title="streamlit_folium.st_folium"] { border-radius: 14px; }
        .st-key-landing_hero { padding: .6rem 0 .4rem; }
    }
    /* On a phone the four sources wrap onto two lines rather than scroll. */
    @media (max-width: 640px) {
        .st-key-source_pick [data-testid="stButtonGroup"] > div { flex-wrap: wrap; }
    }
    /* The spot you just clicked, the one thing to act on next: edged in
       teal, its figures four abreast and so smaller. */
    .st-key-picked_panel { padding: 1rem 1.1rem; border-color: #2dd4bf !important; }
    .st-key-picked_panel [data-testid="stMetricValue"] { font-size: 1.35rem; }
    @media (min-width: 1100px) {
        /* The map reaches the window's edges and its top, under Streamlit's
           own header, and the top bar floats on it where it always sits. */
        body:has(.st-key-landing_stage) [data-testid="stHeader"] { background: transparent; }
        body:has(.st-key-landing_stage) [data-testid="stMainBlockContainer"] {
            padding-top: 0; position: relative;
        }
        /* The bar's wrapper floats, so it leaves no gap above the map. */
        body:has(.st-key-landing_stage) [data-testid="stLayoutWrapper"]:has(> .st-key-topbar) {
            position: absolute; z-index: 7; top: 3.8rem; left: 3rem; right: 3rem;
            width: auto !important;
        }
        body:has(.st-key-landing_stage) .st-key-topbar { border-bottom: 0; }
        .st-key-landing_stage {
            gap: 0 !important; height: 100dvh; margin: 0 -3rem;
            flex: none !important; width: calc(100% + 6rem) !important; max-width: none !important;
        }
        .st-key-landing_stage iframe[title="streamlit_folium.st_folium"] {
            height: 100dvh !important; border-radius: 0;
        }
        .st-key-landing_stage::before {
            content: ""; position: absolute; inset: 0; z-index: 4; pointer-events: none;
            background:
                linear-gradient(180deg, rgba(10, 13, 20, .92), rgba(10, 13, 20, 0) 190px),
                linear-gradient(90deg, rgba(10, 13, 20, .9) 0, rgba(10, 13, 20, .72) 34%,
                                rgba(10, 13, 20, 0) 62%);
        }
        .st-key-landing_hero {
            position: absolute; z-index: 5; top: 8.6rem; left: 3rem; width: min(640px, 48%);
            animation: riseIn .8s cubic-bezier(.2, .7, .2, 1) .25s both;
        }
        .st-key-landing_spots {
            position: absolute; z-index: 5; top: 8.6rem; right: 3rem; width: 420px;
            animation: riseIn .8s cubic-bezier(.2, .7, .2, 1) .4s both;
        }
        .st-key-landing_pick {
            position: absolute; z-index: 6; left: 3rem; bottom: 2rem; right: 72px;
            width: auto !important; pointer-events: none;
        }
        .st-key-landing_stage:has(.st-key-landing_spots) .st-key-landing_pick { right: 500px; }
        .st-key-picked_panel {
            pointer-events: auto; animation: riseIn .5s cubic-bezier(.2, .7, .2, 1) both;
        }
    }
    .stage-title {
        font-size: clamp(2.4rem, 4.2vw, 4.4rem); font-weight: 800; line-height: 1;
        letter-spacing: -.04em; color: #e7ebf3;
    }
    .stage-title span { color: #5eead4; }
    .stage-sub {
        font-size: 1.05rem; line-height: 1.55; color: #a7b1c4; margin-top: .9rem;
        max-width: 54ch;
    }
    /* Four figures under a stage's title: on the landing what a click
       brings, from the source picked; on the trip the spot in focus. */
    .fact-boxes {
        display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem;
    }
    @media (max-width: 640px) {
        .fact-boxes { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .fact-boxes div {
        border: 1px solid #273043; border-left: 3px solid #2dd4bf; border-radius: 4px;
        background: rgba(18, 22, 31, .72); padding: .7rem .85rem;
    }
    .fact-boxes div:nth-child(2n) { border-left-color: #7dd3fc; }
    .fact-boxes b {
        display: block; font-size: 1.45rem; font-weight: 800; letter-spacing: -.02em;
        color: #e7ebf3; white-space: nowrap;
    }
    .fact-boxes span {
        display: block; font-size: .68rem; letter-spacing: .1em; text-transform: uppercase;
        line-height: 1.35; color: #8a94a8; margin-top: .15rem;
    }
    /* A window too short for all of it keeps the title smaller. */
    @media (min-width: 1100px) and (max-height: 820px) {
        .stage-title { font-size: clamp(2.2rem, 3.4vw, 3.4rem); }
        .stage-sub { font-size: .98rem; margin-top: .6rem; }
    }
    .landing-hint {
        display: inline-flex; align-items: center; gap: .55rem;
        font-size: .84rem; font-weight: 600; color: #e7ebf3;
    }
    .landing-hint::before {
        content: ""; width: 8px; height: 8px; border-radius: 50%; background: #2dd4bf;
        box-shadow: 0 0 0 0 rgba(45, 212, 191, .6); animation: ping 2.2s ease-out infinite;
    }
    .landing-hint.warn::before { background: #fbbf24; }
    @keyframes ping { 70%, 100% { box-shadow: 0 0 0 9px rgba(45, 212, 191, 0); } }
    @keyframes riseIn { from { opacity: 0; transform: translateY(14px); } }
    @keyframes stageIn { from { opacity: 0; transform: scale(.985); } }
    @media (prefers-reduced-motion: reduce) {
        .st-key-landing_stage, .st-key-landing_hero, .st-key-landing_spots,
        .st-key-picked_panel, .landing-hint::before { animation: none; }
    }
    .section-label {
        display: flex; align-items: baseline; gap: .7rem; margin: 0 0 .2rem;
        font-size: 1.15rem; font-weight: 700; color: #e7ebf3;
    }
    .section-label small { font-size: .82rem; font-weight: 500; color: #8a94a8; }

    /* Saved spots as list rows: hover finds them on the map, Open reopens.
       The list scrolls past this height, so the zoom buttons below the
       floating list stay clear, and its rows keep their own height rather
       than squeeze to fit. */
    .st-key-spot_rows { max-height: 430px; overflow-y: auto; flex-wrap: nowrap; }
    [class*="st-key-spot_card_"] {
        border: 1px solid #273043; border-radius: 4px; flex-shrink: 0 !important;
        background: #10141c; padding: .55rem .7rem .55rem .85rem;
    }
    [class*="st-key-spot_card_"]:hover { border-color: #3a4558; background: #161b27; }
    /* Streamlit pulls text up by the paragraph gap that is taken away here,
       which pushed each row's last line onto its border. */
    [class*="st-key-spot_card_"] p { margin-bottom: 0; line-height: 1.45; }
    [class*="st-key-spot_card_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
    /* Text and Open button share one line; the text wraps instead. */
    [class*="st-key-spot_card_"] { flex-wrap: nowrap !important; }
    [class*="st-key-spot_card_"] > div:first-child { flex: 1 1 auto !important; min-width: 0 !important; }
    [class*="st-key-spot_card_"] > div:not(:first-child) { flex: 0 0 auto !important; }
    /* Where your list lives, said once under it. */
    .link-note { margin: .45rem 0 0; font-size: .82rem; line-height: 1.35; color: #8a94a8; }
    .link-note a { color: #7dd3fc; font-weight: 600; text-decoration: none; }
    .link-note a:hover { color: #2dd4bf; }

    /* Your list at the right of the top bar on every screen, the navigation after it. */
    .st-key-topbar [data-testid="stHorizontalBlock"] > :has(.st-key-list_chip) {
        margin-left: auto; order: 10;
    }
    .st-key-topbar [data-testid="stHorizontalBlock"] > :has(.st-key-nav) { order: 20; }
    .list-chip {
        display: flex; align-items: center; gap: .4rem; white-space: nowrap;
        font-size: .8rem; color: #8a94a8; padding: .32rem .85rem;
        border: 1px solid #273043; border-radius: 999px; background: #10141c;
    }
    .list-chip b { color: #e7ebf3; }
    .list-chip a { color: #7dd3fc; font-weight: 600; text-decoration: none; margin-left: .35rem; }
    .list-chip a:hover { color: #2dd4bf; }
    .st-key-list_chip button { border-radius: 999px; min-height: 2.15rem; font-size: .86rem; }

    /* A record on its way: the spot, what is coming, one bar, nothing to click.
       The screen you came from stays on the page, faded but live, until the
       run that downloads ends; while this screen is up, it is hidden instead. */
    .st-key-loading_screen { max-width: 640px; margin: 4rem auto 0; }
    body:has(.st-key-loading_screen) [data-stale="true"],
    body:has(.st-key-loading_screen)
        :is([data-testid="stVerticalBlockBorderWrapper"], [data-testid="stVerticalBlock"],
            [data-testid="stHorizontalBlock"]):not(:has([data-stale="false"])) {
        display: none !important;
    }
    .loading-title {
        font-size: clamp(1.6rem, 3vw, 2.2rem); font-weight: 700; letter-spacing: -.02em;
        line-height: 1.1; color: #e7ebf3; margin: 0 0 .6rem;
    }
    .loading-note { color: #8a94a8; font-size: .95rem; line-height: 1.5; }
    .loading-bar {
        position: relative; height: 6px; border-radius: 999px; background: #1b2130;
        overflow: hidden; margin: 1.5rem 0 .6rem;
    }
    .loading-bar > i {
        position: absolute; top: 0; bottom: 0; left: 0; width: 0; border-radius: 999px;
        background: #2dd4bf; transition: width .5s ease;
    }
    .loading-bar.unknown > i { width: 38%; animation: loadingSweep 1.5s ease-in-out infinite; }
    @keyframes loadingSweep { from { left: -40%; } to { left: 100%; } }
    .loading-text { font-size: .85rem; color: #8a94a8; }

    /* The small print at the bottom of every screen, clear of the version badge. */
    .small-print {
        margin: 3rem 0 2.5rem; padding: .9rem 4rem 0 0; border-top: 1px solid #273043;
        max-width: 980px; font-size: .74rem; line-height: 1.5; color: #6e7a90;
    }
    .small-print p { margin: 0 0 .45rem; }
    .small-print b { color: #8a94a8; }
    .small-print a { color: #7dd3fc; text-decoration: none; }
    .small-print a:hover { color: #2dd4bf; }

    iframe[title="streamlit_folium.st_folium"] { border-radius: 6px; }

    /* Every setting lives in the sidebar. Closed, its only way back is a
       faded arrow in the corner, so give it a label. */
    [data-testid="stExpandSidebarButton"] {
        display: inline-flex !important; align-items: center; gap: .35rem;
        background: #2dd4bf !important; border-radius: 999px !important;
        padding: .3rem .9rem .3rem .55rem !important;
    }
    [data-testid="stExpandSidebarButton"] span { color: #0a0d14 !important; }
    [data-testid="stExpandSidebarButton"]::after {
        content: "Filter"; color: #0a0d14; font-weight: 700; font-size: .9rem;
    }
    [data-testid="stExpandSidebarButton"]:hover { background: #5eead4 !important; }

    .step-label { font-size: 1.02rem; font-weight: 700; color: #e7ebf3; margin: .6rem 0 .15rem; }
    .st-key-activity_pick button { min-height: 2.6rem; padding: 0 1.05rem; }
    /* All four sports on one line on a phone, wrapping only if that fails. */
    .st-key-activity_pick [data-testid="stButtonGroup"] > div:last-child { flex-wrap: wrap; }
    @media (max-width: 640px) { .st-key-activity_pick button { padding: 0 .6rem; } }

    /* The sport's range on a knot ruler. */
    .groove { margin: .35rem 0 0; padding-top: 1.3rem; }
    .groove-track, .groove-scale { max-width: 440px; }
    .groove-track { position: relative; height: 6px; border-radius: 3px; background: #181d29; }
    .groove-track::before {
        content: ""; position: absolute; inset: -3px 0; border-right: 1px solid #2a3344;
        background: repeating-linear-gradient(90deg, #2a3344 0 1px, transparent 1px 12.5%);
    }
    .groove-band {
        position: absolute; top: -1px; bottom: -1px; border-radius: 4px; background: #2dd4bf;
    }
    .groove em {
        position: absolute; bottom: calc(100% + 7px); transform: translateX(-50%);
        font-style: normal; font-size: .76rem; font-weight: 700; color: #e7ebf3;
        white-space: nowrap;
    }
    .groove-scale { position: relative; height: .9rem; margin-top: .45rem; }
    .groove-scale span {
        position: absolute; transform: translateX(-50%);
        font-size: .66rem; color: #5b6478; font-variant-numeric: tabular-nums;
    }
    .groove-note { font-size: .86rem; color: #8a94a8; margin-top: .55rem; }

    /* The three ways in. Each is drawn as a small instrument that idles cool
       and warms under the pointer, cool to warm like the wind bands. The
       whole card is the button: its label sits along the bottom edge.
       Registered as colours so they fade rather than jump; "\\3c" is "<",
       spelt out because the sanitiser drops a block holding "<" and a letter. */
    @property --tone { syntax: "\\3c color>"; inherits: true; initial-value: #0f766e; }
    @property --glow { syntax: "\\3c color>"; inherits: true; initial-value: #7dd3fc; }
    [data-testid="stLayoutWrapper"]:has(> [class*="st-key-choice_"]) { flex: 1 1 auto; }
    [class*="st-key-choice_"] {
        position: relative; overflow: hidden; padding: 0 0 4.4rem !important;
        background: #0c1017; border: 1px solid #273043 !important; border-radius: 8px !important;
        transition: border-color .3s ease, --tone .35s ease, --glow .35s ease;
    }
    [class*="st-key-choice_"]:hover:not(:has(button:disabled)) {
        --tone: #2dd4bf; --glow: #fbbf24; border-color: #2dd4bf !important;
    }
    .choice-face {
        height: 200px; display: flex; align-items: center; justify-content: center;
        gap: 1.5rem; background: #0a0d14; border-bottom: 1px solid #1a2030;
    }
    .choice-face > * { zoom: 1.35; }
    /* Narrower cards, smaller drawings, so the two weeks of Compare
       always fit. Below 640px the cards stack, one to a row. */
    @media (max-width: 1299px) {
        .choice-face { height: 160px; }
        .choice-face > * { zoom: .95; }
    }
    @media (max-width: 999px) { .choice-face > * { zoom: .6; } }
    @media (max-width: 640px) {
        .choice-face { height: 120px; }
        .choice-face > * { zoom: .85; }
    }
    [class*="st-key-choice_"]:has(button:focus-visible) {
        border-color: #2dd4bf !important; box-shadow: 0 0 0 1px #2dd4bf;
    }
    .choice-text { padding: 1.3rem 1.5rem 0; }
    .choice-title { font-size: 1.4rem; font-weight: 700; color: #e7ebf3; }
    .choice-blurb { font-size: .98rem; line-height: 1.5; color: #a7b1c4; margin-top: .35rem; }
    .choice-lock { font-size: .82rem; font-weight: 600; color: #fbbf24; margin-top: .55rem; }
    [class*="st-key-choice_"] [class*="st-key-pick_"] {
        position: absolute; inset: 0; z-index: 2; width: auto !important;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] .stButton,
    [class*="st-key-choice_"] [class*="st-key-pick_"] button { width: 100%; height: 100%; }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button {
        display: flex; align-items: flex-end; padding: 0 1.5rem 1.1rem;
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
        padding-top: .9rem; border-top: 1px solid #1f2634;
        font-size: 1rem; font-weight: 700; color: #e7ebf3;
    }
    [class*="st-key-choice_"] [class*="st-key-pick_"] button p::after { content: "→"; color: #5b6478; }
    [class*="st-key-choice_"]:hover [class*="st-key-pick_"] button:not(:disabled) p,
    [class*="st-key-choice_"]:hover [class*="st-key-pick_"] button:not(:disabled) p::after {
        color: #2dd4bf;
    }
    [class*="st-key-choice_"] button:disabled { cursor: not-allowed; }
    [class*="st-key-choice_"] button:disabled p { color: #5b6478 !important; }
    [class*="st-key-choice_"]:has(button:disabled) .choice-face { opacity: .35; }
    [class*="st-key-choice_"]:has(button:disabled) .choice-face * { animation-play-state: paused; }

    /* Rate: a meter from 0 to 10 whose needle hunts and never settles.
       Ticks every 22 degrees round a 220 degree arc, 0 bottom left. */
    .choice-face .dial { position: relative; width: 150px; height: 108px; flex: none; }
    .choice-face .dial i {
        position: absolute; left: calc(50% - 1px); bottom: 30px;
        width: 2px; height: 8px; border-radius: 1px; transform-origin: 50% 100%;
        transform: rotate(calc(var(--n) * 22deg - 110deg)) translateY(-62px);
        background: color-mix(in srgb, var(--tone) 60%, #1a2030);
    }
    .choice-face .dial i:nth-of-type(5n+1) { height: 13px; background: var(--glow); }
    .choice-face .dial-needle {
        position: absolute; left: calc(50% - 1.5px); bottom: 30px; width: 3px; height: 54px;
    }
    .choice-face .dial-needle > span {
        display: block; height: 100%; border-radius: 3px; transform-origin: 50% 100%;
        transform: rotate(-110deg);
        background: linear-gradient(to top, var(--tone), var(--glow));
        animation: needleHunt 6.5s ease-in-out infinite;
    }
    .choice-face .dial::after {
        content: ""; position: absolute; left: calc(50% - 6px); bottom: 24px;
        width: 12px; height: 12px; border-radius: 50%; box-sizing: border-box;
        background: #0a0d14; border: 2px solid var(--glow);
    }
    @keyframes needleHunt {
        0%, 100% { transform: rotate(-110deg); }
        26%      { transform: rotate(40deg); }
        38%      { transform: rotate(14deg); }
        60%      { transform: rotate(88deg); }
        78%      { transform: rotate(-30deg); }
    }
    .choice-face .dial-read { display: flex; flex-direction: column; line-height: 1; }
    .choice-face .dial-read b {
        font-size: 2.7rem; font-weight: 800; letter-spacing: -.04em; color: var(--glow);
    }
    .choice-face .dial-read small {
        margin-top: .35rem; font-size: .66rem; font-weight: 600; letter-spacing: .14em;
        text-transform: uppercase; color: #8a94a8;
    }

    /* Explore: a month-by-hour grid with a wash passing over it. */
    .choice-face .grid { display: flex; gap: 3px; }
    .choice-face .grid > span { display: flex; flex-direction: column; align-items: center; gap: 3px; }
    .choice-face .grid i {
        width: 10px; height: 10px; border-radius: 2px; background: #181d29;
        animation: cellWash 4s ease-in-out infinite;
        animation-delay: calc(var(--c) * .12s + var(--r) * .08s - 4s);
    }
    .choice-face small { font-size: .62rem; font-weight: 600; line-height: 1; color: #5b6478; }
    .choice-face .grid small { margin-top: 3px; }
    @keyframes cellWash {
        0%, 46%, 100% { background: #181d29; }
        18%           { background: var(--tone); }
        28%           { background: color-mix(in srgb, var(--tone) 55%, #181d29); }
    }

    /* Compare: two weeks with a frame stepping along them. */
    .choice-face .days { position: relative; display: flex; gap: 4px; }
    .choice-face .day { display: flex; flex-direction: column; align-items: center; gap: 5px; }
    .choice-face .day i { width: 16px; height: 28px; border-radius: 3px; background: #181d29; }
    .choice-face .day:nth-child(7n) i, .choice-face .day:nth-child(7n+6) i { background: #1e2533; }
    .choice-face .days-frame { position: absolute; left: 0; bottom: 0; width: 100%; height: 28px; }
    .choice-face .days-frame > span {
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
    @media (prefers-reduced-motion: reduce) { .choice-face * { animation: none !important; } }

    /* The compare view's question. */
    .view-title { font-size: 1.9rem; font-weight: 700; letter-spacing: -.02em; line-height: 1.15; margin-bottom: .9rem; }
    .st-key-asking {
        border: 1px solid #273043; border-radius: 8px;
        padding: 1.1rem 1.3rem 1.2rem; margin: .35rem 0; background: #0f131b;
    }
    .asking { font-size: 1.2rem; font-weight: 700; color: #e7ebf3; }
    .view-sub { margin: -.5rem 0 1rem; font-size: 1rem; line-height: 1.5; color: #a7b1c4; }
    </style>
    """
)

# One bar across the top of every screen. Inside a spot the way back joins it.
topbar = st.container(horizontal=True, vertical_alignment="center", key="topbar")
topbar.html(
    f'<div class="app-masthead">{LOGO}<span class="mark">Wind Atlas</span>'
    f'<span class="app-version">v{APP_VERSION}</span></div>'
)

MS_TO_KNOTS = 1.9438444924406046

# ERA5 is published on a 0.25 degree grid, so any click resolves to a cell.
GRID = 0.25
# Météo-France's AROME HD model as Open-Meteo archives it: 0.01 degree cells
# over western Europe, every hour since 13 November 2022. The first hours of
# each forecast run stitched together, not a reanalysis.
AROME_GRID = 0.01
AROME_SINCE = date(2022, 11, 13)
# South, north, west, east: where the model has data, found by asking it.
AROME_AREA = (37.5, 55.4, -12.0, 16.0)
OPEN_METEO = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# The sources that are model cells rather than stations: each cell's size,
# what to call it, and where its map opens, centre and zoom.
CELL_GRIDS = {"era5": GRID, "arome": AROME_GRID}
MODEL_NAMES = {"era5": "ERA5", "arome": "AROME HD"}
CELL_VIEWS = {"era5": ((30.0, 5.0), 3), "arome": ((46.5, 2.0), 4)}
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
    "Days with your wind": 0.55,
    "Season length": 0.20,
    "Big days": 0.15,
    "Steadiness": 0.10,
}

# Top of the wind slider in explore, in knots.
WIND_TOP = 50
LOOKBACK = {"10 years": 10, "20 years": 20, "All": None}
# Where explore and the compare view start, before you change anything.
DEFAULT_HOURS = (10, 17)
DEFAULT_LASTING = 3
# How far the wind may swing over those hours, in degrees, or any amount.
SHIFT_STEPS = [*range(10, 181, 10), "Any"]
DEFAULT_SHIFT = "Any"
# How warm it has to feel through those hours, wind chill counted, or any cold.
FEELS_STEPS = ["Any", *range(-10, 31, 2)]
DEFAULT_FEELS = "Any"
DEFAULT_YEARS = "20 years"
THRESHOLD_COLORS = ["#7dd3fc", "#2dd4bf", "#fbbf24", "#e11d48"]

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
# Upper bound in knots -> what a sailor would call it.
BEAUFORT = [(1, "calm"), (4, "light air"), (7, "light breeze"), (11, "gentle breeze"),
            (17, "moderate breeze"), (22, "fresh breeze"), (28, "strong breeze"),
            (34, "near gale"), (41, "gale"), (999, "storm")]

data_dir = Path("data")
data_dir.mkdir(exist_ok=True)
REGISTRY = data_dir / "locations.json"

# Where the wind comes from: a model cell anywhere on earth, a finer one over
# western Europe, or a station that measured it, from MeteoSwiss or an
# airport's METAR reports. All end up in the same file layout.
SOURCES = {
    "era5": ":material/public: ERA5 · worldwide",
    "arome": ":material/grain: AROME · 1.5 km, western Europe",
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
# Where a station map opens before you pick one, and how many stations it
# draws at most before it thins them out.
SWISS_CENTRE = (46.8, 8.2)
STATION_ZOOM = {"meteoswiss": 8, "metar": 5}
NEARBY_STATIONS = 200
# A station this close to the sea or a lake, in km, is by the water. The
# shores are Natural Earth's (public domain): coasts and lakes at 1:10m,
# with the extra lakes it maps for Europe and North America.
SHORE_KM = 20
SHORES = data_dir / "shores.npy"
NATURAL_EARTH = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
SHORE_LAYERS = ["coastline", "lakes", "lakes_europe", "lakes_north_america"]
# The named seas and lakes, to call a spot after the water it lies on.
WATERS = data_dir / "waters.npz"
WATER_LAYERS = {"geography_marine_polys": "sea", "lakes": "lake", "lakes_europe": "lake",
                "lakes_north_america": "lake"}
# A lake's name that does not say it is one gets "Lake" put before it.
LAKE_WORDS = r"(?i)\b(lake|lac|lago|laguna|lagoon|lough|loch|reservoir)\b|(see|sjön|järvi|jezero)$"


def cds_client():
    """Credentials come from Streamlit secrets when deployed, or from
    ~/.cdsapirc when running locally."""
    try:
        credentials = st.secrets["cds"]
        return cdsapi.Client(url=credentials["url"], key=credentials["key"])
    except Exception:
        # No secrets configured — fall back to the local rc file.
        return cdsapi.Client()


def snap_to_grid(value, grid=GRID):
    """A model resolves to cells, so snap the click to the one it lands in."""
    return round(round(value / grid) * grid, 6)


@st.cache_resource(show_spinner=False)
def timezone_finder():
    return TimezoneFinder()


def timezone_at(latitude, longitude):
    """IANA zone for the coordinates, so local hours honour daylight saving."""
    zone = timezone_finder().timezone_at(lat=latitude, lng=longitude)
    return zone or "UTC"


def spot_key(spot):
    """Registry key: the grid cell for a model, the file name for a station.
    An ERA5 cell, the first kind there was, is the bare cell."""
    if spot.get("station"):
        return f"{STATION_FILES[spot['source']]}_{spot['station']}"
    cell = f"{spot['lat']:+.2f}_{spot['lon']:+.2f}"
    return cell if spot.get("source", "era5") == "era5" else f"{spot['source']}_{cell}"


def spot_path(spot):
    """One cached netCDF per grid cell or station."""
    key = spot_key(spot)
    return data_dir / (f"wind_{key}.nc" if spot.get("source", "era5") == "era5" else f"{key}.nc")


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
    table["summit"] = stations["station_exposition_en"].reindex(table.index) == "crest"
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
    # Names come in many hands: BANGOR INTL ARPT, SMITH_FIELD/BEAUFORT_(ASOS).
    names = (
        table["station_name"].str.replace("_", " ")
        .str.replace(r"\((?:ASOS|AWOS|AUT)\)|\bARPT\b|\bAIRPOR\b", " ", regex=True)
        .str.replace(r"\s+", " ", regex=True).str.strip(" /-")
    )
    names = names.where(~names.str.isupper() & ~names.str.islower(), names.str.title())
    for short, word in {"Intl": "International", "Muni": "Municipal", "Rgnl": "Regional"}.items():
        names = names.str.replace(rf"\b{short}\b", word, regex=True)
    table = pd.DataFrame({
        "name": names, "lat": table["lat"], "lon": table["lon"], "height": table["elev"],
        "since": pd.to_datetime(table["begints"]).dt.year,
        # Every METAR carries the air temperature, and no airport is on a summit.
        "has_temp": True, "summit": False,
    })
    return table.sort_values("name")


def station_table(source):
    """The stations of a station source, MeteoSwiss's or the airports'."""
    return meteoswiss_stations() if source == "meteoswiss" else metar_stations()


def station_entry(source, abbr, row):
    """Station `abbr` of `source` as a spot, from its row in station_table()."""
    latitude, longitude = float(row["lat"]), float(row["lon"])
    return {"lat": latitude, "lon": longitude, "source": source, "station": abbr,
            "height": float(row["height"]), "name": str(row["name"]),
            "zone": timezone_at(latitude, longitude)}


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
    """Where a point is, as a sailor would say it. On a lake or a sea, its
    name and the town nearby: 'Lake Balaton near Tapolca', 'Caribbean Sea,
    62 km SE of Les Abymes'. On land, the town: 'Zürich, Switzerland'
    in it, '33 km E of Valencia, Spain' near it. Within 250 km a big town is
    worth going a little further for than a small one (ten times the people
    for each 20 km); past that, the nearest of any size."""
    if not PLACES.exists():
        return coords_label(latitude, longitude)
    towns, lat, lon, size = town_table()
    here, east = np.radians(latitude), np.radians(longitude)
    km = 2 * 6371 * np.arcsin(np.sqrt(
        np.sin((lat - here) / 2) ** 2 + np.cos(here) * np.cos(lat) * np.sin((lon - east) / 2) ** 2
    ))
    near = km < 250
    index = int(np.argmax(np.where(near, size - km / 20, -np.inf)) if near.any() else np.argmin(km))
    town = towns.iloc[index]
    # Which way the point lies from the town, on eight points of the compass.
    bearing = np.degrees(np.arctan2(
        np.sin(east - lon[index]) * np.cos(here),
        np.cos(lat[index]) * np.sin(here)
        - np.sin(lat[index]) * np.cos(here) * np.cos(east - lon[index]),
    ))
    way = f"{km[index]:,.0f} km {COMPASS[::2][round(bearing % 360 / 45) % 8]} of {town['name']}"
    water = water_name(latitude, longitude)
    if water:
        return f"{water} near {town['name']}" if km[index] < 25 else f"{water}, {way}"
    return f"{town['name']}, {town['country']}" if km[index] < 10 else f"{way}, {town['country']}"


# What a spot nobody named is saved as: its coordinates, or where it was
# said to be before names knew the water, '26 km N of Tangier, Morocco'.
UNNAMED = r"[+-]?\d+(\.\d+)?, *[+-]?\d+(\.\d+)?|[\d,]+ km [NESW]{1,2} of [^,]+, [^,]+"


def spot_title(spot):
    """A spot's name, or where it is when nobody named it."""
    name = spot["name"]
    if re.fullmatch(UNNAMED, name.strip()):
        return place_name(float(spot["lat"]), float(spot["lon"]))
    return name


def km_from(latitude, longitude, lats, lons):
    """How far each of many points is from one, in km."""
    # A flat projection around the point is plenty to tell near from far;
    # the longitude wraps so the date line is no wall.
    east = np.radians((lons - longitude + 180) % 360 - 180) * np.cos(np.radians(latitude))
    north = np.radians(lats - latitude)
    return np.hypot(east, north) * 6371


def stations_in_view(stations, bounds):
    """The stations inside a map's view. Past NEARBY_STATIONS of them, one
    per patch of the view, the longest on record first, so a wide view shows
    where they are without a dot on every one."""
    south, west = bounds["_southWest"]["lat"], bounds["_southWest"]["lng"]
    north, east = bounds["_northEast"]["lat"], bounds["_northEast"]["lng"]
    # Past the date line the map counts on to -190 or 200; measured from
    # the view's west edge, every longitude lines up.
    inside = stations[stations["lat"].between(south, north)
                      & ((stations["lon"] - west) % 360 <= east - west)]
    if len(inside) <= NEARBY_STATIONS:
        return inside
    patch = max(north - south, east - west) / 30
    return inside.sort_values("since").groupby(
        [inside["lat"] // patch, inside["lon"] // patch]).head(1)


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


@st.cache_data(ttl="1d", show_spinner="Finding the stations by the water...")
def by_a_shore(stations, km):
    """Which stations lie within `km` of the sea or a lake shore. Each looks
    only at the shore in its own band of latitude. The distance comes in as
    an argument, so the cache starts over when SHORE_KM changes."""
    points = shores()
    reach = km / 111  # in degrees of latitude
    near = []
    for latitude, longitude in zip(stations["lat"], stations["lon"]):
        low, high = np.searchsorted(points[:, 0], [latitude - reach, latitude + reach])
        band = points[low:high]
        near.append(bool((km_from(latitude, longitude, band[:, 0], band[:, 1]) <= km).any()))
    return pd.Series(near, index=stations.index)


def water_label(properties, kind):
    """A sea or lake's name as a sailor says it: oceans without their North
    and South, capitals tamed, and Lake before a lake that does not say so."""
    name = properties.get("name_en") or properties.get("name")
    if properties.get("featurecla") == "ocean":
        return re.sub(r"^(North|South) ", "", name.title())
    name = name.title() if name.isupper() else name
    return f"Lake {name}" if kind == "lake" and not re.search(LAKE_WORDS, name) else name


@st.cache_resource(show_spinner="Naming the world's waters...")
def waters():
    """Every named sea and lake as (name, kind, outline, bounds), outlines in
    longitude and latitude to the nearest hundredth of a degree, bounds as
    west, south, east, north. Built from Natural Earth the first time and
    kept in data/."""
    if not WATERS.exists():
        names, kinds, outlines = [], [], []
        for layer, kind in WATER_LAYERS.items():
            response = requests.get(f"{NATURAL_EARTH}/ne_10m_{layer}.geojson", timeout=120)
            response.raise_for_status()
            for feature in response.json()["features"]:
                properties, geometry = feature["properties"], feature["geometry"]
                if not geometry or not (properties.get("name_en") or properties.get("name")):
                    continue
                polygons = (
                    [geometry["coordinates"]] if geometry["type"] == "Polygon"
                    else geometry["coordinates"]
                )
                for polygon in polygons:
                    names.append(water_label(properties, kind))
                    kinds.append("ocean" if properties.get("featurecla") == "ocean" else kind)
                    outlines.append(np.round(np.asarray(polygon[0])[:, :2] * 100))
        np.savez_compressed(
            WATERS, names=names, kinds=kinds, sizes=[len(outline) for outline in outlines],
            points=np.concatenate(outlines).astype(np.int16),
        )
    stored = np.load(WATERS)
    outlines = np.split(stored["points"] / 100, np.cumsum(stored["sizes"])[:-1])
    return [
        (str(name), str(kind), outline, (*outline.min(axis=0), *outline.max(axis=0)))
        for name, kind, outline in zip(stored["names"], stored["kinds"], outlines)
    ]


def water_name(latitude, longitude):
    """The water a point lies on: the nearest named lake or sea within
    SHORE_KM, or else the ocean around it. None on land away from them."""
    reach = SHORE_KM / 111  # in degrees of latitude
    wide = reach / max(np.cos(np.radians(latitude)), 0.2)
    nearest, ocean = (SHORE_KM, None), None
    for name, kind, outline, (west, south, east, north) in waters():
        if not (south - reach <= latitude <= north + reach and west - wide <= longitude <= east + wide):
            continue
        inside = Outline(outline).contains_point((longitude, latitude))
        if kind == "ocean":
            ocean = ocean or (name if inside else None)
            continue
        km = 0 if inside else km_from(latitude, longitude, outline[:, 1], outline[:, 0]).min()
        if km <= nearest[0]:
            nearest = (km, name)
    return nearest[1] or ocean


def save_record(spot, target, times, speed, bearing, celsius, credit, **details):
    """Hourly readings stored in the layout of an ERA5 download, so everything
    downstream reads both. `speed` is in m/s, `bearing` where the wind comes
    from in degrees, `celsius` the air, if recorded. Any `details` go into
    the file beside the spot's own."""
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

    # The file carries its own details, a station's code and height among
    # them, so it lists properly even without data/locations.json.
    dataset = xr.Dataset(
        data,
        coords={"valid_time": times.to_numpy(dtype="datetime64[ns]")},
        attrs={
            "source": credit, "name": spot["name"],
            "lat": float(spot["lat"]), "lon": float(spot["lon"]),
            **{key: spot[key] for key in ("station", "height") if key in spot}, **details,
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


def fetch_station(spot, target, progress):
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
    return save_record(
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
    return save_record(
        spot, target, frame["time"], frame["sknt"].mask(variable, 0) / MS_TO_KNOTS,
        frame["drct"].fillna(0), frame["tmpc"], "METAR, Iowa Environmental Mesonet",
    )


def in_arome_area(latitude, longitude):
    south, north, west, east = AROME_AREA
    return south <= latitude <= north and west <= longitude <= east


def open_meteo(spot, first, last):
    """Open-Meteo's hourly AROME HD answer for a cell over a span of days,
    asked up to three times: it answers in a second as a rule, and a slow
    spell on its side should cost a retry rather than the download. The
    nearest cell: for a point on the water it would otherwise reach for one
    on land."""
    for attempt in range(3):
        try:
            response = requests.get(OPEN_METEO, params={
                "latitude": spot["lat"], "longitude": spot["lon"],
                "start_date": first.isoformat(), "end_date": last.isoformat(),
                "hourly": "wind_speed_10m,wind_direction_10m,temperature_2m",
                "models": "meteofrance_arome_france_hd", "wind_speed_unit": "ms",
                "timezone": "GMT", "cell_selection": "nearest",
            }, timeout=60)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
            continue
        answer = response.json() if "json" in response.headers.get("content-type", "") else {}
        if answer.get("error"):
            raise RuntimeError(answer.get("reason", "Open-Meteo gave no answer"))
        response.raise_for_status()
        return answer


def fetch_arome(spot, target, progress):
    """Every hour of AROME HD that Open-Meteo holds for a cell, from the first
    day of its archive to yesterday, a year at a time. `progress(done,
    total)` hears about each year as it lands."""
    last = date.today() - timedelta(days=1)
    years = range(AROME_SINCE.year, last.year + 1)
    pieces = []
    for done, year in enumerate(years, 1):
        answer = open_meteo(spot, max(AROME_SINCE, date(year, 1, 1)), min(last, date(year, 12, 31)))
        pieces.append(pd.DataFrame(answer["hourly"]))
        progress(done, len(years))
    frame = pd.concat(pieces, ignore_index=True).dropna(subset=["wind_speed_10m", "wind_direction_10m"])
    if frame.empty:
        raise RuntimeError("Open-Meteo holds no AROME hours for this cell")
    return save_record(
        spot, target, pd.to_datetime(frame["time"]), frame["wind_speed_10m"],
        frame["wind_direction_10m"], frame["temperature_2m"],
        "AROME HD (Météo-France) via Open-Meteo, CC BY 4.0",
        cell_lat=float(answer["latitude"]), cell_lon=float(answer["longitude"]),
    )


def source_credit(spot):
    """Where a spot's wind comes from, for the small print."""
    if spot.get("station"):
        return f"{STATION_NAMES[spot['source']]} station {spot['station']}"
    if spot.get("source") == "arome":
        return "AROME HD via Open-Meteo, forecast runs stitched together, not a reanalysis"
    return "ERA5 reanalysis"


def small_print():
    """The bottom of every screen: what this is and is not for, and the
    credits the sources' licences ask for."""
    st.html(
        '<div class="small-print">'
        "<p><b>Past records, not a forecast.</b> Check the official forecast before you "
        "go out. Use at your own risk.</p>"
        f"<p>Contains modified Copernicus Climate Change Service information {date.today().year}; "
        "neither the European Commission nor ECMWF is responsible for any use made of it. "
        'Data: <a href="https://open-meteo.com/">Open-Meteo.com</a>, MeteoSwiss, GeoNames '
        "(CC BY 4.0), Iowa Environmental Mesonet, Natural Earth.</p>"
        "</div>"
    )


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
    for source, prefix in (("era5", "wind"), ("arome", "arome")):
        for path in sorted(data_dir.glob(f"{prefix}_*.nc")):
            cell = path.stem.removeprefix(f"{prefix}_")
            key = cell if source == "era5" else path.stem
            entry = registry.get(key)
            if entry is None:
                try:
                    latitude, longitude = (float(part) for part in cell.split("_"))
                except ValueError:
                    continue
                entry = {
                    "lat": latitude, "lon": longitude,
                    "name": f"{latitude:+.2f}, {longitude:+.2f}",
                    "zone": timezone_at(latitude, longitude),
                }
            found[key] = {**entry, "source": source, "path": path}
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


# --- Your spots: the ones in your link -----------------------------------
# Everyone who opens the hosted app shares one data folder, so the folder is
# a cache, nobody's list. Which spots are yours lives in the page address:
# ?spot=36.75_-8.25&spot=metar_LEPA. Bookmark it to keep them, send it to
# share them. Nothing is kept on the server, so a restart of the hosted app
# loses the downloads but not your list: opening a spot fetches it again.

DECIMAL = re.compile(r"-?\d+\.\d+")
CELL_KEY = re.compile(r"(?:(arome)_)?([+-]\d+\.\d+)_([+-]\d+\.\d+)")
STATION_KEY = re.compile(r"(station|metar)_(\w+)")

LINK_NOTE = (
    '<div class="link-note">Your spots are saved in this page\'s address: bookmark it or '
    "share it.</div>"
)

# The copy link in the top bar: the address, spots and all, to the clipboard.
COPY_LINK_SCRIPT = """
<script>
(() => {
  const link = document.getElementById("copy-list-link");
  if (!link) return;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (!navigator.clipboard) { link.textContent = "Copy the address bar"; return; }
    navigator.clipboard.writeText(window.location.href).then(
      () => { link.textContent = "Copied"; },
      () => { link.textContent = "Copy the address bar"; },
    );
    setTimeout(() => { link.textContent = "Copy link"; }, 1600);
  });
})();
</script>
"""

# What the map screen remembers about your last pick, cleared on the way back.
LANDING_PICKS = ("clicked", "centre", "station_pick", "station_find_applied",
                 "station_click_km", "station_clicks_seen")


def link_token(key):
    """A registry key as it reads in the address: without its plus signs,
    which a browser would turn into spaces."""
    return key.replace("+", "")


def key_from_token(token):
    """The registry key an address token stands for, plus signs restored."""
    return "_".join(
        f"{float(part):+.2f}" if DECIMAL.fullmatch(part) else part
        for part in token.replace(" ", "").split("_")
    )


def link_keys():
    """The spots in your link, in the order you added them."""
    keys = []
    for token in st.query_params.get_all("spot"):
        key = key_from_token(token)
        if key not in keys:
            keys.append(key)
    return keys


def write_link(keys):
    if keys:
        st.query_params["spot"] = [link_token(key) for key in keys]
    elif "spot" in st.query_params:
        del st.query_params["spot"]


def follow(spot):
    """Add a spot to your link, if it is not there yet."""
    keys = link_keys()
    if spot_key(spot) not in keys:
        write_link([*keys, spot_key(spot)])


def unfollow(key):
    write_link([other for other in link_keys() if other != key])


def spot_from_key(key, registry):
    """What a key alone says about a spot, for one whose record this server
    does not hold: the registry's entry, else the cell read off the key or
    the station looked up in its table. None where the key says nothing."""
    entry = registry.get(key)
    cell, station = CELL_KEY.fullmatch(key), STATION_KEY.fullmatch(key)
    if cell:
        source = cell.group(1) or "era5"
        if entry is None:
            latitude, longitude = float(cell.group(2)), float(cell.group(3))
            entry = {
                "lat": latitude, "lon": longitude,
                "name": f"{latitude:+.2f}, {longitude:+.2f}",
                "zone": timezone_at(latitude, longitude),
            }
    elif station:
        source = next(name for name, prefix in STATION_FILES.items()
                      if prefix == station.group(1))
        if entry is None:
            try:
                row = station_table(source).loc[station.group(2)]
            except Exception:
                return None
            entry = station_entry(source, station.group(2), row)
    else:
        return None
    spot = {**entry, "source": source}
    return {**spot, "path": spot_path(spot)}


def my_locations(with_record=False):
    """Your spots, in the order you added them: those on this server by
    their file, the rest by what their key says. `with_record` keeps only
    the ones whose record is here to count."""
    on_disk = downloaded_locations()
    registry = load_registry()
    mine = {}
    for key in link_keys():
        entry = on_disk.get(key) or spot_from_key(key, registry)
        if entry is not None and (entry["path"].exists() or not with_record):
            mine[key] = entry
    return mine


def draw_list_chip():
    """Your list in the top bar of every screen: how many spots the address
    carries, the link to copy, and Clear, which empties it and starts you
    on the map as a newcomer would."""
    count = len(link_keys())
    with topbar.container(horizontal=True, vertical_alignment="center", gap="small",
                          key="list_chip", width="content"):
        if not count:
            st.html('<div class="list-chip"><span>Your list</span><b>empty</b></div>')
            return
        st.html(
            f'<div class="list-chip"><span>Your list</span><b>{count}</b>'
            f'{"spot" if count == 1 else "spots"}'
            '<a href="#" id="copy-list-link">Copy link</a></div>' + COPY_LINK_SCRIPT,
            unsafe_allow_javascript=True,
        )
        if st.button("Clear", key="clear_list", type="tertiary", icon=":material/delete_sweep:",
                     help="Empty your list and start on the map, as a newcomer would. "
                     "The records stay on this server."):
            write_link([])
            for key in ("spot", "mode", "last_mode", "fetch_first", *LANDING_PICKS):
                st.session_state.pop(key, None)
            st.rerun()


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
    start, end = hours
    return pd.Series((index.hour >= start) & (index.hour <= end), index=index)


def theme_colors():
    """Palette that follows the active Streamlit theme."""
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    return {
        # Matched to .streamlit/config.toml so the figures sit in the palette.
        "fg": "#e7ebf3" if dark else "#1f2328",
        "muted": "#8a94a8" if dark else "#57606a",
        "grid": "#273043" if dark else "#d0d7de",
    }


def style_figure(fig, ax):
    """Drop the opaque canvas so the rose sits on the app background, its
    ticks, ring and legend in quiet colours."""
    colors = theme_colors()
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    ax.tick_params(colors=colors["muted"], labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(colors["grid"])
    legend = ax.get_legend()
    for text in [*legend.get_texts(), legend.get_title()]:
        text.set_color(colors["muted"])
    return fig


def pick(value, fallback):
    """segmented_control returns None when the user deselects; keep a value."""
    return fallback if value is None else value


def remembered(widget, label, *args, key, default, start="value", **options):
    """Draw a widget whose choice outlives the views that hide it.

    Streamlit forgets a widget's state once it is not drawn, so the value is
    copied aside every run and handed back as the widget's own starting value
    (`start` names that argument). Not put back through session state: a
    widget drawn from there alone shows its blank default, and a range slider
    even falls back to its full span and then reports that span back. An
    emptied choice keeps the last one."""
    kept_value = st.session_state.get(f"kept_{key}", default)
    value = widget(label, *args, key=key, **{start: kept_value}, **options)
    if value is None:
        value = kept_value
    st.session_state[f"kept_{key}"] = value
    return value


def kept(key, default):
    """A remembered setting's value on a screen that does not draw its widget.
    Read from the copy remembered() keeps, never from the widget's own state,
    which a redrawn widget can leave empty."""
    return st.session_state.get(f"kept_{key}", default)


def wind_words(wind):
    """6–25 kt, 15+ kt or up to 12 kt: a wind range as a sailor says it."""
    low, high = wind
    if high >= WIND_TOP:
        return f"{low}+ kt"
    return f"up to {high} kt" if low == 0 else f"{low}–{high} kt"


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
/* Esri's dark canvas is a neutral grey; this turns it the app's blue-black,
   sea about #111822 and land #212c44, so a map and its page read as one.
   Only the base layer: the place names above it stay as drawn. The ground
   under tiles still loading is the sea's colour. */
.leaflet-container { background: #111822; }
.base-tiles { filter: sepia(.6) hue-rotate(185deg) saturate(4) brightness(.6); }
.spot-dot {
    width: 13px; height: 13px; border-radius: 50%;
    background: #7dd3fc;
    border: 2px solid rgba(255,255,255,.9);
    box-shadow: 0 0 0 4px rgba(125,211,252,.22), 0 2px 6px rgba(0,0,0,.45);
}
.spot-dot.picked { background: #2dd4bf; box-shadow: 0 0 0 5px rgba(45,212,191,.25), 0 2px 8px rgba(0,0,0,.5); }
/* A station you have not downloaded: present, but quieter than your spots. */
.spot-dot.idle {
    width: 9px; height: 9px; margin: 2px; background: #5b6478;
    border: 1.5px solid rgba(255,255,255,.6); box-shadow: 0 1px 4px rgba(0,0,0,.4);
}
/* Beside the compare button: the spots compared, in teal like this spot
   but without its ring, and where you clicked, in white. */
.spot-dot.compared { background: #2dd4bf; box-shadow: 0 0 0 4px rgba(45,212,191,.22), 0 2px 6px rgba(0,0,0,.45); }
.spot-dot.clicked { background: #e7ebf3; box-shadow: 0 0 0 5px rgba(231,235,243,.25), 0 2px 8px rgba(0,0,0,.5); }
.spot-dot.picked::after {
    content: ""; position: absolute; inset: -5px; border-radius: 50%;
    border: 2px solid rgba(45,212,191,.55);

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
    color: #717c92 !important;
}
.leaflet-control-attribution img { display: none !important; }
.spot-hover-tip {
    background: #0a0d14; color: #e7ebf3; border: 1px solid #2dd4bf;
    border-radius: 4px; font: 700 12px/1.2 Inter, system-ui, sans-serif;
    padding: 4px 8px; box-shadow: 0 4px 14px rgba(0, 0, 0, .5);
}
.spot-hover-tip::before { border-top-color: #2dd4bf !important; }
</style>
"""

# Wind drifting over the landing map: the earth's wind belts as a textbook
# draws them, not measured or forecast wind. It is drawn on a canvas in a
# pane of the map's own, above the tiles and the AROME outline and under the
# dots, and lets every click through. Like the hover script below it runs in
# the page and reaches into the map's frame; it checks for a new map now and
# then, since a rerun or a new source draws one. Pans and zooms clear it and
# it starts again where the map stops. Nothing moves for anyone who asks
# their system for reduced motion. Same rule as below: no "<" before a
# letter, "/" or "!".
WIND_FLOW_SCRIPT = """
<script>
(() => {
  if (window.__windFlow) return;
  window.__windFlow = true;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let attached = null;
  const attach = () => {
    const frame = document.querySelector(
      '.st-key-landing_stage iframe[title="streamlit_folium.st_folium"]');
    const view = frame && frame.contentWindow;
    const map = view && view.map;
    if (!map || !view.L || map === attached || !map.getContainer().isConnected) return;
    attached = map;
    // On a wide screen the title floats over the map's left part and your
    // spots over its right: shift a new map's view so what it opened on, the
    // spot you clicked included, sits in the clear part between them.
    const hero = document.querySelector(".st-key-landing_hero");
    if (hero && getComputedStyle(hero).position === "absolute") {
      const spots = document.querySelector(".st-key-landing_spots");
      const width = map.getSize().x;
      const left = hero.getBoundingClientRect().right;
      const right = spots ? spots.getBoundingClientRect().left : width;
      map.panBy([width / 2 - (left + right) / 2, 0], { animate: false });
    }
    if (!still) flow(view, map);
  };
  // Often enough that a new map is shifted before it is noticed.
  setInterval(attach, 250);
  attach();

  function flow(view, map) {
    const L = view.L;
    const pane = map.getPane("wind") || map.createPane("wind");
    pane.style.zIndex = 450;
    pane.style.pointerEvents = "none";
    const canvas = view.document.createElement("canvas");
    pane.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const ratio = view.devicePixelRatio || 1;
    // The world's width in pixels at this zoom: the map's own scale.
    let w = 0, h = 0, world = 256, moving = false, particles = [];

    // Each trail is its own last positions, redrawn every frame, so nothing
    // is left behind on the map. They are kept in a ring that is written
    // over, so moving them makes nothing for the browser to clear away, which
    // would pause the wind now and then. A trail that has run its course
    // stops growing and shrinks away from its tail, then starts again
    // elsewhere. `head` is where its next position goes, `n` how many it has.
    const TRAIL = 110;
    const spawn = (p) => {
      p = p || { xs: new Float32Array(TRAIL), ys: new Float32Array(TRAIL) };
      p.x = Math.random() * w; p.y = Math.random() * h;
      p.age = 0; p.life = 500 + Math.random() * 700; p.head = 0; p.n = 0;
      return p;
    };
    const place = () => {
      const size = map.getSize();
      w = size.x; h = size.y;
      world = 256 * Math.pow(2, map.getZoom());
      canvas.width = w * ratio; canvas.height = h * ratio;
      canvas.style.width = w + "px"; canvas.style.height = h + "px";
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      particles = Array.from({ length: Math.round(w * h / 2200) }, () => {
        const p = spawn();
        p.age = Math.random() * p.life;
        return p;
      });
    };
    // The wind belts as a geography book draws them, by latitude: calm at
    // the equator, the trades blowing towards it from about 5 to 30 degrees
    // (from the NE in the north, the SE in the south), calm again near 30,
    // westerlies to 60 (strongest in the Southern Ocean) and weak polar
    // easterlies beyond. The belts follow the sun a few degrees north and
    // south through the year, less so towards the poles. A slow wobble tied
    // to the earth keeps them from looking like stripes and turns the calms
    // into light air from anywhere. Sets `driftX` and `driftY`, in pixels a
    // step, from x and y in the map's world pixels; never faster than 0.42.
    const today = new Date();
    const day = (today - new Date(today.getFullYear(), 0, 1)) / 864e5;
    // Furthest north in early August, furthest south in early February.
    const sun = 4 + 4 * Math.cos(2 * Math.PI * (day - 220) / 365);
    let driftX = 0, driftY = 0;
    const field = (x, y) => {
      const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / world))) * 180 / Math.PI;
      const turn = 2 * Math.PI * x / world;
      const shifted = lat - sun * (1 - Math.abs(lat) / 90);
      const side = shifted < 0 ? -1 : 1;
      const away = Math.abs(shifted);
      // Easterly where positive, westerly where negative, nothing at 0, 30, 60.
      const belt = Math.sin(away * 6 * Math.PI / 180);
      const strength = away < 30 ? 0.85 : away < 60 ? (side < 0 ? 1 : 0.85) : 0.45;
      const wobble = Math.sin(3 * turn + 1.7 * Math.cos(lat * 0.08))
                   + 0.6 * Math.cos(7 * turn - lat * 0.07);
      // East and north: the belt's wind, turned towards the equator in the
      // trades and away from it in the westerlies, plus the light air.
      const east = -belt + 0.3 * Math.cos(2.5 * wobble);
      const north = -side * 0.45 * belt + 0.3 * Math.sin(2.5 * wobble);
      const angle = Math.atan2(north, east) + 0.2 * wobble;
      const speed = 0.065 + 0.35 * strength * Math.abs(belt);
      driftX = Math.cos(angle) * speed;
      driftY = -Math.sin(angle) * speed;
    };

    map.on("movestart zoomstart", () => { moving = true; ctx.clearRect(0, 0, w, h); });
    map.on("moveend zoomend resize", () => { moving = false; place(); });
    place();

    // One step of the wind: every trail moves on, or, run its course or off
    // the map, loses its tail. `off` turns the canvas's pixels into the map's.
    const advance = (off) => {
      for (const p of particles) {
        if (p.age > p.life || p.x > w || p.y > h || p.x < 0 || p.y < 0) {
          p.n -= 1;
          if (p.n <= 0) spawn(p);
          continue;
        }
        field(p.x + off.x, p.y + off.y);
        p.xs[p.head] = p.x; p.ys[p.head] = p.y;
        p.head = (p.head + 1) % TRAIL;
        if (p.n < TRAIL) p.n += 1;
        p.x += driftX; p.y += driftY;
        p.age += 1;
      }
    };

    // Trails are drawn in bands from tail to head, each a little brighter,
    // so they fade towards the tail: one stroke per band, not per trail.
    // Every other position is enough for curves this gentle, and halves the
    // drawing, which is what a sharp screen slows down on.
    const BANDS = 5;
    const draw = () => {
      ctx.clearRect(0, 0, w, h);
      ctx.lineWidth = 1.1;
      ctx.lineCap = "round";
      for (let band = 0; band < BANDS; band++) {
        ctx.strokeStyle = "rgba(214, 233, 255, " + (0.09 * (band + 1)) + ")";
        ctx.beginPath();
        for (const p of particles) {
          const n = p.n;
          const from = Math.floor(n * band / BANDS);
          const to = Math.min(n - 1, Math.floor(n * (band + 1) / BANDS));
          if (to - from < 1) continue;
          // The oldest position, counted on a lap so the index stays positive.
          const oldest = p.head - n + TRAIL;
          let k = (oldest + from) % TRAIL;
          ctx.moveTo(p.xs[k], p.ys[k]);
          for (let i = from + 2; i < to; i += 2) {
            k = (oldest + i) % TRAIL;
            ctx.lineTo(p.xs[k], p.ys[k]);
          }
          k = (oldest + to) % TRAIL;
          ctx.lineTo(p.xs[k], p.ys[k]);
        }
        ctx.stroke();
      }
    };

    // The wind runs on the clock, sixty steps a second, not one step a
    // frame: a screen that draws faster, slower or unevenly (a laptop that
    // changes its refresh rate, a busy moment) shows it at the same speed.
    // Whole steps only, as many as the time since the last frame rounds to,
    // the rest carried over. After a long pause, a hidden tab say, it takes
    // up to four and lets the rest go, rather than racing to catch up.
    const TICK = 1000 / 60;
    let last = 0, owed = 0;
    const frame = (now) => {
      if (!canvas.isConnected || attached !== map) return;
      view.requestAnimationFrame(frame);
      if (moving || !last) { last = now; return; }
      owed += now - last;
      last = now;
      let steps = Math.round(owed / TICK);
      if (steps > 4) { steps = 4; owed = 0; } else { owed -= steps * TICK; }
      if (!steps) return;
      const off = map.containerPointToLayerPoint([0, 0]).add(map.getPixelOrigin());
      for (let i = 0; i < steps; i++) advance(off);
      draw();
    };
    view.requestAnimationFrame(frame);
  }
})();
</script>
"""


# The landing map's height on a phone or narrow window. On a wide one the
# page styles stretch its frame to the window, and FILL_FRAME_CSS, inside the
# frame, has the map follow: streamlit_folium otherwise fixes it in pixels.
LANDING_MAP_HEIGHT = 700
FILL_FRAME_CSS = """
<style>
#map_div, #map_div2 { height: 100vh !important; }
html, body { overflow: hidden; }
</style>
"""


def stage_map(**options):
    """A map that is a page's stage, the landing's: the house map, filling
    whatever frame the page styles give it."""
    world = base_map(**{"control_scale": True, "min_zoom": 2, "world_copy_jump": True,
                        "zoom_control": "bottomright", **options})
    world.get_root().header.add_child(folium.Element(FILL_FRAME_CSS))
    return world


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


def dot_marker(latitude, longitude, tooltip, picked=False, idle=False, kind=None):
    """A designed dot rather than folium's default pin; `kind` names a
    style of MAP_CHROME_CSS beyond picked and idle."""
    classes = ("spot-dot picked" if picked else "spot-dot idle" if idle
               else f"spot-dot {kind}" if kind else "spot-dot")
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
            class_name="label-tiles" if overlay else "base-tiles",
        ).add_to(chart)
    chart.get_root().header.add_child(folium.Element(MAP_CHROME_CSS))
    return chart


def locator_map(latitude, longitude, key, height=230, cell=GRID):
    """The cell, `cell` degrees across, against the coastline, so you can see
    whether the water you care about is actually water. A station is a
    point, so it gets no cell: None. Zoom out to place a cell far offshore."""
    local = base_map(location=[latitude, longitude], zoom_start=7, dragging=True)
    if cell:
        folium.Rectangle(
            bounds=[
                [latitude - cell / 2, longitude - cell / 2],
                [latitude + cell / 2, longitude + cell / 2],
            ],
            color="#2dd4bf", weight=2, fill=True, fill_opacity=0.25,
        ).add_to(local)
    dot_marker(latitude, longitude, "Your cell" if cell else "Station", picked=True).add_to(local)
    st_folium(local, height=height, use_container_width=True, key=key,
              returned_objects=[])


def draw_rose(direction, speed, thresholds):
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
    for band, color, label in zip(bands, ["#dde2ec", *THRESHOLD_COLORS], labels):
        upper = lower + band
        ax.fill_between(
            closed, np.append(lower, lower[0]), np.append(upper, upper[0]),
            color=color, linewidth=0, label=label,
        )
        lower = upper
    ax.plot(closed, np.append(lower, lower[0]), color=palette["fg"], lw=0.7, alpha=0.35)

    ax.set_xticks(np.deg2rad(np.arange(0, 360, 45)))
    ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"])
    ax.tick_params(axis="x", pad=1)
    ax.set_yticklabels([])
    ax.tick_params(axis="y", length=0)
    ax.grid(color=palette["grid"], alpha=0.5, linewidth=0.6)
    ax.spines["polar"].set_linewidth(0.6)
    ax.legend(
        title="knots", loc="center left", bbox_to_anchor=(1.06, 0.5),
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
    return start, end


draw_list_chip()


# --- Stage 1: pick a spot on the map --------------------------------------

if "spot" not in st.session_state:
    st.session_state["spot"] = None
# Kept apart from the switch's own key, which Streamlit drops while the
# landing is off screen, so Back returns you to the source you were using.
st.session_state.setdefault("data_source", "era5")


def enter_spot(spot):
    st.session_state["spot"] = spot
    remember_location(spot)
    follow(spot)


def fresh_click(result, seen_key):
    """The click just made on a map, or None. A dot catches its click before
    the map does, so it reports as an object click (counted, so the same dot
    twice still registers) and open ground as a map click: whichever moved on
    is the new one. st_folium replays both on every rerun, hence comparing
    against the clicks already used rather than the current pick, which let
    a stale click overwrite a station found a moment before."""
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


STATION_PICKS = ("station_pick", "station_find", "station_find_applied", "station_click_km")


def switch_source():
    """A station picked from one source is no station in another."""
    for key in STATION_PICKS:
        st.session_state.pop(key, None)


def forget_pick(centre):
    """Cancel: the pick goes, and the map stays looking where it was."""
    for key in ("clicked", *STATION_PICKS):
        st.session_state.pop(key, None)
    st.session_state["centre"] = centre


def pick_panel(slot, spot, facts, name, action):
    """The spot you clicked, in `slot` at the foot of the map: three facts
    as (label, value, icon, help), a name to give it, the way in and Cancel."""
    with slot.container(key="picked_panel"):
        columns = st.columns([2, 2, 2, 3], vertical_alignment="center")
        for column, (label, value, icon, note) in zip(columns, facts):
            column.metric(label, value, icon=icon, help=note)
        with columns[3]:
            name = st.text_input("Name this spot", value=name, key=f"name_{spot_key(spot)}")
            with st.container(horizontal=True):
                if st.button(f":material/arrow_forward: {action}", type="primary",
                             width="stretch"):
                    enter_spot({**spot, "name": name})
                    st.rerun()
                st.button("Cancel", on_click=forget_pick, args=((spot["lat"], spot["lon"]),))


if st.session_state["spot"] is None:
    saved = my_locations()
    # The switches sit below the hero they rewrite, so read their values ahead.
    source = st.session_state.get("source_pick") or st.session_state["data_source"]
    by_water = st.session_state.get("by_water", True)
    stations, stations_error = None, None
    if source in STATION_FILES:
        try:
            stations = station_table(source)
        except Exception as error:
            stations_error = error
    if stations is not None and by_water:
        # A summit station looks down on the water rather than standing by it.
        stations = stations[by_a_shore(stations, SHORE_KM) & ~stations["summit"]]

    # One line on what a click fetches from the chosen source, and four
    # figures on it, the last always your list.
    if source == "metar":
        pitch = ("Click near an airport to download every wind report it has sent, "
                 "measured beside the runway.")
        facts = [("Hourly", "most since the 1970s"), ("Measured", "not modelled"),
                 (f"{len(stations):,}" if stations is not None else "1,400+",
                  "airports by the water" if by_water else "airports")]
    elif source == "meteoswiss":
        pitch = "Click a MeteoSwiss station to download every hour it has measured."
        facts = [(f"{stations['since'].min():.0f}" if stations is not None else "1981",
                  "first year on record"),
                 ("Measured", "not modelled"),
                 (f"{len(stations):,}" if stations is not None else "25+",
                  "stations by the water" if by_water else "stations with wind")]
    elif source == "arome":
        pitch = ("Click any water in western Europe to download every hour from "
                 "Météo-France's AROME model: lakes, bays and straits get their own wind.")
        facts = [("1.5 km", "per grid cell"), (f"{AROME_SINCE:%b %Y}", "first month on record"),
                 ("Hourly", "every day since")]
    else:
        pitch = ("Click any water to download twenty years of hourly ERA5 wind for that "
                 "spot, and every year since if you ask.")
        facts = [("1940", "first year on record"), ("Hourly", "every day since"),
                 ("25 km", "per grid cell")]
    facts.append((len(saved), "spot in your list" if len(saved) == 1 else "spots in your list"))

    # The map is the stage; the rest floats on it (see "Landing" in the styles).
    # Each part gets its slot now, in the order a phone stacks them, and is
    # filled further down once what it shows is known.
    with st.container(key="landing_stage"):
        hero = st.container(key="landing_hero")
        map_col = st.container()
        list_col = st.container(key="landing_spots") if saved else None
        pick_slot = st.container(key="landing_pick")

    hero.html(
        '<div class="stage-title">Hourly wind history<br>for <span>any spot</span></div>'
        f'<div class="stage-sub">{pitch} Then see how often it is sailable for you, '
        "or compare it with other spots on the dates you are going.</div>"
    )
    hero.html(
        '<div class="fact-boxes">'
        + "".join(f"<div><b>{value}</b><span>{label}</span></div>" for value, label in facts)
        + "</div>"
    )
    found = None
    with hero.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
        source = st.segmented_control(
            "Data source", list(SOURCES),
            format_func=lambda key: SOURCES[key].split(" · ")[0],
            default=st.session_state["data_source"], required=True, key="source_pick",
            on_change=switch_source,
        )
        st.session_state["data_source"] = source
        if stations is not None:
            labels = (stations["name"] + " · " + stations.index.to_series()).to_dict()
            found = st.selectbox(
                "Find a station", list(stations.index), index=None, format_func=labels.get,
                placeholder=f"Search {len(stations):,} stations", key="station_find",
                width=300,
            )
            st.toggle(
                "By the water only", value=True, key="by_water",
                help=f"Stations within {SHORE_KM} km of the sea or a lake. Small lakes "
                "may be missing from the map of shores: switch this off to see them all.",
            )
    if found and st.session_state.get("station_find_applied") != found:
        st.session_state["station_find_applied"] = found
        st.session_state["station_pick"] = found
        st.session_state["station_click_km"] = None
        st.session_state["station_recentre"] = True
        st.rerun()
    if stations_error is not None:
        hero.error(f"Could not load the {STATION_NAMES[source]} stations: {stations_error}")
        st.stop()
    hint_slot = hero.empty()

    clicked = st.session_state.get("clicked")
    centre = clicked or st.session_state.get("centre")
    picked = st.session_state.get("station_pick")
    if stations is None or picked not in stations.index:
        picked = None

    outside = False  # a click where the model has no data
    with map_col:
        if source in CELL_GRIDS:
            grid = CELL_GRIDS[source]
            home, far = CELL_VIEWS[source]
            world = stage_map(location=list(centre or home), zoom_start=7 if centre else far)
            if source == "arome":
                # The model stops at the outline; a click past it finds nothing.
                south, north, west, east = AROME_AREA
                folium.Rectangle(
                    bounds=[[south, west], [north, east]], color="#7dd3fc", weight=1.5,
                    fill=False, dash_array="6 6", tooltip="Where AROME has data",
                ).add_to(world)
                outside = bool(clicked) and not in_arome_area(*clicked)
            if outside:
                hint = "AROME has no data there: click inside the outline"
            elif clicked:
                hint = "Click again to move your pick"
            else:
                hint = "Click anywhere on the water" + (" inside the outline" if source == "arome" else "")
            hint_slot.html(f'<div class="landing-hint{" warn" if outside else ""}">{hint}</div>')
            # Your spots, so the map doubles as your library.
            for entry in saved.values():
                dot_marker(entry["lat"], entry["lon"], spot_title(entry)).add_to(world)
            if clicked and not outside:
                cell_lat, cell_lon = snap_to_grid(clicked[0], grid), snap_to_grid(clicked[1], grid)
                folium.Rectangle(
                    bounds=[
                        [cell_lat - grid / 2, cell_lon - grid / 2],
                        [cell_lat + grid / 2, cell_lon + grid / 2],
                    ],
                    color="#2dd4bf", weight=2, fill=True, fill_opacity=0.25,
                    tooltip=f"{cell_lat:+.2f}, {cell_lon:+.2f}",
                ).add_to(world)
                dot_marker(cell_lat, cell_lon, "Selected cell", picked=True).add_to(world)

            result = st_folium(
                world, height=LANDING_MAP_HEIGHT, use_container_width=True,
                key=f"{source}_picker",
                returned_objects=["last_clicked", "last_object_clicked",
                                  "last_object_clicked_count"],
            )
        else:
            focus = tuple(stations.loc[picked, ["lat", "lon"]]) if picked else centre or SWISS_CENTRE
            world = stage_map(
                location=list(focus), zoom_start=10 if picked or centre else STATION_ZOOM[source])
            hint = (
                "Click another station to switch" if picked
                else "Click a station, or anywhere for the nearest one"
            )
            hint_slot.html(f'<div class="landing-hint">{hint}</div>')
            # The stations where the map looks, as it reported its view after
            # the last move; before it has, those nearest its centre. The ones
            # on your list stand out. They are a layer of their own, so
            # a move swaps the dots without redrawing the map under them.
            view = (st.session_state.get(f"{source}_picker") or {}).get("bounds")
            near = (
                stations_in_view(stations, view) if view and view["_southWest"]["lat"] is not None
                else stations.loc[km_from(*focus, stations["lat"], stations["lon"])
                                  .nsmallest(NEARBY_STATIONS).index]
            )
            mine = {entry["station"] for entry in saved.values() if entry.get("source") == source}
            dots = folium.FeatureGroup(name="stations")
            for abbr, row in near.iterrows():
                if abbr != picked:
                    dot_marker(row["lat"], row["lon"], labels[abbr],
                               idle=abbr not in mine).add_to(dots)
            if picked:
                dot_marker(*focus, labels[picked], picked=True).add_to(dots)

            recentre = st.session_state.pop("station_recentre", False)
            result = st_folium(
                world, height=LANDING_MAP_HEIGHT, use_container_width=True,
                key=f"{source}_picker", feature_group_to_add=dots,
                center=list(focus) if recentre else None,
                zoom=10 if recentre else None,
                returned_objects=["last_clicked", "last_object_clicked",
                                  "last_object_clicked_count", "bounds"],
            )

    fresh = fresh_click(
        result, f"{source}_click_seen" if source in CELL_GRIDS else "station_clicks_seen")
    if fresh is not None and source in CELL_GRIDS:
        # A dot is one of your spots: its own cell, so it opens as itself.
        st.session_state["clicked"] = fresh
        st.rerun()
    elif fresh is not None:
        km = km_from(*fresh, stations["lat"], stations["lon"])
        st.session_state["station_pick"] = km.idxmin()
        st.session_state["station_click_km"] = float(km.min())
        st.rerun()
    st.html(WIND_FLOW_SCRIPT, unsafe_allow_javascript=True)

    if list_col is not None:
        with list_col:
            st.html(
                '<div class="section-label">Your spots'
                f"<small>{len(saved)} in your link · hover to find on the map</small></div>"
            )
            # As tall as its rows, scrolling once it outgrows the space
            # above the zoom buttons (see "spot_rows" in the styles).
            with st.container(gap="xsmall", key="spot_rows"):
                for index, (key, entry) in enumerate(saved.items()):
                    if entry.get("station"):
                        where = (f"{STATION_NAMES[entry['source']]} {entry['station']} · "
                                 f"{entry['height']:.0f} m")
                    else:
                        where = (f"{MODEL_NAMES[entry['source']]} · "
                                 f"{coords_label(entry['lat'], entry['lon'])}")
                    if entry["path"].exists():
                        first, last = record_span(entry["path"], entry["path"].stat().st_mtime)
                        when = f"{first}–{last}"
                    else:
                        # The hosted app lost its downloads in a restart, or the
                        # link was made on another copy of the app.
                        when = "not on this server yet, Open fetches it"
                    # Keyed by position: the hover script below matches cards to
                    # spots by this number.
                    with st.container(key=f"spot_card_{index}", horizontal=True,
                                      vertical_alignment="center"):
                        st.markdown(f"**{spot_title(entry)}**  \n:gray[{where} · {when}]")
                        if st.button("Open", key=f"open_{key}",
                                     icon=":material/arrow_forward:", width="content"):
                            enter_spot({k: v for k, v in entry.items() if k != "path"})
                            st.rerun()
                        if st.button(":material/close:", key=f"drop_{key}", type="tertiary",
                                     width="content",
                                     help="Take this spot off your list. Its record stays "
                                     "here for whoever else has it."):
                            unfollow(key)
                            st.rerun()
            st.html(LINK_NOTE)
        spots = [
            {"lat": entry["lat"], "lon": entry["lon"], "name": spot_title(entry)}
            for entry in saved.values()
        ]
        # Every "<" escaped, so no spot name can trip the sanitizer (see above).
        st.html(
            SPOT_HOVER_SCRIPT.replace("SPOTS_JSON", json.dumps(spots).replace("<", "\\u003c")),
            unsafe_allow_javascript=True,
        )

    if source in CELL_GRIDS and clicked and not outside:
        raw_lat, raw_lon = clicked
        grid = CELL_GRIDS[source]
        latitude, longitude = snap_to_grid(raw_lat, grid), snap_to_grid(raw_lon, grid)
        zone = timezone_at(latitude, longitude)
        cell = {"lat": latitude, "lon": longitude, "source": source, "zone": zone}
        cached = spot_path(cell)
        known = load_registry().get(spot_key(cell), {})
        pick_panel(
            pick_slot, cell,
            [("Your pick", f"{latitude:+.2f}, {longitude:+.2f}", ":material/location_on:",
              f"You clicked {raw_lat:.3f}, {raw_lon:.3f}"),
             ("Time zone", zone.split("/")[-1].replace("_", " "), ":material/schedule:", zone),
             ("Data", "on disk" if cached.exists() else "needs fetching",
              ":material/database:", None)],
            spot_title(known) if known else place_name(latitude, longitude),
            "Use this location",
        )

    if picked:
        row = stations.loc[picked]
        station = station_entry(source, picked, row)
        cached = spot_path(station)
        where = f"{source_credit(station)} · {coords_label(row['lat'], row['lon'])}"
        km = st.session_state.get("station_click_km")
        if km and km >= 1:
            where += f" · nearest to your click, {km:.0f} km away"

        pick_panel(
            pick_slot, station,
            [("Your station", row["name"], ":material/sensors:", where),
             ("Height", f"{row['height']:.0f} m", ":material/landscape:",
              "Above sea level. A station up a hill feels more wind than the water below it."),
             ("Data", "on disk" if cached.exists() else f"since {row['since']:.0f}",
              ":material/database:",
              None if row["has_temp"] else "This station measures no air temperature, "
              "so it borrows it from the nearest one that does.")],
            load_registry().get(spot_key(station), {}).get("name") or row["name"],
            "Use this station",
        )

    small_print()
    st.stop()


# Open on a sport rather than "Any wind", which cannot be rated.
st.session_state.setdefault("activity", "Dinghy")
activity = st.session_state["activity"]
spot = st.session_state["spot"]
LATITUDE, LONGITUDE = spot["lat"], spot["lon"]
LOCAL_TZ, LOCATION_NAME = spot["zone"], spot_title(spot)
# The station's code, or None for a model cell; the cell's size, or None for a station.
STATION = spot.get("station")
SOURCE = spot.get("source", "era5")
CELL = None if STATION else CELL_GRIDS[SOURCE]
SOURCE_CREDIT = source_credit(spot)
dataset_path = spot_path(spot)

# Navigation: step back one screen without losing the location.
# One back / forward pair, plus the reset to the very beginning.
# Back steps out of a view to the options, or off the options back to the map.
# Forward returns to whichever view you last left.
current_mode = st.session_state.get("mode")
# No way out while a record downloads: a click would cut it short. The
# loading screen offers the map itself where the download fails.
if dataset_path.exists() and "fetch_first" not in st.session_state:
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
        st.rerun()

    if nav.button(":material/home: Start over", type="tertiary"):
        for key in ("spot", "mode", "last_mode", *LANDING_PICKS):
            st.session_state.pop(key, None)
        st.rerun()


# --- Stage 2: make sure this location has data ----------------------------
# Every source starts downloading the moment you arrive: nothing to press.


def back_to_map():
    """Leave the spot for the map, as Back does from the start screen."""
    for key in ("spot", "fetch_first", *LANDING_PICKS):
        st.session_state.pop(key, None)
    st.rerun()


def loading_screen(note):
    """The quiet screen a record arrives behind: the spot's name, what is
    being fetched, one bar and nothing to click. Returns `show(fraction,
    text)`, which fills the bar that far, or sweeps it while `fraction` is
    None, and `clear`, which takes the bar away."""
    with st.container(key="loading_screen"):
        st.html(
            f'<div class="loading-title">Downloading {html.escape(LOCATION_NAME)}</div>'
            f'<div class="loading-note">{note}</div>'
        )
        slot = st.empty()
    return bar_in(slot), slot.empty


def bar_in(slot):
    """A progress bar drawn into `slot`: `show(fraction, text)` fills it that
    far, or sweeps it while `fraction` is None."""
    def show(fraction, text):
        fill = "" if fraction is None else f' style="width:{fraction * 100:.0f}%"'
        slot.html(
            f'<div class="loading-bar{" unknown" if fraction is None else ""}"><i{fill}></i></div>'
            f'<div class="loading-text">{html.escape(text)}</div>'
        )
    return show


def fetch_now(fetch, what, clear, hint=None):
    """Run `fetch` straight away. Done, the app goes on to the spot; failed,
    it takes the bar away, says why and offers another go, or the map, or,
    where a record is already in hand and more years were asked for, that
    record as it is."""
    try:
        fetch()
    except Exception as error:
        clear()
        st.error(f"Could not fetch that {what}: {error}")
        if hint:
            st.caption(hint)
        with st.container(horizontal=True):
            st.button("Try again", icon=":material/refresh:", type="primary")
            if dataset_path.exists():
                if st.button("Keep the years you have", icon=":material/undo:"):
                    st.session_state.pop("fetch_first", None)
                    st.rerun()
            elif st.button("Back to the map", icon=":material/arrow_back:"):
                back_to_map()
        st.stop()
    st.session_state.pop("fetch_first", None)
    st.rerun()


def counting(show, unit):
    """A report for the fetches that land `unit`s one by one, moving the bar."""
    def report(done, total):
        show(done / total, f"Downloaded {done} of {total} {unit}" if done < total
             else f"All {unit} in, joining them into one record...")
    return report


def fetch_era5(spot, target, first):
    """Every hour of ERA5 for a cell from `first` to today, prepared on
    Copernicus' side: slow, and only with an account."""
    client = cds_client()
    download_path = data_dir / f"download_{spot['lat']:+.2f}_{spot['lon']:+.2f}.zip"
    client.retrieve(
        "reanalysis-era5-single-levels-timeseries",
        {
            "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind", "2m_temperature"],
            "location": {"latitude": spot["lat"], "longitude": spot["lon"]},
            "date": [f"{first}/{date.today()}"],
            "data_format": "netcdf",
        },
        str(download_path),
    )
    unpack_download(download_path, target)


def fetch_here(entry, show, first=None):
    """Download a spot's record behind `show`, the bar of the loading screen
    or of a row: ERA5 from `first`, or the last twenty years."""
    target = spot_path(entry)
    if entry["source"] == "metar":
        show(None, "The Iowa Environmental Mesonet is gathering every report...")
        fetch_metar(entry, target)
    elif entry["source"] == "meteoswiss":
        show(None, "Asking MeteoSwiss for the station's files...")
        fetch_station(entry, target, progress=counting(show, "files"))
    elif entry["source"] == "arome":
        show(None, "Asking Open-Meteo for the first year...")
        fetch_arome(entry, target, progress=counting(show, "years"))
    else:
        show(None, "Copernicus is preparing the data: this can take a while...")
        fetch_era5(entry, target, first or date(date.today().year - 19, 1, 1))


if not dataset_path.exists() or "fetch_first" in st.session_state:
    # Nothing to press and nothing to click: the record lands behind one bar.
    # ERA5 brings the last twenty years at first, and every year since 1940
    # when asked from the filter, which sets `fetch_first` on a record in hand.
    where = (f"{STATION_NAMES[SOURCE]} station {STATION} · {spot['height']:.0f} m" if STATION
             else f"{MODEL_NAMES[SOURCE]} cell {LATITUDE:+.2f}, {LONGITUDE:+.2f}")
    first = st.session_state.get("fetch_first") or date(date.today().year - 19, 1, 1)
    if SOURCE == "metar":
        note = ("Every report the airport has sent, from the Iowa Environmental Mesonet's "
                "archive; no account needed. It can take a minute.")
    elif SOURCE == "meteoswiss":
        note = ("Every hour the station has measured, from MeteoSwiss open data; "
                "no account needed. It can take a minute or two."
                + ("" if meteoswiss_stations()["has_temp"].get(STATION, True) else
                   " It measures no air temperature, so it borrows it from the nearest "
                   "station that does, corrected for the difference in height."))
    elif SOURCE == "arome":
        note = (f"Every hour since {AROME_SINCE:%d %B %Y} of Météo-France's 1.5 km model, "
                "as Open-Meteo archives it; no account needed, a year at a time. Mind that "
                "it is the first hours of each forecast run stitched together, not a "
                "reanalysis, and only a few years of them: one odd summer carries weight.")
    else:
        note = (f"Every hour from {first:%B %Y} to today, prepared on Copernicus' side; "
                + ("every year since 1940 can follow later, from Years in the filter. "
                   if first.year > 1940 else "")
                + "Nothing to do but wait: this can take a while.")
    show, clear = loading_screen(f"{where} · {LOCAL_TZ}. {note}")
    what = {"metar": "airport", "meteoswiss": "station", "arome": "cell", "era5": "location"}[SOURCE]
    hint = ("If this is the deployed app, add your Copernicus token under Settings → Secrets "
            "(see .streamlit/secrets.toml.example)." if SOURCE == "era5" else None)
    fetch_now(lambda: fetch_here(spot, show, first), what, clear, hint)

df = load_wind(dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ)
data_start = df.index.min().date()
data_end = df.index.max().date()
HAS_TEMP = "temp_c" in df.columns and bool(df["temp_c"].notna().any())


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


def what_to_expect(rating, activity, wind, lasting, session_text, missing_temp,
                   shift=None, feels=None):
    """Turn the numbers into the sentences you would actually say out loud."""
    groove = rating["groove_pct"]
    low = wind[0]
    verdict = (
        "a reliable spot" if groove >= 55 else
        "worth the trip in season" if groove >= 35 else
        "a sometimes spot" if groove >= 20 else
        "usually too light"
    )
    lines = [
        f"For **{activity.lower()}** this is **{verdict}**. Your wind "
        f"({wind_words(wind)}) holds {lasting} h in a row{rule_words(shift, feels)} on "
        f"**{groove:.0f}% of days** during {session_text}.",
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
            f"On days you would sail it averages **{warmth['on_good_days']:.0f}°C**: "
            f"{kit_for(warmth['on_good_days'])}. It ranges from "
            f"{warmth['coolest']:.0f}°C in {calendar.month_name[warmth['coolest_month']]} "
            f"to {warmth['warmest']:.0f}°C in {calendar.month_name[warmth['warmest_month']]}."
        )
    else:
        lines.append(f"_{missing_temp}_")
    return lines


@st.cache_data(show_spinner=False)
def rate_location(path, mtime, zone, years_back, activity, wind, lasting, hours,
                  shift=None, feels=None):
    """Score a spot for one activity, over its own last `years_back` years
    and your hours on the water. A day counts as it does in explore and the
    compare view: your wind holds `lasting` hours in a row, swinging no more
    than `shift` degrees and feeling at least `feels` °C. The weights are a
    judgement call — the four parts are shown so you can disagree."""
    frame = load_wind(path, mtime, zone)
    first_year = lookback_start(frame, years_back)
    usable = frame[frame.index.year >= first_year]
    usable = usable[hour_mask(usable.index, hours)]
    knots = usable["speed"] * MS_TO_KNOTS

    low = wind[0]
    # A big day reaches the third speed band. The top of the range is the
    # wrong mark: reaching it means too much wind, and ERA5's smoothed hourly
    # winds almost never get there.
    strong = ACTIVITIES[activity]["ladder"][-2]
    by_day = knots.resample("1D")
    spread = (by_day.std() / by_day.mean()).dropna()

    good = good_days(path, mtime, zone, years_back, wind, lasting, hours, shift, feels)
    groove = good.mean() * 100
    too_light = (by_day.max().dropna() < low).mean() * 100
    big = (by_day.max().dropna() >= strong).mean() * 100
    steadiness = max(0.0, 1 - float(spread.mean()) / GUSTY_CV)

    per_month = good.groupby(good.index.month).mean() * 100
    good_months = int((per_month >= 40).sum())
    per_hour = knots.groupby(knots.index.hour).mean()
    direction = wind_direction(usable["u10"], usable["v10"])

    parts = {
        "Days with your wind": (
            min(groove / 6, 10),
            f"{groove:.0f}% of days hold {wind_words(wind)} for {lasting} h in a row"
            f"{rule_words(shift, feels)}",
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

    start_hour, end_hour = best_window(knots)

    warmth = None
    if "temp_c" in usable.columns and usable["temp_c"].notna().any():
        temps = usable["temp_c"]
        monthly = temps.groupby(temps.index.month).mean().reindex(range(1, 13))
        # Your days are local calendar days, without a zone.
        on_groove_days = temps[temps.index.tz_localize(None).normalize().isin(good.index[good])]
        warmth = {
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


def shift_degrees(choice):
    """The most the wind may swing, as the filter keeps it: degrees, or None for any."""
    return None if choice == "Any" else int(choice)


def shift_label(choice):
    return "Any" if choice == "Any" else f"{choice}°"


def shift_words(shift):
    """', shifting at most 30°', or nothing where the wind may swing as it likes."""
    return "" if shift is None else f", shifting at most {shift}°"


def feels_degrees(choice):
    """The least it may feel like, as the filter keeps it: °C, or None for any."""
    return None if choice == "Any" else int(choice)


def feels_label(choice):
    return "Any" if choice == "Any" else f"{choice} °C"


def feels_words(feels):
    """', feeling at least 10 °C', or nothing where cold is no bar."""
    return "" if feels is None else f", feeling at least {feels} °C"


def rule_words(shift, feels):
    """The limits on top of the wind range, as the captions add them."""
    return shift_words(shift) + feels_words(feels)


def feels_like(celsius, metres_per_second):
    """What the air feels like in the wind: the wind chill index of
    Environment Canada and the US National Weather Service, which holds
    below 10 °C with wind over 4.8 km/h; warmer or calmer, the air
    temperature itself. Wind at 10 m, as the records give it."""
    kmh = np.asarray(metres_per_second, dtype=np.float32) * 3.6
    air = np.asarray(celsius, dtype=np.float32)
    chill = 13.12 + 0.6215 * air + (0.3965 * air - 11.37) * np.power(kmh, 0.16)
    return np.where((air <= 10) & (kmh > 4.8), chill, air)


def circular_spread(degrees, length):
    """For each hour, the narrowest arc that holds the directions of the
    `length` hours ending there, in degrees: 0 for a wind that never moved,
    180 for one that turned right round. The first `length` - 1 hours have
    no full window and read as not-a-number."""
    values = np.asarray(degrees, dtype=np.float32) % 360
    if len(values) < length:
        return np.full(len(values), np.nan, dtype=np.float32)
    windows = np.sort(np.lib.stride_tricks.sliding_window_view(values, length), axis=1)
    # The gaps between neighbours round the compass, the last one wrapping
    # back to the first: the arc that holds them all is what the widest gap leaves.
    gaps = np.diff(windows, axis=1, append=windows[:, :1] + 360)
    return np.concatenate([np.full(length - 1, np.nan, dtype=np.float32), 360 - gaps.max(axis=1)])


def lasting_days(flags, length, direction=None, shift=None):
    """Per day: do the flagged hours run unbroken for at least `length`
    hours, with the wind swinging no more than `shift` degrees over those
    very hours where a limit is set? `direction` is where the wind comes
    from, hour by hour, aligned with `flags`."""
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
    # How many hours of the run end at this one: `length` or more, and the
    # last `length` hours are all flagged, consecutive and on the same day.
    held = flags.groupby(run).cumsum().ge(length)
    if shift is not None:
        # The same `length` hours, as the wind swung through them.
        held &= pd.Series(circular_spread(direction, length) <= shift, index=flags.index)
    return held.groupby(day).any()


def flag_hours(frame, first_year, wind, dates, feels=None):
    """The record from `first_year` on, cut to your dates (None for all of
    them), with each hour flagged when its wind is in your range and, where
    `feels` is set, the air feels at least that warm in it. An hour with no
    air temperature on record passes rather than failing. A window over New
    Year starts with the first one to open in `first_year`, so none is
    counted with only its January half."""
    opened = window_year(frame.index, dates)
    frame = frame[(opened >= first_year) & in_dates(frame.index, dates)]
    good = (frame["speed"] * MS_TO_KNOTS).between(*wind)
    if feels is not None and "temp_c" in frame:
        warm = feels_like(frame["temp_c"], frame["speed"]) >= feels
        good &= pd.Series(warm | frame["temp_c"].isna().to_numpy(), index=frame.index)
    return frame, good


@st.cache_data(show_spinner=False, max_entries=256)
def good_days(path, mtime, zone, years_back, wind, lasting, hours, shift=None, feels=None):
    """Per local day over a spot's own last `years_back` years: did your
    hours on the water hold your wind `lasting` hours in a row, swinging
    no more than `shift` degrees and feeling at least `feels` °C meanwhile?"""
    frame = load_wind(path, mtime, zone)
    frame, good = flag_hours(frame, lookback_start(frame, years_back), wind, None, feels)
    on_water = hour_mask(frame.index, hours)
    direction = wind_direction(frame["u10"], frame["v10"])[on_water].to_numpy()
    return lasting_days(good[on_water], lasting, direction, shift)


if "mode" not in st.session_state:
    st.session_state["mode"] = None


# --- Start screen ---------------------------------------------------------
# Your spot beside its map, then the sport, then three ways in, each card
# with a small moving drawing of what it answers.

CHOICES = [
    ("rating", "Rate this spot", "One score out of ten for what you sail.", "Rate it"),
    ("explore", "All the stats for this spot",
     "Windy days by month, the wind rose, every year and the numbers.", "Explore"),
    ("window", "Compare", "Compare this spot with other spots on your dates, and find when to go.",
     "Compare"),
]
# Knots across the sport's ruler: enough for the kite's top end.
GROOVE_SCALE = 40


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
    """Compare: two weeks, a frame stepping along them."""
    return '<div class="days">' + "".join(
        f'<span class="day"><small>{letter}</small><i></i></span>' for letter in "MTWTFSS" * 2
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


def step_label(text):
    st.html(f'<div class="step-label">{text}</div>')


if st.session_state["mode"] is None:
    spot_col, map_col = st.columns([7, 5], gap="large", vertical_alignment="center")
    with spot_col:
        st.html(
            '<div class="hero">'
            f'<div class="hero-title">{html.escape(LOCATION_NAME)}</div>'
            f'<div class="hero-facts">{coords_label(LATITUDE, LONGITUDE)} · '
            f"{data_start.year}–{data_end.year}, hourly · {LOCAL_TZ} · "
            + (f"{STATION_NAMES[spot['source']]} {STATION}, {spot['height']:.0f} m"
               if STATION else MODEL_NAMES[SOURCE])
            + "</div></div>"
        )
        step_label("What are you sailing?")
        st.session_state["activity"] = pick(
            st.segmented_control(
                "What are you sailing?", list(ACTIVITIES),
                default=st.session_state["activity"],
                format_func=lambda name: f"{ACTIVITIES[name]['icon']} {name}",
                key="activity_pick", label_visibility="collapsed",
            ),
            st.session_state["activity"],
        )
        chosen = st.session_state["activity"]
        groove_low, groove_high = ACTIVITIES[chosen]["groove"]
        if can_score(chosen):
            note = f"Counts {groove_low}–{groove_high} kt as sailable. Change it in the filter."
        else:
            note = "Counts every day, so there is nothing to rate. Pick a sport to rate the spot."
        st.html(groove_ruler(groove_low, groove_high, note))
    with map_col:
        locator_map(LATITUDE, LONGITUDE, key="start_map", height=340, cell=CELL)

    step_label("What do you want to know?")
    for column, (target, title, blurb, action) in zip(
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
                f'<div class="choice-text"><div class="choice-title">{title}</div>'
                f'<div class="choice-blurb">{blurb}</div>{lock_note}</div>'
            )
            if st.button(action, key=f"pick_{target}", width="stretch", disabled=locked):
                st.session_state["mode"] = target
                st.session_state.pop("last_mode", None)
                # The compare view starts from its first question every time,
                # with this spot spelled out under the table.
                st.session_state["trip_step"] = 0
                st.session_state.pop("trip_shown", None)
                st.rerun()
    small_print()
    st.stop()


# --- The filter and the trip's dates -------------------------------------
# The compare view asks one question, your dates; the rest of its filter is
# in the sidebar beside the answer, and the spots it compares are picked in
# a window of their own. The rating has the same sidebar, and the settings share their
# keys, so one given in either view is already there in the other.

# Where the trip's dates start before you pick: these days of next month.
TRIP_DAYS = (10, 16)
# A trip is at most this many days, as long as the calendar lets you pick:
# drawn year by year, a dot a day.
YEAR_ROWS_UP_TO = 62


def coming_dates(first_day, last_day):
    """Those days of next month, as a window: where the calendar starts."""
    month = date.today().month % 12 + 1
    return (month, first_day), (month, last_day)


# How long your wind has to hold: a button for each of one to five hours,
# and Other for a length of your own.
LASTING_BUTTONS = 5


def ask_lasting(place, hours):
    """How many hours in a row your wind has to hold, drawn in `place`. No
    longer than your `hours` on the water: no day could hold a longer run
    inside them. Kept as one number under "lasting", whether picked or typed."""
    longest = hours[1] - hours[0] + 1
    picks = [*range(1, min(LASTING_BUTTONS, longest) + 1)]
    picks += ["other"] if longest > LASTING_BUTTONS else []
    held = kept("lasting", DEFAULT_LASTING)
    if held > longest:
        held = longest
        st.toast(f"In a row is now {held} h: your hours on the water are {held} h long.",
                 icon=":material/schedule:")
    # Where the kept choice no longer fits, the widgets start from the number
    # kept, and reset to it themselves.
    if st.session_state.get("kept_lasting_pick", DEFAULT_LASTING) not in picks:
        st.session_state["kept_lasting_pick"] = held if held <= LASTING_BUTTONS else "other"
    if st.session_state.get("kept_lasting_own", 0) > longest:
        st.session_state["kept_lasting_own"] = longest
    choice = remembered(
        place.segmented_control, "In a row", picks, required=True,
        format_func=lambda h: "Other" if h == "other" else f"{h} h",
        key="lasting_pick", default=DEFAULT_LASTING, start="default",
    )
    if choice == "other":
        choice = remembered(
            place.number_input, "Hours in a row", min_value=1, max_value=longest, step=1,
            key="lasting_own", default=LASTING_BUTTONS + 1, width=200,
            help=f"Up to {longest} h, the length of your hours on the water.",
        )
    st.session_state["kept_lasting"] = int(choice)


# The dates as a calendar you click: your first day, then your last, the
# range following the pointer in between. A component of our own, as
# Streamlit's date picker is a small field with a pop-up, and always has a
# year. This one has none: every year on record is read on the same days,
# so it shows twelve months that run on from December into January, with
# no years and no weekdays, which change from year to year. It draws as
# many months side by side as there is room for, and only builds them anew
# when you turn the page: hovering and clicking just repaint the days.
CALENDAR_CSS = """
.cal { --orange: #2dd4bf; color: #e7ebf3; font-variant-numeric: tabular-nums; }
.cal-top { display: flex; align-items: center; gap: .8rem; margin-bottom: 1rem; }
.cal-summary { flex: 1; min-width: 0; text-align: center; }
.cal-summary b {
    display: block; font-size: clamp(1.25rem, 2.6vw, 1.7rem); font-weight: 800;
    letter-spacing: -.03em; line-height: 1.2;
}
.cal-summary span { display: block; margin-top: .15rem; font-size: .85rem; color: #8a94a8; }
.cal-summary.far span { color: #fbbf24; }
.nav {
    display: grid; place-items: center; width: 2.6rem; height: 2.6rem; flex: none;
    border: 1px solid #273043; border-radius: 50%; background: #0f131b; color: #e7ebf3;
    cursor: pointer; transition: border-color .2s ease, background .2s ease;
}
.nav:hover { border-color: var(--orange); background: #161b26; }
.nav svg { width: 1.1rem; height: 1.1rem; }
.months { display: grid; grid-template-columns: repeat(var(--count), minmax(0, 1fr)); gap: 1.6rem; }
.month { min-width: 0; }
.month-name { margin: 0 0 .6rem .2rem; font-size: 1.05rem; font-weight: 800; letter-spacing: -.01em; }
.days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
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
.day.today span::after {
    content: ""; position: absolute; bottom: .3rem; left: 50%; width: 4px; height: 4px;
    margin-left: -2px; border-radius: 50%; background: #7dd3fc;
}
/* The range as one band across each week, ends rounded at the picked days. */
.day.in::before {
    content: ""; position: absolute; inset: .2rem 0; background: rgba(45, 212, 191, .17);
}
.day.in.rs::before, .day.lo::before { border-top-left-radius: 1.2rem; border-bottom-left-radius: 1.2rem; }
.day.in.re::before, .day.hi::before { border-top-right-radius: 1.2rem; border-bottom-right-radius: 1.2rem; }
.day.lo::before { left: .25rem; }
.day.hi::before { right: .25rem; }
.day.lo span, .day.hi span { background: var(--orange); color: #0a0d14; font-weight: 800; }
.day.lo.today span::after, .day.hi.today span::after { background: #0a0d14; }
.cal.picking .day.in::before { background: rgba(45, 212, 191, .1); }
.cal.picking .day.hi:not(.lo) span { background: rgba(45, 212, 191, .55); }
.day.far::before { background: rgba(138, 148, 168, .12) !important; }
.day.far span { background: none !important; color: #5b6478 !important; }
.cal-hint { margin-top: .9rem; text-align: center; font-size: .82rem; color: #8a94a8; }
@keyframes slideFrom { from { opacity: 0; transform: translateX(var(--from)); } }
.months.turn .month { animation: slideFrom .35s cubic-bezier(.2, .7, .3, 1) both; }
"""

CALENDAR_JS = """
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];
const SHORT = MONTHS.map((name) => name.slice(0, 3));
// A year of no year in particular: 365 days, February with 28.
const YEAR = 365;
const LENGTHS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const FIRSTS = LENGTHS.map((_, m) => LENGTHS.slice(0, m).reduce((a, b) => a + b, 0));
const ARROW = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
  stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
// A day is its place in that year, 0 for 1 January; "MM-DD" to and from Python.
const parse = (text) => {
  const [m, d] = text.split("-").map(Number);
  return FIRSTS[m - 1] + Math.min(d, LENGTHS[m - 1]) - 1;
};
const monthOf = (t) => FIRSTS.findLastIndex((first) => first <= t);
const dayOf = (t) => t - FIRSTS[monthOf(t)] + 1;
const text = (t) => `${String(monthOf(t) + 1).padStart(2, "0")}-${String(dayOf(t)).padStart(2, "0")}`;
const said = (t) => `${dayOf(t)} ${SHORT[monthOf(t)]}`;
// Days from a on to b, both counted, running on past New Year.
const ahead = (a, b) => ((b - a) % YEAR + YEAR) % YEAR + 1;
// Two days as a range the shorter way round: its first day and its length.
const between = (a, b) => ahead(a, b) <= ahead(b, a) ? [a, ahead(a, b)] : [b, ahead(b, a)];

export default function (component) {
  const { data, setStateValue, parentElement } = component;
  const root = parentElement.querySelector(".cal");
  let s = root.__state;
  if (!s) {
    s = root.__state = { pending: null, hover: null, view: null, sent: null };
    root.innerHTML = `<div class="cal-top">
        <button class="nav prev" aria-label="Earlier months">${ARROW("M15 5l-7 7 7 7")}</button>
        <div class="cal-summary" aria-live="polite"><b></b><span></span></div>
        <button class="nav next" aria-label="Later months">${ARROW("M9 5l7 7-7 7")}</button>
      </div><div class="months"></div>
      <div class="cal-hint">Click your first day, then your last. One day? Click it twice.</div>`;
    root.addEventListener("click", (event) => {
      const nav = event.target.closest(".nav");
      if (nav) {
        // December turns on to January, and back.
        const step = nav.classList.contains("next") ? 1 : -1;
        s.view = (s.view + step + 12) % 12;
        build(step > 0 ? "28px" : "-28px");
        return;
      }
      const day = event.target.closest(".day");
      if (!day) return;
      const t = Number(day.dataset.t);
      const [start, days] = s.pending === null ? [t, 1] : between(s.pending, t);
      if (s.pending === null || days > s.data.longest) {
        // A first day, or too long to be a trip: start again from here.
        s.pending = t;
        s.hover = t;
      } else {
        s.start = start;
        s.days = days;
        s.pending = null;
        const range = { start: text(start), end: text((start + days - 1) % YEAR) };
        s.sent = `${range.start}|${range.end}`;
        s.send("range", range);
      }
      paint();
    });
    root.addEventListener("mouseover", (event) => {
      const day = event.target.closest(".day");
      if (s.pending === null || !day) return;
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
  s.today = parse(data.today);
  // Take what Python holds, unless a pick is half made.
  const held = `${data.start}|${data.end}`;
  if (held !== s.sent) {
    s.sent = held;
    if (s.pending === null) {
      s.start = parse(data.start);
      s.days = ahead(s.start, parse(data.end));
      s.view = null;
    }
  }
  if (s.view === null) s.view = monthOf(s.start);
  build(null);

  function count() {
    const width = root.getBoundingClientRect().width || 900;
    return width >= 860 ? 3 : width >= 540 ? 2 : 1;
  }

  function build(motion) {
    s.count = count();
    const months = root.querySelector(".months");
    months.style.setProperty("--count", s.count);
    let markup = "";
    for (let m = s.view; m < s.view + s.count; m++) {
      const month = m % 12, length = LENGTHS[month];
      let cells = "";
      // Rows of seven from the 1st, with no weekdays above them.
      for (let d = 1; d <= length; d++) {
        const t = FIRSTS[month] + d - 1;
        const kinds = ["day"];
        if (d % 7 === 1) kinds.push("rs");
        if (d % 7 === 0 || d === length) kinds.push("re");
        if (t === s.today) kinds.push("today");
        cells += `<button class="${kinds.join(" ")}" data-t="${t}"
          aria-label="${d} ${MONTHS[month]}"><span>${d}</span></button>`;
      }
      markup += `<div class="month"><div class="month-name">${MONTHS[month]}</div>
        <div class="days">${cells}</div></div>`;
    }
    months.innerHTML = markup;
    months.classList.remove("turn");
    if (motion) {
      void months.offsetWidth;
      months.classList.add("turn");
      months.style.setProperty("--from", motion);
    }
    paint();
  }

  function paint() {
    const picking = s.pending !== null;
    const [lo, days] = picking ? between(s.pending, s.hover) : [s.start, s.days];
    const hi = (lo + days - 1) % YEAR;
    const far = picking && days > s.data.longest;
    root.classList.toggle("picking", picking);
    for (const day of root.querySelectorAll(".day")) {
      const t = Number(day.dataset.t);
      const inside = ahead(lo, t) <= days;
      day.classList.toggle("in", inside);
      day.classList.toggle("lo", t === lo);
      day.classList.toggle("hi", t === hi);
      day.classList.toggle("far", far && inside);
    }
    const summary = root.querySelector(".cal-summary");
    summary.classList.toggle("far", far);
    summary.querySelector("b").textContent = days === 1 ? said(lo) : `${said(lo)} → ${said(hi)}`;
    summary.querySelector("span").textContent = far
      ? `Up to ${s.data.longest} days: pick a nearer last day`
      : picking && days === 1 ? "Now click your last day"
      : `${days} day${days === 1 ? "" : "s"}`;
  }
}
"""

calendar_picker = st.components.v2.component(
    "date_range_calendar", html='<div class="cal"></div>', css=CALENDAR_CSS, js=CALENDAR_JS,
)


def ask_dates(key, default, longest=365):
    """A window of days on the calendar, at most `longest` long, kept under
    `key`. The calendar's own state only lives while it is drawn, so it
    starts from the kept window each time it comes back."""
    def sent(day):
        return f"{day[0]:02d}-{day[1]:02d}"

    held = kept(key, default)
    today = date.today()
    result = calendar_picker(
        key=f"{key}_calendar",
        data={"start": sent(held[0]), "end": sent(held[1]), "longest": longest,
              "today": sent((today.month, today.day))},
        on_range_change=lambda: None,
    )
    picked = (result or {}).get("range")
    if picked:
        held = tuple(tuple(int(part) for part in picked[end].split("-")) for end in ("start", "end"))
    st.session_state[f"kept_{key}"] = held
    return held


def ask_trip_dates(title, default):
    """The compare view's one question, your dates: the run stops here until
    Show me, and Change dates beside the answer's title comes back to it."""
    if st.session_state.setdefault("trip_step", 0):
        return
    with st.container(key="trip_page"):
        st.html(f'<div class="view-title">{title}</div>'
                '<div class="view-sub">Pick your dates. Then compare this spot with other '
                "spots on them, or see when to go.</div>")
        with st.container(key="asking"):
            st.html('<div class="asking">When are you there?</div>')
            ask_dates("trip_dates", default, YEAR_ROWS_UP_TO)
            st.caption(DATES_HINT)
            if st.button("Show me", type="primary", icon=":material/arrow_forward:",
                         key="trip_next"):
                st.session_state["trip_step"] = 1
                st.rerun()
    small_print()
    st.stop()


def settings():
    """The sidebar inside a view: what you sail, then the filter the view
    counts days with: your wind, your time, and the comfort where the record
    has the air temperature. Returns what you sail."""
    side = st.sidebar
    side.header("Filter")
    side.caption("A day counts when all of this holds at once, for your hours in a row.")
    activity = side.selectbox(
        "What you sail", list(ACTIVITIES),
        index=list(ACTIVITIES).index(st.session_state["activity"]), key="activity_side",
    )
    st.session_state["activity"] = activity

    side.subheader("Your wind")
    remembered(side.slider, "Wind", 0, WIND_TOP, format="%d kt",
               key=f"wind_{activity}", default=ACTIVITIES[activity]["groove"])
    remembered(side.select_slider, "May shift up to", SHIFT_STEPS, format_func=shift_label,
               key="shift", default=DEFAULT_SHIFT,
               help="Over your hours in a row, where the wind comes from stays within "
               "this many degrees: 30° is a steady breeze, 90° takes in a sea breeze "
               "swinging round. Any lets it back and veer as it likes.")

    side.subheader("Your time")
    hours = remembered(side.slider, "On the water", 0, 23, format="%d:00",
                       key="hours", default=DEFAULT_HOURS)
    side.caption("Every hour of the day" if hours == (0, 23)
                 else f"{hours[0]:02d}:00–{hours[1]:02d}:59 local time")
    ask_lasting(side, hours)
    remembered(side.segmented_control, "Years", list(LOOKBACK), required=True,
               key="years", default=DEFAULT_YEARS, start="default")
    if SOURCE == "era5" and data_start.year > 1940:
        # ERA5 came with its last twenty years; the rest is one click away.
        if side.button("Every year since 1940", icon=":material/history:", type="tertiary",
                       help=f"This record starts in {data_start.year}. Fetch the whole of "
                       "it, 1940 to today, from Copernicus; that can take a while, and "
                       "the app waits for it."):
            st.session_state["fetch_first"] = date(1940, 1, 1)
            st.rerun()
    if HAS_TEMP:
        side.subheader("Comfort")
        remembered(side.select_slider, "Feels like at least", FEELS_STEPS,
                   format_func=feels_label, key="feels", default=DEFAULT_FEELS,
                   help="Wind chill: what the air feels like in the wind, by the formula "
                   "of Environment Canada and the US National Weather Service, which "
                   "holds below 10 °C with wind over 4.8 km/h; warmer or calmer, it is "
                   "the air temperature itself. Every hour in a row has to feel at least "
                   "this warm. Any: cold is no bar.")
    return activity


# --- Shared by explore and the compare view ------------------------------

POINT_WORDS = {"N": "north", "E": "east", "S": "south", "W": "west"}

# The styles of explore and the compare view, sent with either view.
ANSWER_CSS = """
<style>
/* Room for the calendar: three months side by side. */
.st-key-trip_page { max-width: 1180px; }
.st-key-trip_main { max-width: 1000px; }
.chapter-lead {
    margin-top: 1.2rem; font-size: clamp(1.3rem, 2.4vw, 1.7rem); font-weight: 700;
    line-height: 1.2; letter-spacing: -.01em; color: #e7ebf3;
}
.chapter-sub {
    max-width: 66ch; margin: .45rem 0 1.3rem; font-size: .95rem; line-height: 1.55;
    color: #a7b1c4;
}
.chapter-sub b { color: #e7ebf3; }
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; margin-top: 1rem; font-size: .8rem; color: #8a94a8; }
.legend span::before {
    content: ""; display: inline-block; width: .6rem; height: .6rem; margin-right: .45rem;
    border-radius: 50%; background: var(--c);
}

/* Days as dots: a row a year. */
/* Dots stay day-sized however few there are, and shrink only to fit. */
.dots { display: grid; gap: 5px; max-width: 760px; }
.dot-row {
    display: grid; grid-template-columns: 2.6rem minmax(0, max-content) 2rem;
    justify-content: start; align-items: center; gap: .7rem;

}
.dot-label { font-size: .78rem; font-weight: 600; color: #8a94a8; font-variant-numeric: tabular-nums; }
.dot-row.marked .dot-label, .dot-row.marked .dot-count { color: #2dd4bf; }
.dot-cells { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 16px)); gap: 3px; }
.dot-cells i { aspect-ratio: 1; border-radius: 50%; background: #181d29; }
.dot-cells i.g { background: #14b8a6; }
.dot-count { font-size: .8rem; font-weight: 700; text-align: right; color: #e7ebf3; font-variant-numeric: tabular-nums; }


/* The compare view's page, in one column: where and when on top, then the
   one big button to compare or, once you have, the spots side by side,
   then when to go. */
.trip-when { display: flex; flex-wrap: wrap; align-items: baseline; gap: .2rem .8rem; }
.trip-when b { font-size: 1.2rem; font-weight: 700; color: #e7ebf3; }
.trip-when span { font-size: .88rem; color: #8a94a8; }
.trip-answer { margin-top: .2rem; font-size: 1.08rem; line-height: 1.45; color: #a7b1c4; }
.trip-answer b { color: #2dd4bf; }
.stage-rule { margin-top: .35rem; font-size: .86rem; line-height: 1.5; color: #8a94a8; }
.stage-rule b { color: #a7b1c4; }
.stage-verdict {
    margin-top: .2rem; font-size: 1.05rem; font-weight: 600; line-height: 1.45; color: #e7ebf3;
}
.stage-verdict b { color: #2dd4bf; }
.st-key-trip_cta, .st-key-trip_compared {
    margin-top: 1.1rem; padding: 1.4rem 1.5rem !important; border: 1px solid #2dd4bf;
    border-radius: 12px; background: #0f1a1d;
}
.st-key-trip_compared { border-color: #273043; background: #0c1017; }
.cta-title {
    font-size: clamp(1.35rem, 2.4vw, 1.75rem); font-weight: 800; line-height: 1.15;
    letter-spacing: -.02em; color: #e7ebf3;
}
.cta-sub { margin-top: .4rem; max-width: 64ch; font-size: .98rem; line-height: 1.55; color: #a7b1c4; }
/* The button that starts a comparison: the largest on the page. */
.st-key-trip_open_compare button { min-height: 3.2rem; padding: 0 1.7rem; }
.st-key-trip_open_compare button p { font-size: 1.08rem; }
.st-key-map_source [data-testid="stButtonGroup"] > div { flex-wrap: wrap; }
.scroll-cue {
    display: inline-flex; align-items: center; gap: .5rem; margin-top: 1rem;
    font-size: .95rem; font-weight: 600; color: #7dd3fc; text-decoration: none;
}
.scroll-cue:hover { color: #2dd4bf; }
.scroll-cue svg { width: 1.1rem; height: 1.1rem; flex: none; }

/* The comparison: a row a spot, best first, the same figure in the same
   column all the way down, so the differences read at a glance. */
.st-key-cmp { margin-top: 1rem; }
.cmp-head, .cmp-row {
    display: grid; align-items: center; gap: .4rem 1.2rem;
    grid-template-columns: minmax(0, 1.7fr) minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, .8fr) minmax(0, 1fr);
}
.cmp-head {
    padding: 0 1rem; font-size: .68rem; letter-spacing: .1em; text-transform: uppercase;
    color: #8a94a8;
}
.cmp-row {
    padding: .85rem 1rem; border: 1px solid #273043; border-radius: 10px; background: #10141c;
    transition: border-color .15s ease, background-color .15s ease;
}
/* The spot spelled out under the table, edged in teal. */
.cmp-row.shown { border-color: #2dd4bf; background: #0f1a1d; box-shadow: 0 0 0 1px #2dd4bf; }
/* A row is a button: an invisible one over the whole of it takes the click,
   its words left for screen readers. */
[class*="st-key-cmprow_"] { position: relative; }
[class*="st-key-cmprow_"]:hover .cmp-row:not(.shown) { border-color: #4b5770; background: #131924; }
[class*="st-key-cmprow_"]:has(button:focus-visible) .cmp-row { border-color: #7dd3fc; }
[class*="st-key-cmpshow_"] { position: absolute; inset: 0; z-index: 2; width: auto !important; }
[class*="st-key-cmpshow_"] .stButton, [class*="st-key-cmpshow_"] button { width: 100%; height: 100%; }
[class*="st-key-cmpshow_"] button {
    border-radius: 10px; background: transparent !important; border: 0 !important;
    box-shadow: none !important;
}
[class*="st-key-cmpshow_"] button > div {
    position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%);
}
.cmp-spot { min-width: 0; }
.cmp-spot b {
    display: block; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
    font-size: 1.02rem; color: #e7ebf3;
}
.cmp-spot small {
    display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin-top: .2rem;
    font-size: .76rem; color: #8a94a8;
}
.tag {
    padding: .05rem .45rem; border-radius: 999px; border: 1px solid #2dd4bf;
    font-weight: 700; color: #2dd4bf;
}
.tag.quiet { border-color: #3a4558; color: #a7b1c4; }
.cmp-days .count { font-size: .85rem; color: #a7b1c4; }
.cmp-days .count b {
    font-size: 1.7rem; font-weight: 800; letter-spacing: -.03em; color: #e7ebf3;
    font-variant-numeric: tabular-nums;
}
.cmp-row.best .cmp-days .count b { color: #2dd4bf; }
.meter { display: block; height: 6px; margin-top: .35rem; border-radius: 3px; background: #1e2533; overflow: hidden; }
.meter i { display: block; height: 100%; background: #14b8a6; }
.delta { display: block; margin-top: .3rem; font-size: .76rem; font-weight: 700; color: #8a94a8; }
.delta.up { color: #2dd4bf; }
.delta.down { color: #fb7185; }
.cmp-cell b {
    display: block; font-size: 1.05rem; font-weight: 700; color: #e7ebf3;
    font-variant-numeric: tabular-nums; white-space: nowrap;
}
.cmp-cell small { display: block; margin-top: .1rem; font-size: .76rem; line-height: 1.3; color: #8a94a8; }
.cmp-from { display: flex; align-items: center; gap: .55rem; }
.wind-arrow { width: 1.5rem; height: 1.5rem; flex: none; color: #7dd3fc; transform: rotate(var(--to)); }
.cmp-none { grid-column: 2 / -1; font-size: .9rem; color: #8a94a8; }
/* On a phone a row is a card: the spot and its days across, the rest two by two. */
@media (max-width: 640px) {
    .cmp-head { display: none; }
    .cmp-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .cmp-spot, .cmp-days, .cmp-none { grid-column: 1 / -1; }
}

/* When to go: a chapter of its own under the comparison, the anchor the
   cue above takes you to. */
.when-title {
    margin-top: 2.6rem; padding-top: 1.8rem; border-top: 1px solid #273043;
    font-size: clamp(1.8rem, 3vw, 2.3rem); font-weight: 800; line-height: 1.1;
    letter-spacing: -.03em; color: #e7ebf3; scroll-margin-top: 4.5rem;
}
.dates-title {
    margin: 2.4rem 0 .8rem; font-size: clamp(1.35rem, 2.4vw, 1.75rem); font-weight: 800;
    line-height: 1.15; letter-spacing: -.02em; color: #e7ebf3;
}
.trip-block { margin-top: .4rem; }
.band {
    display: flex; gap: 2px; max-width: 620px; height: 14px; margin-top: 1rem;
    border-radius: 7px; overflow: hidden; background: #12161f;
}
.legend b { color: #e7ebf3; }

/* The picker: a label over each group, a row a spot with its one button,
   edged in teal once it is in the comparison. */
.pick-label { margin-top: .5rem; font-size: .95rem; font-weight: 700; color: #e7ebf3; }
[class*="st-key-pickrow_"] {
    padding: .55rem .7rem .55rem .9rem !important; border: 1px solid #273043;
    border-radius: 8px; background: #10141c; flex-wrap: nowrap !important;
}
[class*="st-key-pickrow_"][class*="__added"] { border-color: #2dd4bf; background: #0f1a1d; }
[class*="st-key-pickrow_"] > div:first-child { flex: 1 1 auto !important; min-width: 0 !important; }
[class*="st-key-pickrow_"] > div:not(:first-child) { flex: 0 0 auto !important; }
[class*="st-key-pickrow_"] p { margin-bottom: 0; line-height: 1.45; }
[class*="st-key-pickrow_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0; }
/* On a phone the text takes the row, its button a line below. */
@media (max-width: 640px) {
    [class*="st-key-pickrow_"] { flex-wrap: wrap !important; }
    [class*="st-key-pickrow_"] > div:first-child { flex: 1 0 100% !important; }
}
.st-key-pick_foot { margin-top: .6rem; padding-top: .9rem; border-top: 1px solid #273043; }

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
    """Days as dots. Each row: its label, the kind of each day ('g' or ''),
    its count, a hover note, and whether to mark it out."""
    widest = max(len(kinds) for _, kinds, *_ in rows)
    drawn = []
    for label, kinds, count, note, marked in rows:
        cells = [f'<i class="{kind}"></i>' if kind else "<i></i>" for kind in kinds]
        drawn.append(
            f'<div class="dot-row{" marked" if marked else ""}" '
            f'title="{html.escape(note)}"><span class="dot-label">{label}</span>'
            f'<span class="dot-cells">{"".join(cells)}</span>'
            f'<span class="dot-count">{count}</span></div>'
        )
    return f'<div class="dots" style="--cols:{widest}">{"".join(drawn)}</div>'


def water_hours(hours):
    return list(range(hours[0], hours[1] + 1))


def best_hours(pct, hours, length=3):
    """The `length` hours in a row on the water that most often have it."""
    span = water_hours(hours)
    length = min(length, len(span))
    runs = [span[i:i + length] for i in range(len(span) - length + 1)]
    return max(runs, key=lambda run: pct.reindex(run).fillna(0).mean())


# --- Explore: the spot at a glance -----------------------------------------
# What the wind does here all year on one screen, with no filter to set:
# the sport picked on the start screen with its own range and the usual
# hours. Four figures, the windy days month by month beside the wind rose
# of the month you click, then the day, the years, the air and the numbers.

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
    spans = [(start, end + 1)]
    x = alt.X("hour:Q", scale=alt.Scale(domain=[0, 24]), title=None,
              axis=alt.Axis(values=list(range(0, 25, 3)), labelExpr="datum.value + 'h'",
                            ticks=False, domain=False, grid=False))
    layers = [
        alt.Chart(pd.DataFrame(spans, columns=["hour", "until"]))
        .mark_rect(color="#7dd3fc", opacity=0.08).encode(x=x, x2="until:Q")
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
    return alt.Chart(grid).mark_rect(cornerRadius=2, stroke="#0a0d14", strokeWidth=1.5).encode(
        x=alt.X("name:N", sort=MONTHS, title=None,
                axis=alt.Axis(orient="top", labelAngle=0, ticks=False, domain=False)),
        y=alt.Y("year:O", sort="descending", title=None, axis=alt.Axis(ticks=False, domain=False)),
        color=alt.Color(
            "pct:Q", title="% of days", scale=alt.Scale(
                domain=[0, 100], range=["#12161f", "#115e59", "#2dd4bf", "#ccfbf1"]),
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
        line.mark_line(color="#2dd4bf", strokeWidth=2.5, interpolate="monotone"),
        line.mark_circle(color="#2dd4bf", size=80, opacity=1).encode(
            tooltip=[alt.Tooltip("name:N", title="month"),
                     alt.Tooltip("celsius:Q", title="°C", format=".0f"),
                     alt.Tooltip("wear:N", title="what to wear")]),
        line.mark_text(dy=-15, color="#e7ebf3", fontSize=12)
        .encode(text=alt.Text("celsius:Q", format=".0f")),
    ).properties(height=260)


if st.session_state["mode"] == "explore":
    st.html(ANSWER_CSS)
    activity = st.session_state["activity"]
    ladder = ACTIVITIES[activity]["ladder"]
    wind = ACTIVITIES[activity]["groove"]
    hours, lasting = DEFAULT_HOURS, DEFAULT_LASTING
    shift, feels = shift_degrees(DEFAULT_SHIFT), None
    years_back = LOOKBACK[DEFAULT_YEARS]

    recent = df[df.index.year >= lookback_start(df, years_back)]
    knots = recent["speed"] * MS_TO_KNOTS
    direction = wind_direction(recent["u10"], recent["v10"])
    water = hour_mask(recent.index, hours)
    with st.spinner("Counting your days..."):
        days = good_days(dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ,
                         years_back, wind, lasting, hours, shift, feels)
    years_text = f"{recent.index.min().year}–{recent.index.max().year}"
    hours_text = "all day" if hours == (0, 23) else f"{hours[0]:02d}:00–{hours[1]:02d}:59"

    st.html(f'<div class="hero"><div class="hero-title">{html.escape(LOCATION_NAME)}</div></div>')
    st.caption(
        f"{activity}: a day counts when {wind_words(wind)} holds {lasting} h in a row, "
        f"{hours_text} local time · {years_text} · {SOURCE_CREDIT}"
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
        help=f"Days when {wind_words(wind)} holds {lasting} h in a row"
        f"{rule_words(shift, feels)}, {hours_text}, in {years_text}",
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
            rose = draw_rose(direction[here], knots[here], ladder)
            st.pyplot(rose, width="stretch")
            plt.close(rose)
            st.caption(
                f"Mostly from the {compass_words(prevailing_direction(direction[here]))}, "
                f"{knots[here].mean():.0f} kt on average. Check the map: is that way open water?"
            )
        else:
            st.info("No readings in your hours.")
        locator_map(LATITUDE, LONGITUDE, key="explore_map", height=220, cell=CELL)

    st.divider()
    hourly = knots if month is None else knots[recent.index.month == month]
    section(f"{when} through the day",
            "the share of hours that top each speed; shaded, the hours counted")
    st.html(band_legend(ladder))
    st.altair_chart(day_chart(over_bands(hourly, hourly.index.hour, ladder), hours),
                    width="stretch")

    if len(days):
        st.divider()
        section("Year by year", f"the share of days with {wind_words(wind)}, month by month")
        st.altair_chart(year_chart(days), width="stretch")

    if HAS_TEMP:
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

    st.divider()
    section("The numbers", "month by month, over the hours counted")
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
    small_print()
    st.stop()


# --- Compare: your dates, then what to expect ------------------------------
# Your dates, a regatta or a week away, at this spot: you pick the days, and
# the wind you need and your hours on the water come from the filter on the
# left. Then one page with two things on it, one under the other. On top, a
# big button that compares this spot with others on your dates, in its own
# data to start with and in any other at a click; once you have, the spots
# side by side in one table. Below it, when to go: the best days of the year
# for your wind, then your dates in full, here or at the spot you click in
# the table.

DATES_HINT = ("Every year on record is checked on these days. Wind and hours on the water "
              "are set in the filter.")
# How many other spots a trip sets beside this one.
COMPARE_UP_TO = 5
FULL = f"Up to {COMPARE_UP_TO + 1} spots side by side: remove one first."
# Your hours on the water as too light, right and too strong.
BANDS = {
    "light": ("Too light", "#334155"),
    "good": ("In your range", "#14b8a6"),
    "strong": ("Too strong", "#e11d48"),
}
# Every calendar day of a year of no year in particular, 1 January first.
YEAR_DAYS = window_days(((1, 1), (12, 31)))
# The year's curve is drawn over the two weeks or so round each day, the
# nearest counting most, in a bell curve this many days wide either way:
# one day of twenty years is too few to read on its own, and a plain mean
# over a week still jags.
SMOOTH_SPREAD = 3


@st.cache_data(show_spinner=False, max_entries=128)
def trip_outlook(path, mtime, zone, years_back, wind, lasting, hours, dates,
                 shift=None, feels=None):
    """What a spot's record says about your dates: per day whether your wind
    held `lasting` hours in a row on the water, swinging no more than `shift`
    degrees and feeling at least `feels` °C, and over your hours on the water
    how often it was too light, right or too strong, how hard it blew, where
    it came from when it was right, and how warm the air was. "rose" holds
    where it came from and how hard, hour by hour on the water."""
    frame = load_wind(path, mtime, zone)
    frame, good = flag_hours(frame, lookback_start(frame, years_back), wind, dates, feels)
    on_water = hour_mask(frame.index, hours)
    knots = frame.loc[on_water, "speed"] * MS_TO_KNOTS
    heading = wind_direction(frame["u10"], frame["v10"])
    flags = lasting_days(good[on_water], lasting, heading[on_water].to_numpy(), shift)
    right = frame[good & on_water]
    air = frame.loc[on_water, "temp_c"].dropna() if "temp_c" in frame else pd.Series(dtype=float)
    return {
        "days": flags.to_frame("good"),
        "light": float((knots < wind[0]).mean() * 100) if len(knots) else 0.0,
        "strong": float((knots > wind[1]).mean() * 100) if len(knots) else 0.0,
        # The middle half of your hours on the water, and its midpoint.
        "knots": np.percentile(knots, [25, 50, 75]).tolist() if len(knots) else None,
        "direction": wind_direction(right["u10"], right["v10"]).to_numpy(),
        "rose": (heading[on_water].to_numpy(), knots.to_numpy()),
        "hour": good.groupby(frame.index.hour).mean().reindex(range(24)) * 100,
        "air": float(air.mean()) if len(air) else None,
    }


def rounded_days(expected, length):
    """Days with your wind in a typical year: whole from five days up, to a
    tenth below, where a whole number would round a trip away."""
    return round(expected) if length >= 5 else round(expected, 1)


def typical_days(days, length):
    """The days of your dates that bring your wind in a typical year."""
    return rounded_days(float(days["good"].mean()) * length, length)


def wind_bands(outlook):
    """Too light, right and too strong as whole percents that add up to 100."""
    light, strong = round(outlook["light"]), round(outlook["strong"])
    return {"light": light, "good": 100 - light - strong, "strong": strong}


def band_bar(bands):
    """Your hours on the water as one bar, too light to too strong, each
    share named beneath it."""
    parts = "".join(
        f'<i style="width:{share}%;background:{BANDS[kind][1]}"></i>'
        for kind, share in bands.items() if share > 0
    )
    named = "".join(
        f'<span style="--c:{BANDS[kind][1]}">{BANDS[kind][0]} <b>{share}%</b></span>'
        for kind, share in bands.items()
    )
    return f'<div class="band">{parts}</div><div class="legend">{named}</div>'


# --- When to go: the year at this spot ---

@st.cache_data(show_spinner=False, max_entries=64)
def year_chances(path, mtime, zone, years_back, wind, lasting, hours, shift=None, feels=None):
    """Each day of the year, 1 January first: the share of the years on
    record whose day brought your wind, counted as your dates are. 29
    February is left out; a day no year has is read off its neighbours.
    None for a record with no days."""
    days = good_days(path, mtime, zone, years_back, wind, lasting, hours, shift, feels)
    days = days[~((days.index.month == 2) & (days.index.day == 29))]
    if not len(days):
        return None
    share = days.groupby([days.index.month, days.index.day]).mean().reindex(YEAR_DAYS)
    return pd.Series(share.to_numpy(dtype=float)).interpolate(limit_direction="both").to_numpy()


def smoothed(values, spread=SMOOTH_SPREAD):
    """A weighted mean over the days round each, the nearest counting most:
    a bell curve `spread` days wide either way, cut off at three times that,
    running on past New Year."""
    half = 3 * spread
    offsets = np.arange(-half, half + 1)
    weights = np.exp(-offsets ** 2 / (2 * spread ** 2))
    looped = np.concatenate([values[-half:], values, values[:half]])
    return np.convolve(looped, weights / weights.sum(), "valid")


def best_stretch(chances, length):
    """The `length` days in a row, running on past New Year if need be, that
    most often bring your wind: as a window of dates, and how many of its
    days do in a typical year. Picked on the smoothed curve, so one lucky
    day does not decide it, and counted on the record itself."""
    def sums(values):
        looped = np.concatenate([values, values[:length - 1]])
        return np.convolve(looped, np.ones(length), "valid")

    start = int(np.argmax(sums(smoothed(chances))))
    window = (YEAR_DAYS[start], YEAR_DAYS[(start + length - 1) % len(YEAR_DAYS)])
    return window, float(sums(chances)[start])


def season_chart(chances, marks):
    """The year as one curve, the share of years each day brought your wind
    over the two weeks or so round it, with `marks` shaded on it, each
    (window, label, colour). Hover for any day."""
    first = pd.Timestamp("2001-01-01")
    rows = pd.DataFrame({"date": pd.date_range(first, periods=len(YEAR_DAYS)),
                         "pct": smoothed(chances) * 100})
    top = float(min(100, max(20, np.ceil(rows["pct"].max() / 10 + 1) * 10)))
    span = alt.Scale(domain=[alt.DateTime(year=2001, month=1, date=1),
                             alt.DateTime(year=2002, month=1, date=1)])

    def at(field, axis=None):
        return alt.X(f"{field}:T", title=None, scale=span, axis=axis)

    month_axis = alt.Axis(format="%b", tickCount={"interval": "month", "step": 1},
                          labelAlign="left", ticks=False, domain=False, grid=False)
    layers = []
    for row, (window, label, colour) in enumerate(marks):
        start, end = YEAR_DAYS.index(window[0]), YEAR_DAYS.index(window[1])
        # A window over New Year is shaded in two pieces, one at each end.
        pieces = [(start, end + 1)] if start <= end else [(start, len(YEAR_DAYS)), (0, end + 1)]
        spans = pd.DataFrame([{"start": first + pd.Timedelta(days=lo), "end": first + pd.Timedelta(days=hi)}
                              for lo, hi in pieces])
        layers.append(alt.Chart(spans).mark_rect(color=colour, opacity=0.16)
                      .encode(x=at("start"), x2="end:T"))
        # Its name along the top, at the longer piece, kept inside the chart in December.
        lo, hi = max(pieces, key=lambda piece: piece[1] - piece[0])
        late = lo > 300
        named = pd.DataFrame([{"at": first + pd.Timedelta(days=hi if late else lo), "text": label}])
        layers.append(
            alt.Chart(named).mark_text(align="right" if late else "left", baseline="top",
                                       dx=-6 if late else 6, dy=4 + 16 * row, fontSize=11,
                                       fontWeight=600, color="#a7b1c4")
            .encode(x=at("at"), y=alt.value(0), text="text:N")
        )
    curve = alt.Chart(rows).encode(
        x=at("date", month_axis),
        y=alt.Y("pct:Q", title="Years with your wind", scale=alt.Scale(domain=[0, top]),
                axis=alt.Axis(tickCount=4, labelExpr="datum.value + '%'", domain=True,
                              domainColor="#4b5770", ticks=True, tickColor="#4b5770", tickSize=4,
                              gridColor="#1e2533", titleColor="#8a94a8",
                              titleFontWeight="normal", titlePadding=10)),
    )
    hover = alt.selection_point(name="day", on="pointerover", nearest=True, fields=["date"],
                                empty=False, clear="pointerout")
    layers += [
        curve.mark_area(color="#2dd4bf", opacity=0.14, interpolate="monotone"),
        curve.mark_line(color="#2dd4bf", strokeWidth=2, interpolate="monotone"),
        alt.Chart(rows).mark_rule(color="#8a94a8").encode(
            x=at("date"), opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
            tooltip=[alt.Tooltip("date:T", title="Day", format="%-d %B"),
                     alt.Tooltip("pct:Q", title="% of years with your wind", format=".0f")],
        ).add_params(hover),
        curve.mark_circle(size=64, color="#2dd4bf", stroke="#0a0d14", strokeWidth=2, opacity=1)
        .encode(opacity=alt.condition(hover, alt.value(1), alt.value(0))),
    ]
    return alt.layer(*layers).properties(height=240)


def use_dates(dates):
    """Plan for these dates instead: the calendar starts from them next time."""
    st.session_state["kept_trip_dates"] = dates


# --- Comparing: the spots beside this one ---

def add_compare(key):
    """A spot joins the comparison while there is room for it."""
    chosen = st.session_state.get("trip_compare", [])
    if key and key not in chosen and len(chosen) < COMPARE_UP_TO:
        st.session_state["trip_compare"] = [*chosen, key]


def drop_compare(key):
    st.session_state["trip_compare"] = [
        other for other in st.session_state.get("trip_compare", []) if other != key
    ]


def clear_compare():
    st.session_state["trip_compare"] = []


def compared_keys(spots, here):
    """The spots in the comparison that have a record here, this trip's own
    spot `here` left out, at most COMPARE_UP_TO of them."""
    return [key for key in st.session_state.get("trip_compare", [])
            if key in spots and key != here][:COMPARE_UP_TO]


def compare_stored(candidate):
    """A spot already on disk joins the comparison, and your list."""
    if spot_key(candidate) not in load_registry():
        remember_location(candidate)
    follow(candidate)
    add_compare(spot_key(candidate))


def plain_source(source):
    """A source's short name, for text: ERA5, AROME HD, MeteoSwiss, METAR."""
    return MODEL_NAMES.get(source) or STATION_NAMES[source]


def told_apart(keys, spots):
    """Each spot's title, with its source added where two of these share
    one, as the same place in two kinds of data does, and its position
    where that is still not enough."""
    names = {key: spot_title(spots[key]) for key in keys}
    for extra in (lambda entry: plain_source(entry.get("source", "era5")),
                  lambda entry: coords_label(entry["lat"], entry["lon"])):
        said = list(names.values())
        names = {key: f"{name} ({extra(spots[key])})" if said.count(name) > 1 else name
                 for key, name in names.items()}
    return names


def spot_at(latitude, longitude, source=None):
    """A spot of `source`, this trip's own unless said, at a point: the cell
    there for a model, the nearest station for a station source. None where
    the source has nothing there."""
    source = source or SOURCE
    if source in STATION_FILES:
        table = station_table(source)
        abbr = km_from(latitude, longitude, table["lat"], table["lon"]).idxmin()
        found = station_entry(source, abbr, table.loc[abbr])
    elif source == "arome" and not in_arome_area(latitude, longitude):
        return None
    else:
        grid = CELL_GRIDS[source]
        latitude, longitude = snap_to_grid(latitude, grid), snap_to_grid(longitude, grid)
        found = {"lat": latitude, "lon": longitude, "source": source,
                 "name": place_name(latitude, longitude), "zone": timezone_at(latitude, longitude)}
    known = load_registry().get(spot_key(found), {})
    return {**found, "name": known.get("name") or found["name"]}


# How far the nearest station may be and still be offered for a place:
# further than that, it tells of somewhere else.
STATION_REACH_KM = 150
# South, north, west, east: where MeteoSwiss stations are worth looking for.
SWISS_AREA = (45.5, 48.0, 5.5, 10.8)
# Why a source has nothing at a place, said under it.
NOTHING_HERE = {
    "arome": "the model covers western Europe only",
    "meteoswiss": "it has stations in Switzerland only",
    "metar": f"no airport within {STATION_REACH_KM} km",
}


def found_at(latitude, longitude, source):
    """What `source` has at a point, as (spot, km away, why not): the cell
    over it for a model, the nearest station within STATION_REACH_KM for a
    station source. Where it has nothing there, or cannot be reached just
    now, the spot is None and `why not` says which."""
    south, north, west, east = SWISS_AREA
    if source == "meteoswiss" and not (south <= latitude <= north and west <= longitude <= east):
        return None, None, NOTHING_HERE[source]
    try:
        other = spot_at(latitude, longitude, source)
    except Exception:  # a station list that will not load
        return None, None, "it could not be reached just now"
    if other is None:
        return None, None, NOTHING_HERE.get(source)
    km = float(km_from(latitude, longitude, np.array([other["lat"]]), np.array([other["lon"]]))[0])
    if source in STATION_FILES and km > STATION_REACH_KM:
        return None, None, NOTHING_HERE[source]
    return other, km, None


# How long a new spot takes to download, as short as a row can take it.
DOWNLOAD_TIME = {"metar": "about a minute", "meteoswiss": "a minute or two",
                 "arome": "a year at a time", "era5": "can take a while"}
CELL_SIZES = {"era5": "25 km", "arome": "1.5 km"}
def source_tag(spot):
    """A spot's source in a few words for its row, measured or modelled,
    its full credit on hover."""
    source = spot.get("source", "era5")
    short = (f"{STATION_NAMES[source]} {spot['station']} · measured" if spot.get("station")
             else f"{MODEL_NAMES[source]} · modelled, {CELL_SIZES[source]} cell")
    return f'<span title="{html.escape(source_credit(spot))}">{short}</span>'


def pick_label(text):
    st.html(f'<div class="pick-label">{html.escape(text)}</div>')


def pick_row(candidate, row_key, chosen, room, what, scope="fragment"):
    """One spot you could compare, as a row: `what` it is, its source,
    measured or modelled, on disk or not, and its one button: Add, Download
    and add, or Remove once it is in. A download runs in the row itself,
    then reruns `scope`: the picker it sits in, or the whole page."""
    candidate = {name: value for name, value in candidate.items() if name != "path"}
    key, source = spot_key(candidate), candidate["source"]
    stored, added = spot_path(candidate).exists(), key in chosen
    if candidate.get("station"):
        height = candidate.get("height")
        how = (f"{plain_source(source)} · measured"
               + (f", {height:.0f} m above the sea" if pd.notna(height) else ""))
    else:
        how = (f"{plain_source(source)} · modelled, {CELL_SIZES[source]} cell"
               + (f", since {AROME_SINCE:%B %Y}" if source == "arome" else ""))
    state = ("in the comparison" if added else "on disk" if stored
             else f"not downloaded yet, {DOWNLOAD_TIME[source]}")
    fetch = False
    with st.container(horizontal=True, vertical_alignment="center",
                      key=f"pickrow_{row_key}_{key}{'__added' if added else ''}"):
        st.markdown(f"{what}  \n:gray[{how} · {state}]", width="stretch")
        button = {"key": f"pickbtn_{row_key}_{key}", "width": "content"}
        if added:
            st.button("Remove", icon=":material/close:", on_click=drop_compare, args=(key,),
                      **button)
        elif not room:
            st.button("Add", icon=":material/add:", disabled=True, help=FULL, **button)
        elif stored:
            st.button("Add", icon=":material/add:", on_click=compare_stored, args=(candidate,),
                      **button)
        else:
            fetch = st.button("Download and add", icon=":material/download:", **button)
    if fetch:
        slot = st.empty()
        try:
            fetch_here(candidate, bar_in(slot))
        except Exception as error:
            slot.error(f"Could not download {candidate['name']}: {error}")
        else:
            compare_stored(candidate)
            st.rerun(scope=scope)


def station_words(candidate, km, where):
    """'Palma de Mallorca (LEPA), the nearest airport, 13 km away' and the like."""
    kind = "airport" if candidate["source"] == "metar" else "station"
    return (f"**{candidate['name']}** ({candidate['station']}), the nearest {kind}, "
            f"{km:.0f} km {where}")


def compare_map(source, spots, chosen, here):
    """The map beside the compare button: this spot, the spots compared and
    your other spots in `source`, the stations near for a station source,
    the outline of AROME's area for AROME, and the point you clicked with
    its cell, drawn as a layer of its own so a click leaves the map where
    you moved it. With spots compared, it fits them all. Returns a new
    click as (lat, lon), or None."""
    world = base_map(location=[LATITUDE, LONGITUDE], control_scale=True,
                     zoom_start=5 if source == "metar" else 6 if source == "era5" else 7)
    compared = [spots[key] for key in chosen]
    if compared:
        lats = [LATITUDE, *(entry["lat"] for entry in compared)]
        lons = [LONGITUDE, *(entry["lon"] for entry in compared)]
        world.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], max_zoom=8,
                         padding=(30, 30))
    if source == "arome":
        south, north, west, east = AROME_AREA
        folium.Rectangle(bounds=[[south, west], [north, east]], color="#7dd3fc", weight=1.5,
                         fill=False, dash_array="6 6", tooltip="Where AROME has data").add_to(world)
    if source in STATION_FILES:
        try:
            table = station_table(source)
        except Exception:
            table = None
        if table is not None:
            near = table.loc[km_from(LATITUDE, LONGITUDE, table["lat"], table["lon"])
                             .nsmallest(NEARBY_STATIONS).index]
            for abbr, row in near.iterrows():
                dot_marker(row["lat"], row["lon"], f"{row['name']} · {abbr}", idle=True).add_to(world)
    for key, entry in spots.items():
        if key != here and key not in chosen and entry.get("source", "era5") == source:
            dot_marker(entry["lat"], entry["lon"], spot_title(entry)).add_to(world)
    for entry in compared:
        dot_marker(entry["lat"], entry["lon"], f"{spot_title(entry)} · compared",
                   kind="compared").add_to(world)
    dot_marker(LATITUDE, LONGITUDE, f"{LOCATION_NAME} · this spot", picked=True).add_to(world)

    marks = folium.FeatureGroup(name="clicked")
    at = st.session_state.get("compare_at")
    if at and at[:2] == (spot_key(spot), source):
        latitude, longitude = at[2:]
        if source in CELL_GRIDS:
            grid = CELL_GRIDS[source]
            latitude, longitude = snap_to_grid(latitude, grid), snap_to_grid(longitude, grid)
            folium.Rectangle(
                bounds=[[latitude - grid / 2, longitude - grid / 2],
                        [latitude + grid / 2, longitude + grid / 2]],
                color="#e7ebf3", weight=2, fill=True, fill_opacity=0.2,
            ).add_to(marks)
        dot_marker(latitude, longitude, "Where you clicked", kind="clicked").add_to(marks)
    result = st_folium(
        world, height=300, use_container_width=True, key=f"compare_map_{source}",
        feature_group_to_add=marks,
        returned_objects=["last_clicked", "last_object_clicked", "last_object_clicked_count"],
    )
    return fresh_click(result, f"compare_map_{source}_seen")


def map_picker(spots, chosen, where):
    """Beside the compare button: which data a click fetches, this spot's
    own unless you pick another, the map, and below it the spot of that
    data where you clicked, with its button."""
    source = st.segmented_control(
        "Or click the map, in this data", list(SOURCES), default=SOURCE, required=True,
        key="map_source", format_func=lambda key: SOURCES[key].split(" · ")[0],
        help=f"{LOCATION_NAME} has {plain_source(SOURCE)}, so the map starts there: like with "
        "like. Pick other data to find its cell or station at the place you click.",
    ) or SOURCE
    named = plain_source(source)
    clicked = compare_map(source, spots, chosen, where)
    if clicked:
        st.session_state["compare_at"] = (spot_key(spot), source, *clicked)
        st.rerun()
    st.caption(f"Click anywhere for the {named} "
               + ("station nearest" if source in STATION_FILES else "cell")
               + f" there. Teal: this spot and the spots compared; blue: your other {named} spots"
               + ("; grey: stations." if source in STATION_FILES else "."))
    at = st.session_state.get("compare_at")
    if not at or at[:2] != (spot_key(spot), source):
        return
    found, km, why = found_at(*at[2:], source)
    room = len(chosen) < COMPARE_UP_TO
    if found is None:
        st.caption(f"Nothing from {named} there: {why}.")
    elif spot_key(found) == spot_key(spot):
        st.caption(f"That is {LOCATION_NAME} itself.")
    elif found.get("station"):
        pick_row(found, "map", chosen, room, station_words(found, km, "from where you clicked"),
                 scope="app")
    else:
        pick_row(found, "map", chosen, room,
                 f"**{spot_title(found)}**, the cell where you clicked", scope="app")


@st.dialog("Compare with other spots", width="medium", icon=":material/compare_arrows:",
           on_dismiss="rerun")
def compare_picker(dates):
    """Pick the spots to set beside this one on your `dates`, from lists:
    your spots in this spot's own data first, like with like, then this same
    place in each other source, then your spots in other data. A place you
    have no spot at yet is found on the map beside the button. Adding,
    removing and downloading all happen in here; Show the comparison, or
    closing it, brings the page up to date."""
    spots = my_locations(with_record=True)
    here = spot_key(spot)
    chosen = compared_keys(spots, here)
    room = len(chosen) < COMPARE_UP_TO
    own = plain_source(SOURCE)
    st.markdown(f"Which spots should stand beside **{LOCATION_NAME}** on {dates_label(dates)}?")

    pick_label(f"Your spots in {own}, like this one")
    alike = [key for key, entry in spots.items()
             if entry.get("source", "era5") == SOURCE and key != here]
    if alike:
        with st.container(height=230 if len(alike) > 4 else "content", border=False):
            for key in alike:
                pick_row(spots[key], "alike", chosen, room, f"**{spot_title(spots[key])}**")
    else:
        st.caption(f"None of your other spots use {own} yet: click the map beside the button "
                   "to find one.")

    # This same place in each other source: how far the data agree.
    pick_label(f"{LOCATION_NAME} in other data")
    same, missing = set(), []
    for source in SOURCES:
        if source == SOURCE:
            continue
        other, km, why = found_at(LATITUDE, LONGITUDE, source)
        if other is None:
            missing.append(f"{plain_source(source)} ({why})")
            continue
        same.add(spot_key(other))
        what = (station_words(other, km, "away") if other.get("station")
                else f"**The {CELL_SIZES[source]} cell over this spot**")
        pick_row(other, "same", chosen, room, what)
    if missing:
        st.caption(f"Nothing here from {joined(missing)}.")

    rest = [key for key, entry in spots.items()
            if entry.get("source", "era5") != SOURCE and key != here and key not in same]
    if rest:
        pick_label("Your spots in other data")
        with st.container(height=230 if len(rest) > 4 else "content", border=False):
            for key in rest:
                pick_row(spots[key], "rest", chosen, room, f"**{spot_title(spots[key])}**")
    st.caption("Somewhere new? Close this and click the map beside the button.")

    with st.container(horizontal=True, vertical_alignment="center", key="pick_foot"):
        st.caption(f"{len(chosen)} of {COMPARE_UP_TO} added" if chosen else "Nothing added yet.",
                   width="stretch")
        if st.button("Show the comparison" if chosen else "Close", type="primary",
                     icon=":material/arrow_forward:" if chosen else None, key="pick_done"):
            st.rerun()


# An arrow blowing the way the wind goes, turned by `to` degrees from north.
ARROW_SVG = (
    '<svg class="wind-arrow" style="--to:{to}deg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true"><path d="M12 20V4M6 10l6-6 6 6"/></svg>'
)


def years_held(days):
    """The years a record holds your dates in, as 2022–2025, or '' for none."""
    years = days.index.year
    if not len(years):
        return ""
    return f"{years.min()}–{years.max()}" if years.min() < years.max() else str(years.min())


def show_below(key):
    """Spell this compared spot out under the table: when to go, and your dates."""
    st.session_state["trip_shown"] = key


def compare_table(order, spots, names, outlooks, totals, best, where, shown, length, bare):
    """The spots side by side, a row each, best first, so the differences
    read at a glance: the days with your wind and how many more or fewer
    than at this spot, the usual wind, where it comes from as an arrow
    blowing that way, and the air. `bare` puts a count as a figure. A row
    is a button: click it and the chapter under the table is about that
    spot. `shown` is the one it is about now, edged in teal."""
    st.html('<div class="cmp-head"><span>Spot</span><span>Days with your wind</span>'
            "<span>Usual wind</span><span>From</span><span>Air</span></div>")
    for key in order:
        found = outlooks[key]
        tags = (('<span class="tag">Best bet</span>' if key in best else "")
                + ('<span class="tag quiet">This spot</span>' if key == where else ""))
        name = html.escape(names[key])
        held = years_held(found["days"])
        cells = [f'<span class="cmp-spot"><b title="{name}">{name}</b>'
                 f"<small>{tags}{source_tag(spots[key])}{f' · {held}' if held else ''}</small></span>"]
        if key not in totals or found["knots"] is None:
            cells.append('<span class="cmp-none">No record on these dates</span>')
        else:
            count = totals[key]
            delta = ""
            if key != where and where in totals:
                diff = round(count - totals[where], 1)
                kind = "up" if diff > 0 else "down" if diff < 0 else ""
                said = ("same as this spot" if not diff
                        else f"{'+' if diff > 0 else '−'}{bare(abs(diff))} vs this spot")
                delta = f'<small class="delta {kind}">{said}</small>'
            low, middle, high = found["knots"]
            point = prevailing_direction(found["direction"]) if len(found["direction"]) else None
            arrow = ARROW_SVG.format(to=COMPASS.index(point) * 22.5 + 180) if point else ""
            air = found["air"]
            air_value = "–" if air is None else f"{air:.0f} °C"
            air_note = "no air temperature" if air is None else kit_for(air)
            cells += [
                f'<span class="cmp-days"><span class="count"><b>{bare(count)}</b> '
                f"{'of years' if length == 1 else f'of {length} days'}</span>"
                f'<i class="meter"><i style="width:{count / length * 100:.0f}%"></i></i>{delta}</span>',
                f'<span class="cmp-cell"><b>{low:.0f}–{high:.0f} kt</b><small>{beaufort(middle)}</small></span>',
                f'<span class="cmp-cell cmp-from">{arrow}<span><b>{point or "–"}</b>'
                "<small>wind from</small></span></span>",
                f'<span class="cmp-cell"><b>{air_value}</b><small>{air_note}</small></span>',
            ]
        kinds = "".join(kind for kind, on in ((" best", key in best), (" shown", key == shown))
                        if on)
        with st.container(key=f"cmprow_{key}"):
            st.html(f'<div class="cmp-row{kinds}">{"".join(cells)}</div>')
            st.button(f"Show {names[key]} below", key=f"cmpshow_{key}", on_click=show_below,
                      args=(key,))


# Down to "When to go", for whoever does not want to compare.
DOWN_ARROW = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
              '<path d="M12 4v16M6 14l6 6 6-6"/></svg>')


if st.session_state["mode"] == "window":
    st.html(ANSWER_CSS)
    spots = my_locations(with_record=True)
    # The spot you picked, by its key among your others with a record here.
    where = next((key for key, entry in spots.items() if entry["path"] == dataset_path), None)
    if where is None:
        where, spots = "here", {"here": {**spot, "path": dataset_path}, **spots}
    default_dates = coming_dates(*TRIP_DAYS)
    ask_trip_dates(f"Compare {html.escape(LOCATION_NAME)}", default_dates)

    activity = settings()
    dates = kept("trip_dates", default_dates)
    length = len(window_days(dates))
    wind = tuple(kept(f"wind_{activity}", ACTIVITIES[activity]["groove"]))
    hours = tuple(kept("hours", DEFAULT_HOURS))
    lasting = kept("lasting", DEFAULT_LASTING)
    shift = shift_degrees(kept("shift", DEFAULT_SHIFT))
    feels = feels_degrees(kept("feels", DEFAULT_FEELS)) if HAS_TEMP else None
    years_back = LOOKBACK[kept("years", DEFAULT_YEARS)]
    within = (
        "at any hour" if hours == (0, 23)
        else f"between {hours[0]:02d}:00 and {hours[1]:02d}:59 local time"
    )
    def years_counted(key):
        """The years counted at a spot: the filter's, or fewer where its record is shorter."""
        path = spots[key]["path"]
        frame = load_wind(path, path.stat().st_mtime, spots[key]["zone"])
        first, last = lookback_start(frame, years_back), frame.index.max().year
        return f"{first}–{last}" if first < last else str(first)

    def outlook_at(key):
        path = spots[key]["path"]
        return trip_outlook(path, path.stat().st_mtime, spots[key]["zone"], years_back,
                            wind, lasting, hours, dates, shift, feels)

    # Nothing is compared until you ask: then this spot and the ones you
    # added, best first.
    chosen = compared_keys(spots, where)
    st.session_state["trip_compare"] = chosen
    # The spot spelled out under the table: this one, or one you clicked in it.
    shown = st.session_state.get("trip_shown")
    if shown not in (where, *chosen):
        shown = where
    st.session_state["trip_shown"] = shown
    with st.spinner("Reading every year on record..."):
        outlooks = {key: outlook_at(key) for key in [where, *chosen]}
        shown_path = spots[shown]["path"]
        chances = year_chances(shown_path, shown_path.stat().st_mtime, spots[shown]["zone"],
                               years_back, wind, lasting, hours, shift, feels)
    totals = {key: typical_days(found["days"], length) for key, found in outlooks.items()
              if len(found["days"])}
    names = told_apart(list(outlooks), spots)
    shown_name = html.escape(LOCATION_NAME if shown == where else names[shown])
    out_of = f"of your {length} days"
    days_word = "day" if length == 1 else "days"

    def bare(count):
        """A count of days with your wind as a figure: 6, or for a single day
        the share of years it came on, 80%."""
        return f"{count * 100:.0f}%" if length == 1 else f"{count:g}"

    def told(count):
        """The same in words: 6 of your 7 days, or 80% of years."""
        return f"{bare(count)} of years" if length == 1 else f"{count:g} {out_of}"

    with st.container(key="trip_main"):
        # --- Where, when, and by which rule ---
        st.html(f'<div class="view-title">Compare {html.escape(LOCATION_NAME)}</div>')
        with st.container(horizontal=True, vertical_alignment="center", gap="small",
                          key="trip_when"):
            st.html(f'<div class="trip-when"><b>{dates_label(dates)}</b>'
                    f"<span>{length} {days_word} · {html.escape(source_credit(spot))}</span></div>",
                    width="content")
            if st.button("Change dates", icon=":material/edit_calendar:", type="tertiary",
                         key="trip_redate"):
                st.session_state["trip_step"] = 0
                st.rerun()
        if where not in totals:
            answer = "No record on these dates here yet: pick other dates."
        elif length == 1:
            answer = f"Your wind came here on your day in <b>{told(totals[where])}</b>."
        else:
            answer = f"In a typical year, <b>{told(totals[where])}</b> bring your wind here."
        st.html(
            f'<div class="trip-answer">{answer}</div>'
            f'<div class="stage-rule">A day counts when <b>{wind_words(wind)}</b> blows for '
            f"{lasting} h in a row {within}{rule_words(shift, feels)}. Counted over "
            f"{years_held(outlooks[where]['days']) or years_counted(where)}. Change it in the "
            "filter.</div>"
        )

        # --- Compare: the big button with the map beside it, then, once
        # there are spots to compare, the spots side by side under both ---
        if chosen:
            order = sorted(outlooks, key=lambda key: (-totals.get(key, -1), key != where))
            top = max(totals.values(), default=None)
            leaders = [key for key in order if totals.get(key) == top]
            # The best bet stands out only where it beats someone.
            best = [key for key in leaders if len(leaders) < len(totals)]
            bold = {key: f"<b>{html.escape(names[key])}</b>" for key in order}
            if not totals:
                verdict = "No record on these dates yet: pick other dates, or other spots."
            elif len(totals) == 1:
                only = next(iter(totals))
                verdict = (f"{told(totals[only])} at {bold[only]}; the others have no "
                           "record on these dates.")
            elif not best:
                verdict = f"Much the same at each: {told(top)}."
            elif best == [where]:
                verdict = f"{bold[where]} is your best bet: {told(top)}."
            elif where in best:
                verdict = f"{joined([bold[key] for key in best])} share the most: {told(top)}."
            else:
                verdict = (
                    f"{joined([bold[key] for key in best])} "
                    f"{'has' if len(best) == 1 else 'have'} the most wind: {told(top)}, "
                    + (f"against {bare(totals[where])} at {bold[where]}." if where in totals
                       else f"and {bold[where]} has no record on these dates.")
                )
        with st.container(key="trip_compared" if chosen else "trip_cta"):
            text_col, map_col = st.columns(2, gap="large")
            with text_col:
                if not chosen:
                    others = [{"era5": "ERA5", "arome": "AROME", "meteoswiss": "MeteoSwiss stations",
                               "metar": "airports"}[source] for source in SOURCES if source != SOURCE]
                    st.html(
                        f'<div class="cta-title">Compare {html.escape(LOCATION_NAME)} with other '
                        "spots</div>"
                        f'<div class="cta-sub">See side by side which spot gets the most wind on '
                        f"{dates_label(dates)}. Pick from your spots with the button, or click a "
                        f"place on the map. Both start with {plain_source(SOURCE)}, the data "
                        f"this spot has; {', '.join(others[:-1])} or {others[-1]} are one click "
                        "away.</div>"
                    )
                    if st.button("Compare with other spots", type="primary",
                                 icon=":material/compare_arrows:", key="trip_open_compare"):
                        compare_picker(dates)
                else:
                    st.html(f'<div class="cta-title">Side by side on {dates_label(dates)}</div>'
                            f'<div class="stage-verdict">{verdict}</div>')
                    with st.container(horizontal=True, vertical_alignment="center", gap="small",
                                      key="trip_compared_top"):
                        if st.button("Add or change spots", icon=":material/edit:",
                                     key="trip_change_compare"):
                            compare_picker(dates)
                        st.button("Clear", icon=":material/close:", type="tertiary",
                                  key="trip_clear_compare", on_click=clear_compare)
            with map_col:
                map_picker(spots, chosen, where)
            if chosen:
                with st.container(gap="small", key="cmp"):
                    compare_table(order, spots, names, outlooks, totals, best, where, shown,
                                  length, bare)
                st.caption("Click a spot to see when to go there, and your dates in full, "
                           "below. Days with your wind in a typical year of each spot's own "
                           "record, in its own local time. A model cell smooths the wind over "
                           "its area; a station measures it where it stands.")
        st.html(
            f'<a class="scroll-cue" href="#when-to-go">{DOWN_ARROW}'
            + ("Not comparing? " if not chosen else "")
            + f"Scroll down for when to go to {shown_name}</a>"
        )

        # --- When to go: the best days of the year, then your dates, at
        # this spot or the one you clicked in the table ---
        st.html(f'<div class="when-title" id="when-to-go">When to go to {shown_name}</div>')
        if chances is not None:
            window, expected = best_stretch(chances, length)
            better = rounded_days(expected, length)
            yours = totals.get(shown)
            stretch = "day" if length == 1 else f"{length} days"
            marks = [(dates, "Your dates", "#7dd3fc")]
            if yours is not None and (window == dates or better <= yours):
                lead = f"Your {'day is' if length == 1 else 'dates are'} as good as it gets here"
                sub = (("No other day brings" if length == 1
                        else f"No other {length} days in a row bring")
                       + f" your wind more often: <b>{told(yours)}</b>"
                       + ("." if length == 1 else " in a typical year."))
            else:
                marks.append((window, f"Best {stretch}", "#2dd4bf"))
                lead = f"The best {stretch} here: {dates_label(window)}"
                against = ("none on record on your dates." if yours is None
                           else f"<b>{bare(yours)}</b> on your {'day' if length == 1 else 'dates'}.")
                sub = (f"Your wind came on it in about <b>{told(better)}</b>, against {against}"
                       if length == 1 else
                       f"About <b>{better:g} of {length} days</b> with your wind in a typical "
                       f"year, against {against}")
            st.html(f'<div class="chapter-lead">{lead}</div><div class="chapter-sub">{sub}</div>')
            if len(marks) > 1:
                st.button(f"Plan for {dates_label(window)} instead", icon=":material/event:",
                          key="trip_best_dates", on_click=use_dates, args=(window,))
            st.altair_chart(season_chart(chances, marks), width="stretch")
            st.caption(f"Each day of the year: how often it brought your wind in "
                       f"{years_counted(shown)}, "
                       "over the two weeks round it, the nearest days counting most. Shaded, "
                       "your dates"
                       + (f" and the best {stretch}." if len(marks) > 1 else "."))

        # Your dates in full: four figures, every year on record, the wind
        # and the air beside the rose of where it comes from.
        found = outlooks[shown]
        days = found["days"]
        st.html(f'<div class="dates-title">On your dates, {dates_label(dates)}</div>')
        if shown not in totals or found["knots"] is None:
            st.caption("No record on these dates here yet: pick other dates.")
        else:
            low, middle, high = found["knots"]
            figures = [
                (bare(totals[shown]), "of years with your wind") if length == 1
                else (f"{totals[shown]:g} of {length}", "days with your wind"),
                (f"{low:.0f}–{high:.0f} kt", beaufort(middle)),
                (prevailing_direction(found["direction"]) if len(found["direction"]) else "–",
                 "wind from, in range"),
                (f"{found['air']:.0f} °C", f"air · {kit_for(found['air'])}")
                if found["air"] is not None else ("–", "no air temperature"),
            ]
            st.html('<div class="fact-boxes">'
                    + "".join(f"<div><b>{value}</b><span>{label}</span></div>"
                              for value, label in figures)
                    + "</div>")
            text_col, rose_col = st.columns([3, 2], gap="large")
            rows = year_rows(days, dates)
            if rows:
                tally = {year: sum(kind == "g" for kind in kinds) for year, kinds in rows}
                most, least = max(tally.values()), min(tally.values())
                tops = [year for year in tally if tally[year] == most]
                lows = [year for year in tally if tally[year] == least]
                if length == 1:
                    lead = f"Your wind came in {sum(tally.values())} of the {len(tally)} years"
                elif most == least:
                    lead = (f"All {length} days, every year" if most == length
                            else f"{most} {out_of}, every year")
                else:
                    lead = f"{least} to {most} {out_of}, depending on the year"
                text_col.html(
                    f'<div class="trip-block"><div class="chapter-lead">{lead}</div>'
                    '<div class="chapter-sub">One row per year, a dot per day, lit when your '
                    "wind came."
                    + ("" if most == least else
                       f" <b>{most}</b> in {years_named(tops, len(tally))}; the leanest, "
                       f"{years_named(lows, len(tally))}, had <b>{least}</b>.")
                    + "</div>"
                    + dot_rows([
                        (year, kinds, tally[year], f"{year}: {tally[year]} of {length} days",
                         most != least and year in tops and len(tops) <= 3)
                        for year, kinds in rows
                    ])
                    + "</div>"
                )

            # The wind: how hard, how often right, from where, best when.
            bands = wind_bands(found)
            best_time = best_hours(found["hour"], hours)
            direction = found["direction"]
            text_col.html(
                '<div class="trip-block">'
                f'<div class="chapter-lead">Usually {low:.0f}–{high:.0f} kt, '
                f"a {beaufort(middle)}</div>"
                f'<div class="chapter-sub">Of your hours on the water, <b>{bands["good"]}%</b> '
                f"have wind in your range, {bands['light']}% are too light and "
                f"{bands['strong']}% too strong"
                + (f". In range, it mostly comes from the <b>"
                   f"{compass_words(prevailing_direction(direction))}</b>" if len(direction) else "")
                + f". Best between <b>{best_time[0]:02d}:00 and {best_time[-1]:02d}:59</b>.</div>"
                + band_bar(bands) + "</div>"
            )
            # The air, and what that means you wear.
            if found["air"] is not None:
                text_col.html(
                    '<div class="trip-block">'
                    f'<div class="chapter-lead">Around {found["air"]:.0f} °C in your hours</div>'
                    '<div class="chapter-sub">Air, not water, on average over your dates. What to '
                    f"wear: <b>{kit_for(found['air'])}</b>.</div></div>"
                )
            headings, speeds = found["rose"]
            if len(headings):
                with rose_col:
                    rose = draw_rose(headings, speeds, ACTIVITIES[activity]["ladder"])
                    st.pyplot(rose, width="stretch")
                    plt.close(rose)
                    st.caption("Where the wind comes from in your hours on these dates.")
    small_print()
    st.stop()


# --- Rating ---------------------------------------------------------------

if st.session_state["mode"] == "rating":
    activity = settings()
    # Forward, or switching sport in the sidebar, can land here with a range
    # that has nothing to score.
    if not can_score(activity):
        st.info(
            f"**{activity}** counts every day as sailable, so there is nothing to "
            "score. Pick what you sail in the filter."
        )
        st.stop()
    # The same settings the compare view counts with.
    wind = tuple(kept(f"wind_{activity}", ACTIVITIES[activity]["groove"]))
    hours = tuple(kept("hours", DEFAULT_HOURS))
    lasting = kept("lasting", DEFAULT_LASTING)
    shift = shift_degrees(kept("shift", DEFAULT_SHIFT))
    feels = feels_degrees(kept("feels", DEFAULT_FEELS)) if HAS_TEMP else None
    years_back = LOOKBACK[kept("years", DEFAULT_YEARS)]
    SESSION_TEXT = "all day" if hours == (0, 23) else f"{hours[0]:02d}:00–{hours[1]:02d}:59"

    with st.spinner("Counting your days..."):
        rating = rate_location(
            dataset_path, dataset_path.stat().st_mtime, LOCAL_TZ, years_back,
            activity, wind, lasting, hours, shift, feels,
        )

    st.html(
        f'<div class="rating-hero"><div class="rating-score">{rating["overall"]:.1f}'
        '<span style="font-size:1.6rem;opacity:.55"> / 10</span></div></div>'
        f'<div style="font-size:1.15rem;font-weight:600">{html.escape(LOCATION_NAME)} · '
        f"{activity}</div>"
    )
    st.caption(
        f"Scored on {rating['years']}: {wind_words(wind)} for {lasting} h in a row"
        f"{rule_words(shift, feels)}, {SESSION_TEXT} local time. Data: {SOURCE_CREDIT}."
    )

    expect_col, parts_col = st.columns([2, 3], gap="medium")

    with expect_col:
        with st.container(border=True):
            st.markdown("##### :material/lightbulb: What to expect")
            missing_temp = "No air temperature in this download."
            for line in what_to_expect(rating, activity, wind, lasting, SESSION_TEXT,
                                       missing_temp, shift, feels):
                st.markdown(line)

    with parts_col:
        for name, (score, detail) in rating["parts"].items():
            label, bar = st.columns([2, 5], vertical_alignment="center")
            label.markdown(f"**{name}**  \n<small>{detail}</small>", unsafe_allow_html=True)
            # st.progress rejects numpy scalars, so hand it a plain float.
            bar.progress(float(score) / 10, text=f"{score:.1f} / 10")

    st.divider()
    highlights = st.columns(4)
    highlights[0].metric(
        "Best month", calendar.month_name[rating["best_month"]], border=True,
        icon=":material/trending_up:",
        help=f"{rating['best_month_pct']:.0f}% of days hold {wind_words(wind)} "
        f"for {lasting} h in a row",
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
        locator_map(LATITUDE, LONGITUDE, key="rating_map", height=340, cell=CELL)
    with rose_col:
        rose = draw_rose(rating["direction"], rating["knots"], ACTIVITIES[activity]["ladder"])
        st.pyplot(rose, width="stretch")
        plt.close(rose)
    with note_col:
        st.markdown("**How this is scored**")
        st.caption(
            "A weighted blend of "
            + ", ".join(
                f"{name.lower()} ({weight:.0%})" for name, weight in RATING_WEIGHTS.items()
            )
            + ". The four parts are shown above, so you can weigh them yourself."
        )
    small_print()
    st.stop()
