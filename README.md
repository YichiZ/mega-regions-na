# North America megaregions

Interactive poster of US + Canada megaregions, after the Europe map by @47Tolkien.

    uv run python pipeline/build.py          # raw data in data/raw -> site/data.json + site/glow.png
    uv run python pipeline/build.py --sweep  # print the threshold sweep
    python3 -m http.server 8765 --directory site   # then open http://localhost:8765

Poster PNG (with the server running):

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --hide-scrollbars --window-size=1800,1250 --virtual-time-budget=5000 --screenshot=poster.png http://localhost:8765

Raw inputs (data/raw): GHSL GHS-POP E2025 R2023A 1 km (Mollweide), Census `cb_2023_us_cbsa_500k` + `cbsa-est2025-alldata.csv` + `NST-EST2025-ALLDATA.csv`, StatCan table 17-10-0148 + 2021 CMA/CA boundaries (layer 6 of the `Cartographic_boundary_files` map service on geo.statcan.gc.ca, saved as `statcan_cma_2021.geojson`), Natural Earth 10m/50m admin layers.

Thresholds, names and the frame are constants at the top of `pipeline/build.py`.
