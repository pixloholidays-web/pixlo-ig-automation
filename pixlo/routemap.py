"""Animated route-map shot for Reels: a line draws itself from the starting city to the destination.

Map shapes come from Natural Earth (public domain): land, rivers, lakes and city points. No country or state
borders are drawn, on purpose: border depictions are legally sensitive in India and add nothing to a route.
Everything is drawn with Pillow in the brand colours, so there is no map-tile service or attribution involved.
"""
import json
import math
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .carousel import _font, _rgb
from .config import ROOT

W, H = 1080, 1920
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
LAYERS = {"land": "ne_10m_land.geojson", "lakes": "ne_10m_lakes.geojson",
          "rivers": "ne_10m_rivers_lake_centerlines.geojson", "places": "ne_10m_populated_places_simple.geojson"}
GEO_DIR = ROOT / "media" / "cache" / "geo"
BOX = (150, 600, 930, 1240)  # the route is fitted inside this part of the frame (clear of the Reels UI)
SS = 2                       # the base map is drawn at 2x and scaled down, for smooth edges


@lru_cache(maxsize=None)
def _layer(name: str) -> list[dict]:
    path = GEO_DIR / LAYERS[name]
    if not path.exists():
        import requests
        resp = requests.get(NE_URL + LAYERS[name], timeout=120)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["features"]


def _mercator(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-85.0, min(85.0, lat))
    return math.radians(lon), -math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def valid(route: dict | None) -> bool:
    """True if the route has two usable, distinct points."""
    try:
        a, b = route["from"], route["to"]
        pts = [(float(p["lat"]), float(p["lon"])) for p in (a, b)]
    except (KeyError, TypeError, ValueError):
        return False
    if not all(-60 <= lat <= 75 and -180 <= lon <= 180 for lat, lon in pts) or not (a.get("name") and b.get("name")):
        return False
    return math.dist(pts[0], pts[1]) > 0.15  # closer than ~15 km: nothing to show


class _View:
    """Maps lon/lat to frame pixels so that both ends of the route sit inside BOX."""

    def __init__(self, a: tuple[float, float], b: tuple[float, float]):
        (ax, ay), (bx, by) = _mercator(*a), _mercator(*b)
        span_x, span_y = max(abs(ax - bx), 0.012), max(abs(ay - by), 0.012)
        self.scale = min((BOX[2] - BOX[0]) / span_x, (BOX[3] - BOX[1]) / span_y)
        self.ox = (BOX[0] + BOX[2]) / 2 - (ax + bx) / 2 * self.scale
        self.oy = (BOX[1] + BOX[3]) / 2 - (ay + by) / 2 * self.scale

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = _mercator(lon, lat)
        return x * self.scale + self.ox, y * self.scale + self.oy

    def bounds(self, margin: float = 0.3) -> tuple[float, float, float, float]:
        """lon/lat box of the visible frame plus a margin (degrees), for skipping far-away shapes."""
        lon0 = math.degrees((0 - self.ox) / self.scale) - margin
        lon1 = math.degrees((W - self.ox) / self.scale) + margin
        lat = lambda py: math.degrees(2 * math.atan(math.exp(-(py - self.oy) / self.scale)) - math.pi / 2)
        return lon0, lat(H) - margin, lon1, lat(0) + margin


def _ring_visible(ring: list, box: tuple) -> bool:
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    return not (max(lons) < box[0] or min(lons) > box[2] or max(lats) < box[1] or min(lats) > box[3])


def _polygons(geometry: dict):
    if geometry["type"] == "Polygon":
        yield geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        yield from geometry["coordinates"]


def _lines(geometry: dict):
    if geometry["type"] == "LineString":
        yield geometry["coordinates"]
    elif geometry["type"] == "MultiLineString":
        yield from geometry["coordinates"]


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _base_map(cfg: dict, view: _View, skip_names: set[str], keep_clear: list[tuple]) -> Image.Image:
    """Land, water and a few city names. keep_clear: (x, y, radius) spots where no city label may go."""
    colors = cfg["brand"]["colors"]
    dark, primary = _rgb(colors["dark"]), _rgb(colors["primary"])
    sea, land = dark, _mix(dark, primary, 0.34)
    water = _mix(dark, primary, 0.72)
    img = Image.new("RGB", (W * SS, H * SS), sea)
    d = ImageDraw.Draw(img)
    box = view.bounds()
    px = lambda ring: [(x * SS, y * SS) for x, y in (view.xy(p[0], p[1]) for p in ring)]

    for feature in _layer("land"):
        for poly in _polygons(feature["geometry"]):
            if not _ring_visible(poly[0], box):
                continue
            d.polygon(px(poly[0]), fill=land)
            for hole in poly[1:]:
                d.polygon(px(hole), fill=sea)
    for feature in _layer("lakes"):
        for poly in _polygons(feature["geometry"]):
            if _ring_visible(poly[0], box):
                d.polygon(px(poly[0]), fill=sea)
    for feature in _layer("rivers"):
        for line in _lines(feature["geometry"]):
            if _ring_visible(line, box):
                d.line(px(line), fill=water, width=3 * SS, joint="curve")
    img = img.resize((W, H), Image.LANCZOS)

    # A few nearby cities for orientation
    d = ImageDraw.Draw(img, "RGBA")
    font = _font(cfg["brand"]["font_regular"], 30)
    cities = []
    for feature in _layer("places"):
        p = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"][:2]
        if box[0] < lon < box[2] and box[1] < lat < box[3] and p.get("name") and p["name"].lower() not in skip_names:
            cities.append((p.get("pop_max") or 0, p["name"], view.xy(lon, lat)))
    placed: list[tuple[float, float]] = []
    for _, name, (x, y) in sorted(cities, reverse=True):
        if len(placed) >= 6:
            break
        if not (60 < x < W - 220 and 470 < y < 1400) or any(math.dist((x, y), q) < 130 for q in placed):
            continue
        if any(math.dist((x + 60, y), (cx, cy)) < r for cx, cy, r in keep_clear):
            continue
        placed.append((x, y))
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 150))
        d.text((x + 14, y - 19), name, font=font, fill=(255, 255, 255, 135))
    return img


def _bezier(a: tuple, b: tuple, n: int = 80) -> list[tuple[float, float]]:
    """A gentle arc from a to b."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    side = -1 if dx >= 0 else 1  # of the two perpendiculars, the one that bulges upwards on screen
    cx, cy = mx - side * dy * 0.18, my + side * dx * 0.18
    return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * cx + t * t * b[0],
             (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * cy + t * t * b[1]) for t in (i / n for i in range(n + 1))]


def _pill(d: ImageDraw.ImageDraw, anchor: tuple, text: str, font, bg: tuple, fg: tuple, above: bool, alpha: float):
    tw = d.textlength(text, font=font)
    w, h = tw + 52, font.size + 34
    x = min(max(anchor[0] - w / 2, 40), W - 40 - w)
    y = anchor[1] - h - 34 if above else anchor[1] + 34
    a = round(255 * alpha)
    d.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=(*bg, a))
    d.text((x + 26, y + 13), text, font=font, fill=(*fg, a))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def frames(cfg: dict, route: dict, n: int):
    """Yield n RGB frames (H x W x 3 uint8) of the route drawing itself."""
    brand, colors = cfg["brand"], cfg["brand"]["colors"]
    accent, dark, white = _rgb(colors["accent"]), _rgb(colors["dark"]), _rgb(colors["text"])
    a_ll = (float(route["from"]["lon"]), float(route["from"]["lat"]))
    b_ll = (float(route["to"]["lon"]), float(route["to"]["lat"]))
    view = _View(a_ll, b_ll)
    a, b = view.xy(*a_ll), view.xy(*b_ll)
    path = _bezier(a, b)
    clear = [(a[0], a[1], 210), (b[0], b[1], 240)] + [(x, y, 75) for x, y in path[::4]]
    base = _base_map(cfg, view, {route["from"]["name"].lower(), route["to"]["name"].lower()}, clear)
    f_small, f_big = _font(brand["font_bold"], 44), _font(brand["font_black"], 58)
    f_note = _font(brand["font_semibold"], 46)
    b_above = b[1] <= a[1]  # the destination's label goes on the far side from the start
    note = (route.get("note") or "").strip()

    for i in range(n):
        p = _ease(i / max(1, round(n * 0.62)))       # the line finishes at ~62% of the shot
        img = base.convert("RGBA")
        d = ImageDraw.Draw(img, "RGBA")
        upto = max(2, round(p * (len(path) - 1)) + 1)
        seg = path[:upto]
        d.line(seg, fill=(*dark, 190), width=18, joint="curve")
        for k in range(0, len(seg) - 1, 3):          # dashes
            d.line(seg[k:k + 3], fill=(*accent, 255), width=9, joint="curve")
        hx, hy = seg[-1]
        d.ellipse((hx - 13, hy - 13, hx + 13, hy + 13), fill=(*accent, 255))

        d.ellipse((a[0] - 17, a[1] - 17, a[0] + 17, a[1] + 17), fill=(*white, 255), outline=(*dark, 255), width=6)
        _pill(d, a, route["from"]["name"], f_small, dark, white, above=not b_above, alpha=_ease(i / 6))
        if p >= 1:
            pop = _ease((i - round(n * 0.62)) / 5)   # the pin pops in when the line arrives
            r = 26 * pop
            d.ellipse((b[0] - r - 12, b[1] - r - 12, b[0] + r + 12, b[1] + r + 12), fill=(*accent, 70))
            d.ellipse((b[0] - r, b[1] - r, b[0] + r, b[1] + r), fill=(*accent, 255), outline=(*dark, 255), width=6)
            _pill(d, b, route["to"]["name"], f_big, accent, dark, above=b_above, alpha=pop)
            if note:  # a heading above the map, clear of both labels
                tw = d.textlength(note, font=f_note)
                d.rounded_rectangle(((W - tw) / 2 - 34, 352, (W + tw) / 2 + 34, 352 + 84), radius=42,
                                    fill=(8, 14, 20, round(175 * pop)))
                d.text(((W - tw) / 2, 352 + 12), note, font=f_note, fill=(*white, round(255 * pop)))

        frame = np.asarray(img.convert("RGB"))
        z = 1.0 + 0.05 * i / max(1, n - 1)           # slow push-in so the map never sits still
        m = cv2.getRotationMatrix2D((W / 2, (BOX[1] + BOX[3]) / 2), 0, z)
        yield cv2.warpAffine(frame, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
