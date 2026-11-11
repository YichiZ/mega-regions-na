"""North America megaregions: GHSL 1 km grid -> smoothed density -> cores -> watershed -> site/data.

  uv run python pipeline/build.py          # build site/data.json + site/glow.png
  uv run python pipeline/build.py --sweep  # print the threshold sweep and exit
"""
import json
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from PIL import Image
from rasterio import features
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from scipy import ndimage as ndi
from shapely.geometry import box, shape
from shapely.ops import unary_union
from skimage.segmentation import watershed

ROOT = Path(__file__).resolve().parent.parent
RAW, CACHE, SITE = ROOT / "data/raw", ROOT / "data/cache", ROOT / "site"

CRS = "ESRI:102008"  # North America Albers Equal Area Conic
X0, Y0, X1, Y1 = -2_500_000, -1_750_000, 3_300_000, 2_150_000  # contiguous US + southern Canada
CELL = 1000
W, H = (X1 - X0) // CELL, (Y1 - Y0) // CELL
TRANSFORM = from_origin(X0, Y1, CELL, CELL)
FRAME = box(X0, Y0, X1, Y1)

SIGMA_KM = 10
MIN_MEGA_POP = 4_000_000
MEMBER_SHARE = 0.5  # a metro belongs where at least half of its gridded residents live
CIRCLE_MIN_POP = 250_000
GLOW_MIN, GLOW_FULL = 300, 4000  # residents per km2: glow starts / saturates
GLOW_BLOCK = 2  # glow.png is stored at 2 km: the 1 km image decodes to ~90 MB, too much for phones
IN_SCOPE = ("USA", "CAN")
YEAR = "2025"

# (core threshold, extension threshold) in smoothed residents per km2
# The site opens on DEFAULT_VARIANT; the toggle lists them in this order.
DEFAULT_VARIANT = "100/75"
VARIANTS = {f"{core}/{ext}": {"label": label, "core": core, "ext": ext} for label, core, ext in [
    ("Europe", 250, 180), ("Tuned", 100, 75), ("", 80, 60), ("", 60, 45), ("", 50, 35), ("", 40, 30),
    ("", 30, 22), ("", 20, 15), ("", 15, 10), ("Loosest", 10, 7),
]}
MAJOR_METRO_POP = 750_000  # naming rules only look at metros this large (small towns share big-city names)
SWEEP_CORES, SWEEP_EXTS = (100, 150, 200, 250, 300), (75, 100, 140, 180)

# Megaregion names: the first rule whose metros are all inside wins; otherwise "A–B".
NAME_RULES = [
    ({"New York", "Chicago", "Dallas"}, "Eastern & Central North America"),
    ({"New York", "Chicago", "Atlanta"}, "Eastern North America"),
    ({"New York", "Chicago"}, "Northeast–Great Lakes"),
    ({"Los Angeles", "San Francisco"}, "California"),
    ({"Miami", "New Orleans"}, "Florida–Gulf Coast"),
    ({"Dallas", "San Antonio"}, "Dallas–San Antonio"),
    ({"Montréal", "Ottawa"}, "St. Lawrence Valley"),
    ({"Phoenix", "Tucson"}, "Sun Corridor"),
    ({"New Orleans"}, "Gulf Coast"),
    ({"Chicago", "Detroit", "Cincinnati"}, "Great Lakes–Ohio Valley"),
    ({"Chicago", "Detroit"}, "Great Lakes"),
    ({"Chicago", "Grand Rapids"}, "Lake Michigan"),
    ({"Atlanta", "Charlotte"}, "Piedmont Atlantic"),
    ({"Atlanta", "Knoxville"}, "Atlanta–East Tennessee"),
    ({"Pittsburgh", "Cincinnati"}, "Ohio Valley"),
    ({"Toronto", "Rochester"}, "Lake Ontario"),
    ({"New York", "Boston", "Washington"}, "Northeast Corridor"),
    ({"New York", "Washington"}, "New York–Washington"),
    ({"New York"}, "New York–Philadelphia"),
    ({"Washington"}, "Washington–Baltimore"),
    ({"Boston"}, "Greater Boston"),
    ({"Miami", "Orlando"}, "Florida Peninsula"),
    ({"Miami"}, "South Florida"),
    ({"Tampa"}, "Central Florida"),
    ({"Seattle", "Vancouver"}, "Cascadia"),
    ({"Seattle"}, "Puget Sound"),
    ({"Los Angeles"}, "Southern California"),
    ({"San Francisco"}, "Northern California"),
    ({"Chicago"}, "Chicago–Milwaukee"),
    ({"Toronto"}, "Golden Horseshoe"),
    ({"Charlotte"}, "Carolina Piedmont"),
    ({"Atlanta"}, "Greater Atlanta"),
    ({"Dallas"}, "Dallas–Fort Worth"),
    ({"Houston"}, "Greater Houston"),
    ({"Detroit"}, "Detroit–Windsor"),
    ({"Phoenix"}, "Greater Phoenix"),
    ({"Montréal"}, "Greater Montréal"),
    ({"Denver"}, "Front Range"),
    ({"Minneapolis"}, "Twin Cities"),
]
UNSPLIT = {"Winston-Salem"}
PROVINCE_PART = re.compile(r"\s*\(.*\)")  # StatCan suffix on metros that straddle a provincial border


def short_name(name: str, country: str) -> str:
    head = name.split(",")[0]
    if country == "CAN":
        return head.split(" - ")[0].split(" / ")[0]
    first = head.split("--")[0]
    return next((u for u in UNSPLIT if first.startswith(u)), first.split("-")[0])


def load_pop() -> np.ndarray:
    """GHSL residents per 1 km Albers cell, zero outside the US and Canada."""
    cached = CACHE / "pop.npy"
    if cached.exists():
        return np.load(cached)
    tif = next(RAW.glob("GHS_POP_*.tif"))
    with rasterio.open(tif) as src, WarpedVRT(
        src, crs=CRS, transform=TRANSFORM, width=W, height=H, resampling=Resampling.sum
    ) as vrt:
        pop = vrt.read(1).astype("float32")
    pop[~np.isfinite(pop) | (pop < 0)] = 0
    pop[~country_mask()] = 0
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(cached, pop)
    return pop


def countries() -> gpd.GeoDataFrame:
    return gpd.read_file(RAW / "ne_10m_admin_0_countries_lakes.zip")[["ADM0_A3", "geometry"]]


def country_mask() -> np.ndarray:
    """True for cells whose centre is not in a foreign country (keeps coastal cells the border file misses)."""
    foreign = countries().query("ADM0_A3 not in @IN_SCOPE").to_crs(CRS).clip(FRAME)
    return features.rasterize(foreign.geometry, out_shape=(H, W), transform=TRANSFORM) == 0


def land_mask() -> np.ndarray:
    home = countries().query("ADM0_A3 in @IN_SCOPE").to_crs(CRS).clip(FRAME)
    return features.rasterize(home.geometry, out_shape=(H, W), transform=TRANSFORM) == 1


def load_metros() -> gpd.GeoDataFrame:
    """US CBSAs and Canadian CMAs/CAs with official population estimates, in Albers."""
    us_pop = pd.read_csv(RAW / f"cbsa-est{YEAR}-alldata.csv", encoding="latin1", dtype=str)
    us_pop = us_pop[us_pop.LSAD.str.endswith("Statistical Area")]
    us_pop = us_pop.assign(pop=us_pop[f"POPESTIMATE{YEAR}"].astype(int))[["CBSA", "pop"]]
    us = gpd.read_file(RAW / "cb_2023_us_cbsa_500k.zip").merge(us_pop, left_on="CBSAFP", right_on="CBSA")
    us = us.assign(key="US" + us.CBSAFP, full=us.NAME, country="USA")

    ca_pop = pd.read_csv(RAW / "17100148.csv", dtype=str, usecols=["REF_DATE", "DGUID", "Gender", "Age group", "VALUE"])
    ca_pop = ca_pop[(ca_pop.REF_DATE == YEAR) & (ca_pop["Age group"] == "All ages") & (ca_pop.Gender == "Total - gender")]
    ca_pop = ca_pop[ca_pop.DGUID.str[4:9].isin(["S0503", "S0504"])]  # whole CMAs and CAs, not provincial parts
    ca_pop = ca_pop.assign(CMAUID=ca_pop.DGUID.str[-3:], pop=ca_pop.VALUE.astype(int))[["CMAUID", "pop"]]
    ca = gpd.read_file(RAW / "statcan_cma_2021.geojson")
    ca = ca.set_geometry(ca.geometry.make_valid())  # the service's simplification leaves self-intersections
    ca = ca.dissolve("CMAUID", as_index=False).merge(ca_pop, on="CMAUID")
    ca = ca.assign(key="CA" + ca.CMAUID, full=ca.CMANAME.str.replace(PROVINCE_PART, "", regex=True), country="CAN")

    cols = ["key", "full", "country", "pop", "geometry"]
    metros = pd.concat([us[cols].to_crs(CRS), ca[cols].to_crs(CRS)], ignore_index=True)
    metros = metros[metros.intersects(FRAME)].reset_index(drop=True)
    metros["name"] = [short_name(f, c) for f, c in zip(metros.full, metros.country)]
    metros["mid"] = np.arange(1, len(metros) + 1)
    return metros


def by_zone_and_metro(zones: np.ndarray, n_zones: int, mid: np.ndarray, n_metros: int, pop: np.ndarray) -> np.ndarray:
    """Gridded residents for every (zone, metro) pair."""
    pair = zones.ravel().astype(np.int64) * (n_metros + 1) + mid.ravel()
    size = (n_zones + 1) * (n_metros + 1)
    return np.bincount(pair, weights=pop.ravel(), minlength=size).reshape(n_zones + 1, n_metros + 1)


def members_of(zones, n_zones, grid) -> np.ndarray:
    """Boolean [zone, metro]: metro has at least MEMBER_SHARE of its gridded residents in the zone."""
    table = by_zone_and_metro(zones, n_zones, grid["mid"], grid["n"], grid["pop"])
    share = table / np.maximum(grid["metro_grid_pop"], 1)[None, :]
    member = share >= MEMBER_SHARE
    member[0, :] = False
    member[:, 0] = False
    return member


def find_regions(grid: dict, core_t: float, ext_t: float):
    """Returns (zone raster labelled 1..k, member matrix [k+1, metros]) for megaregions, largest first."""
    smooth, official = grid["smooth"], grid["official"]
    cores, n = ndi.label(smooth >= core_t)
    core_pop = members_of(cores, n, grid) @ official
    keep = np.flatnonzero(core_pop >= MIN_MEGA_POP)
    markers = np.where(np.isin(cores, keep), cores, 0)
    # Flooding the inverted density from each core makes neighbours meet on the lowest-density divide.
    grown = watershed(-smooth, markers, mask=smooth >= ext_t)
    pops = members_of(grown, n, grid) @ official
    order = sorted(keep, key=lambda z: -pops[z])
    relabel = np.zeros(n + 1, dtype=np.int32)
    relabel[order] = np.arange(1, len(order) + 1)
    zones = relabel[grown]
    return zones, members_of(zones, len(order), grid)


def region_name(names: list[str], majors: set[str]) -> str:
    inside = majors or set(names)
    return next((label for need, label in NAME_RULES if need <= inside), "–".join(names[:2]))


def km(geom):
    """Albers metres -> kilometres at 100 m precision, to keep the JSON small."""
    return shapely.transform(geom, lambda a: np.round(a / 1000, 1))


def describe(zones, member, grid, metros) -> list[dict]:
    land = grid["land"]
    out = []
    for z in range(1, member.shape[0]):
        ids = np.flatnonzero(member[z])
        ids = ids[np.argsort(-grid["official"][ids])]
        cells = (zones == z) & land
        polys = [shape(g) for g, _ in features.shapes(cells.astype("uint8"), mask=cells, transform=TRANSFORM)]
        geom = unary_union(polys).simplify(1500)
        names = [metros.name[i - 1] for i in ids]
        out.append({
            "name": region_name(names, {metros.name[i - 1] for i in ids if grid["official"][i] >= MAJOR_METRO_POP}),
            "pop": int(grid["official"][ids].sum()),
            "area_km2": int(cells.sum()),
            "metros": [metros.key[i - 1] for i in ids],
            "countries": sorted({metros.country[i - 1] for i in ids}),
            "geometry": shapely.geometry.mapping(km(geom)),
        })
    return out


def build_grid(metros) -> dict:
    pop = load_pop()
    mid = features.rasterize(zip(metros.geometry, metros.mid), out_shape=(H, W), transform=TRANSFORM, dtype="int32")
    n = len(metros)
    metro_grid_pop = np.bincount(mid.ravel(), weights=pop.ravel(), minlength=n + 1)
    rows, cols = np.indices(pop.shape)
    safe = np.maximum(metro_grid_pop, 1)
    cx = X0 + (np.bincount(mid.ravel(), weights=(pop * cols).ravel(), minlength=n + 1) / safe + 0.5) * CELL
    cy = Y1 - (np.bincount(mid.ravel(), weights=(pop * rows).ravel(), minlength=n + 1) / safe + 0.5) * CELL
    return {
        "pop": pop, "smooth": ndi.gaussian_filter(pop, SIGMA_KM), "mid": mid, "n": n,
        "metro_grid_pop": metro_grid_pop, "cx": cx, "cy": cy, "land": land_mask(),
        "official": np.concatenate([[0], metros["pop"].to_numpy()]).astype(float),
    }


def check_grid(grid, metros) -> None:
    """The grid must reproduce official metro populations, or every share and total is suspect."""
    big = metros["pop"].to_numpy() >= CIRCLE_MIN_POP
    ratio = grid["metro_grid_pop"][1:][big] / metros["pop"].to_numpy()[big]
    print(f"grid/official for metros >= 250k: median {np.median(ratio):.3f}, min {ratio.min():.3f}, max {ratio.max():.3f}")
    assert 0.9 < np.median(ratio) < 1.1, "GHSL grid and official metro estimates disagree"


def sweep(grid, metros) -> None:
    for core_t in SWEEP_CORES:
        for ext_t in SWEEP_EXTS:
            zones, member = find_regions(grid, core_t, ext_t)
            regions = describe(zones, member, grid, metros)
            print(f"\ncore {core_t} / ext {ext_t}: {len(regions)} megaregions, {sum(r['pop'] for r in regions)/1e6:.1f}M")
            for r in regions:
                tops = ", ".join(metros.set_index("key").name[r["metros"][:5]])
                print(f"  {r['pop']/1e6:5.1f}M  {r['name']:<24} {len(r['metros']):>3} metros: {tops}")


def basemap() -> dict:
    states = gpd.read_file(RAW / "ne_50m_admin_1_states_provinces_lakes.zip").query("adm0_a3 in @IN_SCOPE")
    others = gpd.read_file(RAW / "ne_50m_admin_0_countries_lakes.zip").query("ADM0_A3 not in @IN_SCOPE")

    def shapes(gdf):
        g = gdf.to_crs(CRS).clip(FRAME).geometry.simplify(2000)
        return [shapely.geometry.mapping(km(x)) for x in g if not x.is_empty]

    return {"states": shapes(states), "others": shapes(others)}


def write_glow(pop) -> None:
    pop = pop.reshape(H // GLOW_BLOCK, GLOW_BLOCK, W // GLOW_BLOCK, GLOW_BLOCK).max(axis=(1, 3))  # keep the densest cell
    t = np.clip((pop - GLOW_MIN) / (GLOW_FULL - GLOW_MIN), 0, 1)
    alpha = np.where(pop >= GLOW_MIN, 130 + 125 * np.sqrt(t), 0).astype("uint8")
    Image.merge("LA", [Image.new("L", alpha.shape[::-1], 255), Image.fromarray(alpha)]).save(SITE / "glow.png", optimize=True)


def totals() -> dict:
    us = pd.read_csv(RAW / f"NST-EST{YEAR}-ALLDATA.csv", dtype=str).query("SUMLEV == '010'")[f"POPESTIMATE{YEAR}"]
    ca = pd.read_csv(RAW / "17100148.csv", dtype=str, usecols=["REF_DATE", "GEO", "Gender", "Age group", "VALUE"])
    ca = ca[(ca.REF_DATE == YEAR) & (ca.GEO == "Canada") & (ca["Age group"] == "All ages") & (ca.Gender == "Total - gender")]
    area = countries().query("ADM0_A3 in @IN_SCOPE").to_crs(CRS).area.sum() / 1e6
    return {"pop": int(us.iloc[0]) + int(ca.VALUE.iloc[0]), "area_km2": int(area), "year": int(YEAR)}


def main() -> None:
    metros = load_metros()
    grid = build_grid(metros)
    check_grid(grid, metros)
    if "--sweep" in sys.argv:
        return sweep(grid, metros)

    SITE.mkdir(exist_ok=True)
    variants = {}
    for key, v in VARIANTS.items():
        zones, member = find_regions(grid, v["core"], v["ext"])
        regions = describe(zones, member, grid, metros)
        assert all(r["pop"] >= MIN_MEGA_POP for r in regions), "a megaregion fell under the 4M floor"
        variants[key] = {**v, "regions": regions}
        print(key, [(r["name"], round(r["pop"] / 1e6, 1)) for r in regions])

    in_region = {k for v in variants.values() for r in v["regions"] for k in r["metros"]}
    shown = metros[(metros["pop"] >= CIRCLE_MIN_POP) | metros.key.isin(in_region)]
    data = {
        "frame_km": [X0 / 1000, Y0 / 1000, X1 / 1000, Y1 / 1000],
        "totals": totals(),
        "default_variant": DEFAULT_VARIANT,
        "method": {"sigma_km": SIGMA_KM, "min_pop": MIN_MEGA_POP, "circle_min_pop": CIRCLE_MIN_POP, "glow_min": GLOW_MIN},
        "base": basemap(),
        "metros": [
            {"key": m.key, "name": m["name"], "full": m.full, "country": m.country, "pop": int(m["pop"]),
             "x": round(grid["cx"][m.mid] / 1000, 1), "y": round(grid["cy"][m.mid] / 1000, 1)}
            for _, m in shown.iterrows()
        ],
        "variants": variants,
    }
    (SITE / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    write_glow(grid["pop"])
    print(f"wrote {SITE/'data.json'} ({(SITE/'data.json').stat().st_size/1e6:.1f} MB) and glow.png")


if __name__ == "__main__":
    main()
