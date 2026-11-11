# North America megaregions

Interactive poster of US + Canada megaregions, after the Europe map by @47Tolkien. Live at https://mega-regions-na.vercel.app.

    uv run python pipeline/fetch.py          # download raw inputs into data/raw (about 400 MB, needs curl)
    uv run python pipeline/build.py          # data/raw -> site/data.json + site/glow.png
    uv run python pipeline/build.py --sweep  # print a threshold sweep (its own grid, not the site's options)
    python3 -m http.server 8765 --directory site   # then open http://localhost:8765

`build.py` caches the reprojected grid in `data/cache`; delete that folder if the inputs or the frame change. It prints a grid-to-official population ratio for large metros and stops if the median is off by more than 10%, which is the first thing a wrong download trips. Simplified geometry in `data.json` can differ in the last digit across GEOS versions.

Thresholds, the site's threshold options, region names and the frame are constants at the top of `pipeline/build.py`.

Poster PNG (macOS, with the server running):

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --hide-scrollbars --window-size=1800,1250 --virtual-time-budget=5000 --screenshot=poster.png http://localhost:8765

Deploy: `vercel deploy site --prod --yes`.

## Data and licences

`site/data.json`, `site/glow.png` and `poster.png` are derived from:

- **GHS-POP R2023A, epoch 2025, 1 km** © European Union, Joint Research Centre, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Reprojected to Albers, smoothed and thresholded here.
- **US Census Bureau** Vintage 2025 population estimates and 2023 CBSA cartographic boundaries. Public domain; not endorsed by the Census Bureau.
- **Statistics Canada**, table 17-10-0148-01 and 2021 Census cartographic boundary files (CMA/CA layer of the `Cartographic_boundary_files` map service). Adapted from Statistics Canada under the [Statistics Canada Open Licence](https://www.statcan.gc.ca/en/reference/licence). This does not constitute an endorsement by Statistics Canada of this product.
- **Natural Earth** 10m and 50m admin layers. Public domain.

The raw downloads are not committed.
