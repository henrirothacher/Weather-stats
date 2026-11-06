# Wind atlas

Pick any point on earth, any Swiss weather station or any airport, pull its
wind history, and see whether it is worth sailing — by month, by time of
day, and when, and at which of your spots, the wind you want turns up most.

Built with Streamlit on three sources, picked on the start screen:

- **ERA5** — ECMWF's reanalysis, hourly, 1940–present, 0.25° grid, anywhere.
- **AROME HD** — Météo-France's 1.5 km model over western Europe
  (37.5–55.4°N, 12°W–16°E), hourly since 13 November 2022, as
  [Open-Meteo](https://open-meteo.com/) archives it (CC BY 4.0). The first
  hours of each forecast run stitched together, not a reanalysis, and only
  a few years of it: a single odd summer carries weight.
- **MeteoSwiss** — measured hourly wind from the ~150 SwissMetNet stations,
  most since 1981 (open data, CC BY 4.0).
- **METAR** — the weather reports of ~5,900 airports worldwide, as archived
  by the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/),
  most hourly since the 1970s. One report an hour is kept, the one nearest
  the hour; years with only a few reports a day are left out.

The station maps show only stations by the water: within 20 km of the sea or
a lake, as [Natural Earth](https://www.naturalearthdata.com/) maps the
shores (public domain), and for MeteoSwiss not on a summit. A switch on the
start screen shows them all, for lakes too small for Natural Earth. A
MeteoSwiss station that measures no air temperature borrows it from the
nearest one that does, corrected for the difference in height.

A spot you have not named is called after the water it lies on, from
Natural Earth's named seas and lakes, and the town nearby, from
[GeoNames](https://www.geonames.org/) (towns of 15,000 people or more,
CC BY 4.0): "Lake Balaton near Tapolca", "Strait of Gibraltar, 26 km N of
Tangier".

Comparing spots keeps to one kind by default, stations with stations and
model cells with model cells; a switch opens it to all, in explore's
settings and in the trip planner's comparison.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Fetching new ERA5 locations needs a free Copernicus account (MeteoSwiss
stations and airports need nothing). Put your token in `~/.cdsapirc`, or copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` and fill it in.

## Deploy

1. Push to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), point a new app at
   this repo with `streamlit_app.py` as the entry point.
3. In **Settings → Secrets**, paste the contents of
   `.streamlit/secrets.toml.example` with your real token.

Note: the hosted filesystem is ephemeral. Locations committed under `data/`
are always available; anything downloaded from the running app disappears
when the app restarts.

Everyone who opens the hosted app shares that folder, so it is a cache, not
anybody's list. Which spots are yours lives in the page address instead
(`?spot=36.75_-8.25&spot=metar_LEPA`): bookmark it to keep them, send it to
share them. A spot whose record a restart has swept away stays in your list
and is fetched again when you open it.

## Layout

- `streamlit_app.py` — the whole app
- `data/wind_<lat>_<lon>.nc` — one cached ERA5 cell per file
- `data/arome_<lat>_<lon>.nc` — one AROME HD cell per file, in the same
  layout
- `data/station_<code>.nc` — one MeteoSwiss station per file, converted to
  the same layout (`u10`, `v10`, `t2m` on `valid_time`)
- `data/metar_<code>.nc` — one airport per file, in that same layout
- `data/locations.json` — names given to those cells and stations, shared
  by everyone; which spots are yours is in your link, not here
- `data/places.csv` — GeoNames towns, to name the cells you have not
- `data/shores.npy` — every coast and lake shore point, to the nearest
  0.01°; the app builds it from Natural Earth when it is missing
- `data/waters.npz` — the outlines of Natural Earth's named seas and lakes,
  to name spots after their water; built the same way
- `.streamlit/config.toml` — theme
