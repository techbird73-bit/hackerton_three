# -*- coding: utf-8 -*-
"""
collect_facilities.py
복지시설 데이터 수집 + VWorld 지오코딩 → CSV 저장
- 사회보장정보원 사회복지시설정보 API (전수 페이징 수집)
- 주소 결합(fcltAddr + fcltDtl_1Addr) → VWorld 지오코딩 → 위경도
- 실패분 로깅, 결과 캐싱(이미 변환한 주소 재호출 방지)

키는 코드에 박지 말고 환경변수로:
  export DATA_GO_KR_KEY="..."   (공공데이터포털 Decoding 키)
  export VWORLD_KEY="..."       (VWorld 인증키)
"""
import os
import time
import json
import xml.etree.ElementTree as ET

import requests
import pandas as pd

DATA_KEY = os.environ.get("DATA_GO_KR_KEY", "")
VWORLD_KEY = os.environ.get("VWORLD_KEY", "")

FCLT_ENDPOINT = (
    "https://apis.data.go.kr/B554287/"
    "sclWlfrFcltInfoInqirService1/getFcltByBassInfoInqire"
)
VWORLD_ENDPOINT = "https://api.vworld.kr/req/address"

CACHE_PATH = "geocode_cache.json"


# ──────────────────────────────────────────────
# 1. 복지시설 전수 수집 (페이징)
# ──────────────────────────────────────────────
def fetch_all_facilities(num_rows=100, max_pages=1000, sleep=0.2):
    """totalCount를 먼저 받아 전 페이지를 순회 수집."""
    rows = []
    page = 1
    while page <= max_pages:
        params = {
            "serviceKey": DATA_KEY,
            "pageNo": page,
            "numOfRows": num_rows,
        }
        r = requests.get(FCLT_ENDPOINT, params=params, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)

        items = root.findall(".//item")
        if not items:
            break

        for it in items:
            rows.append({c.tag: (c.text or "").strip() for c in it})

        total = root.findtext(".//totalCount")
        if total and page * num_rows >= int(total):
            break
        page += 1
        time.sleep(sleep)

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 2. 주소 결합
# ──────────────────────────────────────────────
def build_address(row):
    """fcltAddr + fcltDtl_1Addr(건물번호만) 결합. 법정동 괄호 제거."""
    base = (row.get("fcltAddr") or "").strip()
    detail = (row.get("fcltDtl_1Addr") or "").strip()
    # "16 (체부동)" → "16"
    if "(" in detail:
        detail = detail.split("(")[0].strip()
    return f"{base} {detail}".strip()


# ──────────────────────────────────────────────
# 3. VWorld 지오코딩 (캐시 적용)
# ──────────────────────────────────────────────
def _load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def geocode_vworld(address, cache, sleep=0.1):
    """도로명(ROAD) 우선, 실패 시 지번(PARCEL) 재시도. (lon, lat) 반환."""
    if address in cache:
        return cache[address]

    for addr_type in ("ROAD", "PARCEL"):
        params = {
            "service": "address",
            "request": "getcoord",
            "version": "2.0",
            "crs": "EPSG:4326",
            "address": address,
            "type": addr_type,
            "format": "json",
            "key": VWORLD_KEY,
        }
        try:
            r = requests.get(VWORLD_ENDPOINT, params=params, timeout=10)
            data = r.json()
            if data["response"]["status"] == "OK":
                p = data["response"]["result"]["point"]
                result = (float(p["x"]), float(p["y"]))  # (lon, lat)
                cache[address] = result
                time.sleep(sleep)
                return result
        except Exception:
            pass
        time.sleep(sleep)

    cache[address] = None
    return None


# ──────────────────────────────────────────────
# 4. 파이프라인
# ──────────────────────────────────────────────
def main(out_path="facilities_geocoded.csv"):
    print("[1/3] 복지시설 수집 중...")
    df = fetch_all_facilities()
    print(f"  수집: {len(df)}건")

    df["full_address"] = df.apply(build_address, axis=1)

    print("[2/3] VWorld 지오코딩 중...")
    cache = _load_cache()
    lons, lats, fails = [], [], 0
    for i, addr in enumerate(df["full_address"]):
        res = geocode_vworld(addr, cache)
        if res:
            lons.append(res[0]); lats.append(res[1])
        else:
            lons.append(None); lats.append(None); fails += 1
        if (i + 1) % 50 == 0:
            _save_cache(cache)
            print(f"  {i+1}/{len(df)} 처리 (실패 {fails})")
    _save_cache(cache)

    df["lon"], df["lat"] = lons, lats
    ok = df.dropna(subset=["lon", "lat"])
    print(f"[3/3] 완료: 성공 {len(ok)}건, 실패 {fails}건")

    ok.to_csv(out_path, index=False, encoding="utf-8-sig")
    if fails:
        df[df["lon"].isna()][["fcltNm", "full_address"]].to_csv(
            "geocode_failed.csv", index=False, encoding="utf-8-sig"
        )
    print(f"저장: {out_path}")
    return ok


if __name__ == "__main__":
    main()
