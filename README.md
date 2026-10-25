# Wind atlas

Pick any point on earth, any Swiss weather station or any airport, pull its
wind history, and see whether it is worth sailing — by month, by time of
day, and when, and at which of your spots, the wind you want turns up most.

Built with Streamlit on three sources, picked on the start screen:

- **ERA5** — ECMWF's reanalysis, hourly, 1940–present, 0.25° grid, anywhere.
- **MeteoSwiss** — measured hourly wind from the ~150 SwissMetNet stations,
  most since 1981 (open data, CC BY 4.0).
- **METAR** — the weather reports of ~5,900 airports worldwide, as archived
  by the [Iowa Environmental Mesonet](https://mesonet.agron.iastate.edu/),
  most hourly since the 1970s. One report an hour is kept, the one nearest
  the hour; years with only a few reports a day are left out.

A spot you have not named is called after the nearest well-known town, from
[GeoNames](https://www.geonames.org/) (towns of 15,000 people or more,
CC BY 4.0).

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

## Layout

- `streamlit_app.py` — the whole app
- `data/wind_<lat>_<lon>.nc` — one cached ERA5 cell per file
- `data/station_<code>.nc` — one MeteoSwiss station per file, converted to
  the same layout (`u10`, `v10`, `t2m` on `valid_time`)
- `data/metar_<code>.nc` — one airport per file, in that same layout
- `data/locations.json` — names you have given those cells
- `data/places.csv` — GeoNames towns, to name the cells you have not
- `.streamlit/config.toml` — theme
