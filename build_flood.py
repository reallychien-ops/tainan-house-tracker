#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把水利署第三代淹水潛勢圖「定量降雨 24 小時 650mm」（2022-08 版，全國檔）的臺南市部分，轉成網頁用的透明圖 flood_650.png + flood_650.json。
資料只在水利署改版時需要重跑：.venv/bin/python build_flood.py
來源：政府資料開放平臺 dataset 25766（水利署淹水潛勢圖）→ 水利空間資訊服務平台下載 flood_650mm_24hr SHP
（水利署地圖伺服器 maps.wra.gov.tw 有防爬蟲驗證碼，所以不即時連線查詢，改用這份下載檔）
圖片的像素是 Web Mercator 等距，Leaflet imageOverlay 可直接疊；顏色索引 1~5 = 淹水 0.3-0.5 / 0.5-1 / 1-2 / 2-3 / >3 公尺。
"""
import json
import math
import os
import tempfile
import urllib.request
import zipfile

import shapefile  # pyshp
from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_URL = "https://gic.wra.gov.tw/gis/gic/API/Google/DownLoad.aspx?fname=flood_650mm_24hr&filetype=SHP"
SHP_IN_ARCHIVE = "Flood_650mm_24HR/Flood_650mm_24HR"
CITY = "臺南市"
PX_M = 20  # 每像素幾公尺（Mercator 公尺，台南約等於地面 18m）
COLORS = [(0, 0, 0), (159, 216, 255), (79, 176, 245), (31, 127, 224), (24, 71, 184), (23, 15, 115)]  # 藍色系，和液化的綠黃紅分開


def tm2_to_wgs84(x, y):
    """TWD97 TM2（EPSG:3826）→ 經緯度（GRS80，中央經線 121，k0 0.9999，東偏 250000）"""
    a, b = 6378137.0, 6356752.314245
    lon0, k0, dx = math.radians(121), 0.9999, 250000
    e = math.sqrt(1 - (b / a) ** 2)
    x -= dx
    M = y / k0
    mu = M / (a * (1 - e ** 2 / 4 - 3 * e ** 4 / 64 - 5 * e ** 6 / 256))
    e1 = (1 - math.sqrt(1 - e ** 2)) / (1 + math.sqrt(1 - e ** 2))
    fp = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    e2 = (e * a / b) ** 2
    C1, T1 = e2 * math.cos(fp) ** 2, math.tan(fp) ** 2
    R1 = a * (1 - e ** 2) / (1 - e ** 2 * math.sin(fp) ** 2) ** 1.5
    N1 = a / math.sqrt(1 - e ** 2 * math.sin(fp) ** 2)
    D = x / (N1 * k0)
    lat = fp - (N1 * math.tan(fp) / R1) * (D ** 2 / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * e2) * D ** 4 / 24
                                          + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * e2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = lon0 + (D - (1 + 2 * T1 + C1) * D ** 3 / 6 + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * e2 + 24 * T1 ** 2) * D ** 5 / 120) / math.cos(fp)
    return math.degrees(lat), math.degrees(lon)


def merc(lat, lng):
    return 6378137 * math.radians(lng), 6378137 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def main():
    tmp = tempfile.mkdtemp()
    arc = os.path.join(tmp, "flood.zip")
    urllib.request.urlretrieve(SRC_URL, arc)
    zipfile.ZipFile(arc).extractall(tmp)
    r = shapefile.Reader(os.path.join(tmp, SHP_IN_ARCHIVE), encoding="utf-8")
    items = [(rec["Class"], shp) for rec, shp in zip(r.records(), r.shapes()) if rec["CityName"] == CITY]

    xmin = min(shp.bbox[0] for _, shp in items)
    ymin = min(shp.bbox[1] for _, shp in items)
    xmax = max(shp.bbox[2] for _, shp in items)
    ymax = max(shp.bbox[3] for _, shp in items)
    (s, w), (n, e) = tm2_to_wgs84(xmin, ymin), tm2_to_wgs84(xmax, ymax)
    s, w = min(s, tm2_to_wgs84(xmax, ymin)[0]), min(w, tm2_to_wgs84(xmin, ymax)[1])
    n, e = max(n, tm2_to_wgs84(xmin, ymax)[0]), max(e, tm2_to_wgs84(xmax, ymin)[1])
    mx0, my0 = merc(s, w)
    mx1, my1 = merc(n, e)
    W, H = math.ceil((mx1 - mx0) / PX_M), math.ceil((my1 - my0) / PX_M)

    def px(pt):
        mx, my = merc(*tm2_to_wgs84(*pt))
        return (mx - mx0) / PX_M, (my1 - my) / PX_M

    out = Image.new("P", (W, H), 0)
    for c, shp in sorted(items, key=lambda x: x[0]):  # Class 1..5 = 0.3-0.5 … >3 公尺，深的蓋在淺的上面
        mask = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(mask)
        bounds = list(shp.parts) + [len(shp.points)]
        for i in range(len(shp.parts)):
            ring = shp.points[bounds[i]:bounds[i + 1]]
            area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))
            d.polygon([px(p) for p in ring], fill=1 if area < 0 else 0)  # shapefile 外圈順時針、洞逆時針
        out.paste(c, mask=mask)

    pal = [v for col in COLORS for v in col] + [0] * (768 - 3 * len(COLORS))
    out.putpalette(pal)
    out.save(os.path.join(BASE_DIR, "flood_650.png"), optimize=True, transparency=0)
    meta = {"bounds": [[round(s, 6), round(w, 6)], [round(n, 6), round(e, 6)]], "size": [W, H],
            "levels": ["", "0.3-0.5", "0.5-1.0", "1.0-2.0", "2.0-3.0", ">3.0"],
            "source": "經濟部水利署第三代淹水潛勢圖（定量降雨 24 小時 650mm，2022-08 版，臺南市）", "source_url": SRC_URL}
    with open(os.path.join(BASE_DIR, "flood_650.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(meta, os.path.getsize(os.path.join(BASE_DIR, "flood_650.png")) // 1024, "KB")


if __name__ == "__main__":
    main()
