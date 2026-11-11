"""Download the raw inputs build.py reads into data/raw (about 400 MB). Skips files already present.

  uv run python pipeline/fetch.py
"""
import json
import subprocess
import urllib.parse
import zipfile
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data/raw"
USER_AGENT = "Mozilla/5.0"  # naciscdn.org rejects the default urllib agent

GHSL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/GHS_POP_E2025_GLOBE_R2023A_54009_1000/V1-0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.zip"
CENSUS = "https://www2.census.gov"
NATURAL_EARTH = "https://naciscdn.org/naturalearth"
STATCAN_CMA_LAYER = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer/6/query"
STATCAN_SIMPLIFY_DEG = 0.002  # about 200 m; build.py repairs the self-intersections this leaves

# (url, member to extract from the zip, or None to keep the download as it is)
FILES = [
    (GHSL, "GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"),
    (f"{CENSUS}/geo/tiger/GENZ2023/shp/cb_2023_us_cbsa_500k.zip", None),
    (f"{CENSUS}/programs-surveys/popest/datasets/2020-2025/metro/totals/cbsa-est2025-alldata.csv", None),
    (f"{CENSUS}/programs-surveys/popest/datasets/2020-2025/state/totals/NST-EST2025-ALLDATA.csv", None),
    ("https://www150.statcan.gc.ca/n1/tbl/csv/17100148-eng.zip", "17100148.csv"),
    (f"{NATURAL_EARTH}/10m/cultural/ne_10m_admin_0_countries_lakes.zip", None),
    (f"{NATURAL_EARTH}/50m/cultural/ne_50m_admin_0_countries_lakes.zip", None),
    (f"{NATURAL_EARTH}/50m/cultural/ne_50m_admin_1_states_provinces_lakes.zip", None),
]


def download(url: str, dest: Path) -> None:
    """curl, not urllib: geo.statcan.gc.ca's certificate chain only verifies against the system trust store."""
    print("fetching", dest.name)
    subprocess.run(["curl", "-sSL", "--fail", "-A", USER_AGENT, "-o", str(dest), url], check=True)


def fetch_statcan_boundaries(dest: Path) -> None:
    """2021 CMA/CA boundaries from StatCan's map service (its zip downloads refuse scripted requests)."""
    query = urllib.parse.urlencode({
        "where": "1=1", "outFields": "CMAUID,CMANAME", "outSR": 4326,
        "maxAllowableOffset": STATCAN_SIMPLIFY_DEG, "f": "geojson",
    })
    download(f"{STATCAN_CMA_LAYER}?{query}", dest)
    data = json.loads(dest.read_text())
    if data.get("exceededTransferLimit") or not data.get("features"):
        dest.unlink()
        raise RuntimeError("StatCan returned a truncated or empty boundary set; page the query with resultOffset")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for url, member in FILES:
        archive = RAW / url.rsplit("/", 1)[1]
        target = RAW / member if member else archive
        if target.exists():
            continue
        download(url, archive)
        if member:
            with zipfile.ZipFile(archive) as z:
                z.extract(member, RAW)
            archive.unlink()
    boundaries = RAW / "statcan_cma_2021.geojson"
    if not boundaries.exists():
        fetch_statcan_boundaries(boundaries)
    print("data/raw is ready; delete data/cache if the inputs changed")


if __name__ == "__main__":
    main()
