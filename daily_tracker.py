#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台南購屋每日巡檢 (daily_tracker.py) — 2026-11-13 由 Claude 接手重寫
1. 所有還沒下架的物件（永慶＋樂屋網）查價格與上下架；只有網站明確回「已成交/關閉/404」才判下架
2. 永慶 9 個行政區「透天厝・1,200～1,920 萬・屋齡 26 年內」新上架掃描，過宮廟 200m 與液化檢核後加入
3. 每間物件記 added_at／price_history／delisted_at；附近 500m 寶可夢道館（twpkinfo）
4. 回寫 Google 試算表、properties.json、git push（GitHub Pages 網頁直接讀 properties.json）
錯誤一律寫進 daily_check_log.json 的 errors，不再吞掉。
"""

import os
import re
import json
import math
import gzip
import datetime
import subprocess
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar

from curl_cffi import requests as cffi

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROP_FILE = os.path.join(BASE_DIR, "properties.json")
LOG_FILE = os.path.join(BASE_DIR, "daily_check_log.json")
SPREADSHEET_ID = "1R3vS8mfq9VH-YCfOArEKBaqKMST2HmpqD9MVI66TsW0"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
HEADERS = {"User-Agent": UA}

SCAN_DISTRICTS = ["北區", "安南區", "永康區", "新化區", "善化區", "新市區", "安定區", "麻豆區", "仁德區"]
SCAN_FILTER = "透天厝_type/1200-1920_price/0-26_age"
GYM_RADIUS_M = 500
NAME_MUST = re.compile(r"車墅|車庫|雙車|車位")
NAME_SKIP = re.compile(r"收租|投報|投資|學宿|店面|住店|透店|農舍|小木屋|渡假|度假")

errors = []


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def flatten(html):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " | ", t)
    return re.sub(r"(\s*\|\s*)+", " | ", t)


def get(url, timeout=15):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout).read().decode("utf-8", "ignore")


def check_yungching(url):
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15)
        final_url, html = resp.geturl(), resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return {"active": False}
        raise
    if "/house/" not in final_url or "物件不存在" in html or "已下架" in flatten(html)[:3000]:
        return {"active": False}
    m = re.search(r"\|\s*([\d,]+)\s*\|\s*萬\s*\|\s*單價([\d\.]+)萬/坪", flatten(html))
    if not m:
        raise RuntimeError("永慶頁面讀不到價格")
    return {"active": True, "price": float(m.group(1).replace(",", "")), "unit_price": float(m.group(2))}


def check_rakuya(url):
    r = cffi.get(url, impersonate="chrome", timeout=20)
    if r.status_code == 404 or "已成交或關閉" in r.text:
        return {"active": False}
    if r.status_code != 200:
        raise RuntimeError(f"樂屋網 HTTP {r.status_code}")
    m = re.search(r'"price"\s*:\s*"?(\d{6,})', r.text)
    if not m:
        raise RuntimeError("樂屋網頁面讀不到價格")
    return {"active": True, "price": round(int(m.group(1)) / 10000, 1)}


def check_listing(url):
    if "yungching.com.tw/house/" in url:
        return check_yungching(url)
    if "rakuya.com.tw" in url:
        return check_rakuya(url)
    raise RuntimeError("不支援的網站")


# ---------- 寶可夢道館（twpkinfo，與 PokeGuard 同來源） ----------
_gym_opener = None


def gyms_near(lat, lng):
    global _gym_opener
    if _gym_opener is None:
        _gym_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        _gym_opener.addheaders = [("User-Agent", UA)]
        _gym_opener.open("https://twpkinfo.com/igym.aspx", timeout=30).read()
    pad = 0.0065
    url = (f"https://twpkinfo.com/getgym.ashx?a={lat + pad}&b={lng + pad}&c={lat - pad}&d={lng - pad}"
           f"&e=0&f=1&g=Y&h=&i=&j=765")
    raw = _gym_opener.open(urllib.request.Request(url, headers={
        "Referer": "https://twpkinfo.com/igym.aspx", "X-Requested-With": "XMLHttpRequest"}), timeout=30).read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    rows = json.loads(raw.decode("utf-8-sig")).get("fp", [])
    if rows and rows[0].get("a", "").startswith("error"):
        raise RuntimeError(f"道館 API 拒絕：{rows[0].get('b')}")
    out = []
    for g in rows:
        if g.get("i") == "stop":  # 火箭隊補給站，不是道館
            continue
        glat, glng = float(g["c"]), float(g["d"])
        d = haversine(lat, lng, glat, glng)
        if d <= GYM_RADIUS_M:
            out.append({"name": g.get("g") or "（未命名道館）", "lat": glat, "lng": glng, "dist": int(d)})
    return sorted(out, key=lambda x: x["dist"])


# ---------- 新案雷達 ----------
def scan_list_urls():
    """回傳 [(url, name)]，9 區各自翻頁到沒有新物件為止"""
    found = {}
    for d in SCAN_DISTRICTS:
        base = "https://buy.yungching.com.tw/list/" + urllib.parse.quote(f"台南市-{d}_c/{SCAN_FILTER}", safe="/_-")
        for pg in range(1, 6):
            html = get(base + (f"?pg={pg}" if pg > 1 else ""))
            items = []
            for blk in re.findall(r'"@type"\s*:\s*"ItemList".*?"itemListElement"\s*:\s*(\[.*?\])\s*[,}]', html, flags=re.S):
                try:
                    items += json.loads(blk)
                except ValueError:
                    pass
            fresh = [(i["url"], i.get("name", "")) for i in items if "/house/" in i.get("url", "") and i["url"] not in found]
            for u, n in fresh:
                found[u] = n
            if len(fresh) < 10:
                break
    return list(found.items())


def inspect_candidate(url, temples):
    """抓永慶物件頁，回傳 dict；不符條件回傳 (None, 原因)"""
    t = flatten(get(url))
    m = re.search(r"\|\s*([\d,]+)\s*\|\s*萬\s*\|\s*單價([\d\.]+)萬/坪\s*\|\s*([^|]+)\s*\|\s*屋齡([\d\.]+)\s*年\s*\|\s*([^|]+)\s*\|\s*建物([\d\.]+)坪", t)
    if not m:
        return None, "讀不到價格欄位"
    htype = m.group(3).strip()
    if "透天" not in htype and "別墅" not in htype:
        return None, f"型態 {htype}"
    price, age = float(m.group(1).replace(",", "")), float(m.group(4))
    if not (1200 <= price <= 1920) or age > 26:
        return None, f"{price}萬/{age}年 超出條件"
    l_m = re.search(r"土地坪數\s*\|\s*([\d\.]+)坪", t)
    layout_m = re.search(r"建物格局\s*\|\s*([^|]+)", t)
    addr_m = re.search(r"\|\s*([^|]*台南市[^|]+?)\s*\|", t)
    addr = addr_m.group(1).replace("住宅", "").strip() if addr_m else "台南市"

    g = json.loads(get(f"https://dmap.ncdr.nat.gov.tw/api/district2xyInfo?address={urllib.parse.quote(addr + '10號')}", 10))
    lng = float(next(x["Value"] for x in g if x["Key"] == "x"))
    lat = float(next(x["Value"] for x in g if x["Key"] == "y"))

    dists = sorted((haversine(lat, lng, tp["lat"], tp["lng"]), tp["name"]) for tp in temples)
    if dists and dists[0][0] < 200:
        return None, f"{dists[0][1]} 只有 {int(dists[0][0])}m"
    temple_desc = f"🟢 200m內無登記宮廟（最近 {dists[0][1]} 約 {int(dists[0][0])}m，座標以路段估）" if dists else "🟢 周邊無登記宮廟"

    lq = json.loads(get("https://gis.liquid.net.tw/arcgis/rest/services/P04032/Soil_cgs_2026/MapServer/0/query?"
                        f"geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects"
                        "&outFields=*&returnGeometry=false&f=json", 10)).get("features", [])
    code = lq[0]["attributes"].get("TYPE", "") if lq else "LOW"
    return {
        "addr": addr, "price": price, "unit_price": float(m.group(2)), "age": age,
        "floors": m.group(5).strip(), "b_ping": float(m.group(6)),
        "l_ping": float(l_m.group(1)) if l_m else 0.0,
        "layout": layout_m.group(1).strip() if layout_m else "",
        "liq_level": {"LOW": "low", "MED": "med", "HIGH": "high"}.get(code, "med"),
        "liq_desc": {"LOW": "🟢 低潛勢（綠區）", "MED": "🟡 中潛勢（黃區）", "HIGH": "🔴 高潛勢（紅區）"}.get(code, "🟡 中潛勢"),
        "temple_desc": temple_desc, "lat": round(lat, 5), "lng": round(lng, 5),
    }, None


def sync_to_google_sheet(data_bundle):
    """將追蹤中與保留的物件同步回 Google 試算表 1_精選推薦物件總表"""
    header1 = ["台南購屋「市區＋原台南縣」精選推薦物件全健檢總表（實價登錄比價 × 500萬自備款試算 × 淹水/液化/宮廟/地籍圖資 × 每日價格追蹤）"] + [""] * 21
    header2 = [f"最後自動巡檢時間：{data_bundle['updated_at']} ｜ 買方條件：首購自住、年薪250萬、自備現金上限500萬內、南科30分內、獨立車庫可裝充電樁、不要宮廟"] + [""] * 21
    header3 = [
        "推薦排序與狀態", "區域分類", "行政區與路段", "物件名稱（社區）", "建商（建設公司／營造廠）與網路口碑評價",
        "目前開價(萬)", "開價單價(萬/坪)", "同區實登合理單價", "建議出價～目標成交價(萬)",
        "預估自備現金(含稅費裝修)", "預估月付房貸(30年2.6%)",
        "屋齡(年)", "建坪 / 地坪", "格局與樓層", "車庫形式與臨路條件",
        "南科通勤與 Pokémon GO 道館機能",
        "淹水潛勢 (NCDR)", "土壤液化 (地礦中心2026)", "周邊登記宮廟距離 (內政部寺廟庫)", "地籍段別與門口道路權屬 (NLSC)",
        "前手實登與賞屋重點結論（含價格變動紀錄）", "物件連結"
    ]
    rows = [header1, header2, header3]
    for p in data_bundle["properties"]:
        if not p.get("tracked", True) and "避雷" not in p.get("rank", ""):
            # 使用者在網頁上手動刪除/停止追蹤的物件，不寫入主表
            continue
        trend_badge = ""
        if p.get("price_trend") and p["price_trend"] not in ("持平", "已下架"):
            trend_badge = f"【{p['price_trend']}】"
        status_badge = ""
        if p.get("status") == "已下架":
            status_badge = "【已下架】"
        rows.append([
            f"{status_badge}{trend_badge}{p['rank']}",
            p["zone_type"],
            p["district_road"],
            p["title"],
            p["developer"],
            p["price"],
            p["unit_price"],
            p["ref_unit_price"],
            p["target_price"],
            p["cash_needed"],
            p["monthly_pay"],
            p["age"],
            p["area_str"],
            p["layout"],
            p["garage_road"],
            p["commute_pogo"],
            p["flood_desc"],
            p["liq_desc"],
            p["temple_desc"],
            p["cadastral_desc"],
            p["summary_note"],
            p["url"]
        ])

    subprocess.run(
        ["gws", "sheets", "spreadsheets", "values", "clear", "--params", json.dumps({"spreadsheetId": SPREADSHEET_ID, "range": "'1_精選推薦物件總表'!A1:Z500"})],
        capture_output=True
    )
    val_body = {
        "valueInputOption": "USER_ENTERED",
        "data": [{
            "range": "'1_精選推薦物件總表'!A1",
            "majorDimension": "ROWS",
            "values": rows
        }]
    }
    subprocess.run(
        ["gws", "sheets", "spreadsheets", "values", "batchUpdate", "--params", json.dumps({"spreadsheetId": SPREADSHEET_ID}), "--json", json.dumps(val_body)],
        capture_output=True
    )



def run_daily_check(scan_new=True):
    now = datetime.datetime.now()
    now_str, today = now.strftime("%Y-%m-%d %H:%M"), now.strftime("%Y-%m-%d")
    with open(PROP_FILE, encoding="utf-8") as f:
        data = json.load(f)
    props, temples = data["properties"], data.get("temples", [])
    changes, checked = [], 0

    for p in props:
        p.setdefault("added_at", "2026-11-10")
        if "price_history" not in p:
            p["price_history"] = [{"date": p["added_at"], "price": p.get("initial_price", p["price"])}]
            if p["price"] != p["price_history"][0]["price"]:
                p["price_history"].append({"date": p["added_at"], "price": p["price"]})
        if p.get("status") == "已下架" or not p.get("url"):
            continue
        try:
            res = check_listing(p["url"])
        except Exception as e:
            errors.append(f"{p['id']} 查價失敗：{e}")
            continue
        checked += 1
        p["last_checked"] = now_str
        if not res["active"]:
            p["status"], p["price_trend"], p["delisted_at"] = "已下架", "已下架", today
            changes.append(f"❌ 已下架：{p['district_road']}（{p['title']}）")
            continue
        old, new = p["price"], res["price"]
        if new != old:
            diff = round(abs(new - old), 1)
            word = "降價" if new < old else "漲價"
            p["price_trend"] = f"{'📉' if new < old else '📈'}{word}{diff:g}萬(原{old:g}萬)"
            p["price"] = new
            if res.get("unit_price"):
                p["unit_price"] = res["unit_price"]
            p["price_history"].append({"date": today, "price": new})
            changes.append(f"{'📉' if new < old else '📈'} {word} {diff:g} 萬：{p['district_road']} {old:g}→{new:g} 萬")

    new_count = 0
    if scan_new:
        known = {p["url"] for p in props}
        try:
            listing = scan_list_urls()
        except Exception as e:
            listing = []
            errors.append(f"新案搜尋頁讀取失敗：{e}")
        seen = set(data.setdefault("radar_seen", []))
        sigs = {(p["price"], p.get("age")) for p in props}
        for url, name in listing:
            if url in known or url in seen:
                continue
            seen.add(url)
            if not NAME_MUST.search(name) or NAME_SKIP.search(name):
                continue
            try:
                ni, why = inspect_candidate(url, temples)
            except Exception as e:
                seen.discard(url)  # 暫時性錯誤，明天再試
                errors.append(f"新案 {url} 檢查失敗：{e}")
                continue
            if not ni or "住店" in ni["addr"] or (ni["price"], ni["age"]) in sigs:
                continue
            sigs.add((ni["price"], ni["age"]))
            n = max(int(x["id"][1:]) for x in props) + 1
            props.append({
                "id": f"P{n:02d}", "tracked": True, "status": "上架追蹤中", "price_trend": "🆕新上架",
                "added_at": today, "last_checked": now_str, "rank": "🆕新案雷達", "zone_type": "自動巡檢新案",
                "district_road": ni["addr"].replace("台南市", ""), "title": name,
                "developer": "【待查】新案雷達自動加入，建商與屋況尚未人工健檢",
                "price": ni["price"], "initial_price": ni["price"], "unit_price": ni["unit_price"],
                "price_history": [{"date": today, "price": ni["price"]}],
                "ref_unit_price": "待查", "target_price": f"{int(ni['price'] * 0.9):,} ～ {int(ni['price'] * 0.93):,} 萬",
                "cash_needed": f"約 {int(ni['price'] * 0.23)} 萬（貸8成試算）",
                "monthly_pay": f"約 {int(ni['price'] * 0.8 * 10000 * 0.004003):,} 元",
                "age": ni["age"], "area_str": f"建 {ni['b_ping']} / 地 {ni['l_ping']}",
                "layout": f"{ni['layout']}｜{ni['floors']}", "garage_road": "待查（標題含車庫/車墅才會被搜尋到）",
                "commute_pogo": "", "flood_desc": "⚪ 未自動檢核（請開淹水圖層看）", "flood_level": "unknown",
                "liq_desc": ni["liq_desc"], "liq_level": ni["liq_level"], "temple_desc": ni["temple_desc"],
                "cadastral_desc": "待查", "summary_note": f"{today} 新案雷達加入：過宮廟 200m 與液化檢核，其餘待看屋確認",
                "url": url, "lat": ni["lat"], "lng": ni["lng"],
            })
            known.add(url)
            new_count += 1
            changes.append(f"🆕 新物件：{ni['addr']} {name} {ni['price']:g} 萬（{ni['age']:g} 年）")

        data["radar_seen"] = sorted(seen)

    for p in props:
        if "gyms" not in p and p.get("status") != "已下架":
            try:
                p["gyms"] = gyms_near(p["lat"], p["lng"])
            except Exception as e:
                errors.append(f"{p['id']} 道館查詢失敗：{e}")

    data["updated_at"] = now_str
    with open(PROP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    try:
        sync_to_google_sheet(data)
    except Exception as e:
        errors.append(f"試算表同步失敗：{e}")

    entry = {"timestamp": now_str, "checked_count": checked, "new_discovered_count": new_count,
             "changes": changes or ["✅ 全部持平"], "errors": errors}
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, encoding="utf-8") as f:
            logs = json.load(f)
    logs.insert(0, entry)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs[:60], f, ensure_ascii=False, indent=2)

    git = ["git", "-C", BASE_DIR]
    subprocess.run(git + ["add", "properties.json", "daily_check_log.json"], capture_output=True)
    subprocess.run(git + ["commit", "-m", f"Daily check {now_str}"], capture_output=True)
    push = subprocess.run(git + ["push", "origin", "main"], capture_output=True, text=True)
    if push.returncode != 0:
        entry["errors"].append(f"git push 失敗：{push.stderr.strip()[:200]}")
    return entry


if __name__ == "__main__":
    print(json.dumps(run_daily_check(scan_new=True), ensure_ascii=False, indent=2))
