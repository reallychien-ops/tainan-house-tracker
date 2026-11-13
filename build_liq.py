#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把地調所土壤液化潛勢（gis.liquid.net.tw Soil_cgs_2026）台南範圍轉成網頁用的透明圖 liq.png。
跟 flood_650.png 同範圍、同像素（讀 flood_650.json 的 bounds/size），網頁和 daily_tracker 都讀這張圖，不再即時連那台伺服器（常常很慢或逾時）。
資料只在地調所改版時需要重跑：.venv/bin/python build_liq.py（伺服器慢，會自己重試）
顏色索引 1/2/3 = 低／中／高潛勢。
"""
import json
import math
import os
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = "https://gis.liquid.net.tw/arcgis/rest/services/P04032/Soil_cgs_2026/MapServer/0/query"
LEVEL = {"LOW": 1, "MED": 2, "HIGH": 3}
COLORS = [(0, 0, 0), (85, 255, 0), (255, 255, 0), (255, 0, 0)]  # 同地調所原圖：綠／黃／紅


def query(**params):
    url = SRC + "?" + urllib.parse.urlencode({**params, "f": "json"})
    for i in range(8):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=180).read())
        except Exception as e:
            print("retry", i + 1, e)
            time.sleep(20)
    raise RuntimeError("液化伺服器一直沒回應")


def merc(lat, lng):
    return 6378137 * math.radians(lng), 6378137 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def main():
    with open(os.path.join(BASE_DIR, "flood_650.json"), encoding="utf-8") as f:
        meta = json.load(f)
    (s, w), (n, e) = meta["bounds"]
    W, H = meta["size"]
    mx0, my0 = merc(s, w)
    mx1, my1 = merc(n, e)
    env = dict(geometry=f"{w},{s},{e},{n}", geometryType="esriGeometryEnvelope", inSR=4326, spatialRel="esriSpatialRelIntersects")
    ids = query(where="1=1", returnIdsOnly="true", **env)["objectIds"]
    print(len(ids), "塊")
    feats = []
    for i in range(0, len(ids), 3):
        chunk = ids[i:i + 3]
        feats += query(objectIds=",".join(map(str, chunk)), outFields="TYPE", outSR=3857, maxAllowableOffset=5, returnGeometry="true")["features"]
        print(len(feats), "/", len(ids))

    out = Image.new("P", (W, H), 0)
    for f in sorted(feats, key=lambda f: LEVEL.get(f["attributes"]["TYPE"], 0)):  # 高的蓋在低的上面
        c = LEVEL.get(f["attributes"]["TYPE"])
        if not c:
            continue
        mask = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(mask)
        # 大圈先畫、小圈後畫：洞裡面若還有一塊區域（洞中島），才不會被洞擦掉
        rings = [(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1])), r) for r in f["geometry"]["rings"]]
        for area, ring in sorted(rings, key=lambda t: -abs(t[0])):
            d.polygon([((x - mx0) / (mx1 - mx0) * W, (my1 - y) / (my1 - my0) * H) for x, y in ring], fill=1 if area < 0 else 0)  # 外圈順時針、洞逆時針
        out.paste(c, mask=mask)
    out.putpalette([v for col in COLORS for v in col] + [0] * (768 - 3 * len(COLORS)))
    out.save(os.path.join(BASE_DIR, "liq.png"), optimize=True, transparency=0)
    print("liq.png", os.path.getsize(os.path.join(BASE_DIR, "liq.png")) // 1024, "KB")


if __name__ == "__main__":
    main()
