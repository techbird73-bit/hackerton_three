# -*- coding: utf-8 -*-
"""
복지시설 접근성 분석 (도보 10분 등시선 + 2SFCA)
─────────────────────────────────────────────
- 지도 클릭으로 분석 지점 선택
- OSMnx로 해당 지점 도보 네트워크 다운로드
- ego_graph 기반 도보 N분 등시선(isochrone) 생성
- 등시선 내 복지시설 탐색 + 거리/도달시간
- 2SFCA로 지역 접근성 지수 산출 → 사각지대 시각화
- Folium 지도에 등시선·시설·접근성 히트맵 표출

데이터: facilities_geocoded.csv (collect_facilities.py 산출물)
        컬럼: fcltNm, cfbNm, lon, lat, (capacity 옵션)
"""
import os

import numpy as np
import pandas as pd
import streamlit as st
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
from shapely.ops import unary_union
from scipy.spatial import cKDTree

st.set_page_config(page_title="복지시설 접근성 분석", page_icon="🗺️", layout="wide")

# OSMnx는 무거우므로 지연 임포트(설치 안내 위해)
try:
    import osmnx as ox
    import networkx as nx
    import geopandas as gpd
    OSMNX_OK = True
except Exception as e:
    OSMNX_OK = False
    _IMPORT_ERR = str(e)

WALK_SPEED_KMH = 4.5          # 도보 속도
WALK_MPM = WALK_SPEED_KMH * 1000 / 60   # 분당 이동거리(m)
DATA_PATH = "facilities_geocoded.csv"


# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────
@st.cache_data
def load_facilities(path=DATA_PATH):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df = df.dropna(subset=["lon", "lat"])
    if "capacity" not in df.columns:
        df["capacity"] = 100   # 수용력 정보 없으면 균일 가정
    return df


# ──────────────────────────────────────────────
# 도보 네트워크 + 이동시간 부여
# ──────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def load_walk_graph(lat, lon, dist=1200):
    """클릭 지점 주변 도보 네트워크. 각 엣지에 도보 이동시간(분) 부여."""
    G = ox.graph_from_point((lat, lon), dist=dist, network_type="walk")
    for u, v, k, data in G.edges(keys=True, data=True):
        length = data.get("length", 0)        # m
        data["time"] = length / WALK_MPM       # 분
    return G


# ──────────────────────────────────────────────
# 등시선 폴리곤 (ego_graph + node/edge buffer)
# ──────────────────────────────────────────────
def make_isochrone(G, center_node, trip_time, node_buff=25, edge_buff=15):
    sub = nx.ego_graph(G, center_node, radius=trip_time, distance="time")
    if len(sub.nodes()) == 0:
        return None, sub
    coord = {n: (d["x"], d["y"]) for n, d in sub.nodes(data=True)}
    pts = [Point(xy) for xy in coord.values()]
    nodes_gdf = gpd.GeoDataFrame(geometry=pts, crs="EPSG:4326").to_crs(3857)
    lines = [LineString([coord[a], coord[b]]) for a, b in sub.edges()]
    polys = list(nodes_gdf.buffer(node_buff))
    if lines:
        edges_gdf = gpd.GeoDataFrame(geometry=lines, crs="EPSG:4326").to_crs(3857)
        polys += list(edges_gdf.buffer(edge_buff))
    iso = unary_union(polys)
    iso_wgs = gpd.GeoSeries([iso], crs=3857).to_crs(4326).iloc[0]
    return iso_wgs, sub


# ──────────────────────────────────────────────
# 2SFCA 접근성 지수
# ──────────────────────────────────────────────
def compute_2sfca(demand_xy, demand_pop, fac_xy, fac_cap, catch_m=800):
    """도보권(catch_m) 기반 2단계 유동 카치먼트. 접근성 배열 반환."""
    # 위경도→미터 근사 위해 평면 투영(간단히 EPSG:3857 스케일)
    dtree = cKDTree(demand_xy)
    ftree = cKDTree(fac_xy)

    # Step1: 시설별 공급비율 Rj
    Rj = np.zeros(len(fac_xy))
    for j, fxy in enumerate(fac_xy):
        idx = dtree.query_ball_point(fxy, catch_m)
        dsum = demand_pop[idx].sum()
        Rj[j] = fac_cap[j] / dsum if dsum > 0 else 0

    # Step2: 수요지별 접근성 Ai
    Ai = np.zeros(len(demand_xy))
    for i, dxy in enumerate(demand_xy):
        jdx = ftree.query_ball_point(dxy, catch_m)
        Ai[i] = Rj[jdx].sum()
    return Ai


def to_meters(lon, lat, lon0, lat0):
    """기준점 대비 미터 평면 근사(소지역용)."""
    mx = (lon - lon0) * 111_320 * np.cos(np.radians(lat0))
    my = (lat - lat0) * 110_540
    return np.column_stack([mx, my])


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
st.title("🗺️ 복지시설 접근성 분석 — 도보 10분 등시선 + 2SFCA")

if not OSMNX_OK:
    st.error(
        "OSMnx/GeoPandas 미설치로 네트워크 분석을 실행할 수 없습니다.\n\n"
        "`pip install osmnx geopandas` 후 다시 실행하세요.\n\n"
        f"세부: {_IMPORT_ERR}"
    )
    st.stop()

@st.cache_data
def make_demo_facilities():
    """데모 시설을 1회만 생성(rerun마다 위치가 바뀌어 깜빡이는 문제 방지)."""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "fcltNm": [f"복지시설{i}" for i in range(8)],
        "cfbNm": ["노인복지시설"] * 8,
        "lon": 127.27 + rng.random(8) * 0.04,
        "lat": 36.47 + rng.random(8) * 0.04,
        "capacity": rng.integers(50, 200, 8),
    })


fac = load_facilities()
DEMO_MODE = fac is None
if DEMO_MODE:
    st.info(
        "실데이터(facilities_geocoded.csv)가 없어 **데모 좌표**로 동작 중입니다. "
        "실데이터를 보려면 collect_facilities.py 로 수집·지오코딩 후 다시 실행하세요."
    )
    fac = make_demo_facilities()

with st.sidebar:
    st.header("분석 설정")
    trip_min = st.slider("등시선 도보 시간(분)", 5, 20, 10)
    catch_min = st.slider("2SFCA 도보권(분)", 5, 15, 10)
    net_dist = st.slider("네트워크 반경(m)", 600, 2000, 1200, step=200)
    st.caption(f"도보 속도 {WALK_SPEED_KMH} km/h 기준")
    st.divider()
    st.metric("로드된 복지시설", f"{len(fac):,}곳")

col_map, col_info = st.columns([3, 2])

if "click_lat" not in st.session_state:
    st.session_state.click_lat = 36.480
    st.session_state.click_lon = 127.289

with col_map:
    fmap = folium.Map(
        location=[st.session_state.click_lat, st.session_state.click_lon],
        zoom_start=14, tiles="CartoDB positron",
    )
    # 전체 시설 마커
    for _, r in fac.iterrows():
        folium.CircleMarker(
            [r["lat"], r["lon"]], radius=4, color="#2c7fb8",
            fill=True, fill_opacity=0.7,
            tooltip=f"{r['fcltNm']} ({r.get('cfbNm','')})",
        ).add_to(fmap)
    folium.Marker(
        [st.session_state.click_lat, st.session_state.click_lon],
        tooltip="분석 지점", icon=folium.Icon(color="red", icon="user"),
    ).add_to(fmap)

    st.caption("지도를 클릭해 분석 지점을 선택하세요.")
    state = st_folium(
        fmap, height=520, key="map",
        returned_objects=["last_clicked"],   # 클릭 외 이벤트로 인한 rerun 방지
    )
    if state and state.get("last_clicked"):
        new_lat = round(state["last_clicked"]["lat"], 6)
        new_lon = round(state["last_clicked"]["lng"], 6)
        # 좌표가 실제로 바뀐 경우에만 갱신 → 무한 리렌더 루프 차단
        if (new_lat != round(st.session_state.click_lat, 6) or
                new_lon != round(st.session_state.click_lon, 6)):
            st.session_state.click_lat = new_lat
            st.session_state.click_lon = new_lon
            st.rerun()

with col_info:
    lat, lon = st.session_state.click_lat, st.session_state.click_lon
    st.metric("분석 지점", f"{lat:.5f}, {lon:.5f}")

    if st.button("🚶 접근성 분석 실행", type="primary", use_container_width=True):
        with st.spinner("도보 네트워크 다운로드 중... (최초 1회 시간 소요)"):
            G = load_walk_graph(lat, lon, dist=net_dist)
            center = ox.nearest_nodes(G, lon, lat)

        iso, sub = make_isochrone(G, center, trip_min)
        st.session_state.iso = iso

        # 등시선 내 시설 탐색
        if iso is not None:
            mask = fac.apply(
                lambda r: iso.contains(Point(r["lon"], r["lat"])), axis=1
            )
            reachable = fac[mask]
        else:
            reachable = fac.iloc[0:0]
        st.session_state.reachable = reachable

        st.success(f"도보 {trip_min}분 내 복지시설: {len(reachable)}곳")
        if len(reachable):
            st.dataframe(
                reachable[["fcltNm", "cfbNm"]].reset_index(drop=True),
                use_container_width=True, height=180,
            )
        else:
            st.warning("도보권 내 복지시설이 없습니다 — 접근성 사각지대일 수 있습니다.")

        # ── 2SFCA: 분석지점 주변 격자 수요 + 시설 공급 ──
        # 수요격자: 네트워크 노드를 수요지점으로 사용(인구는 균일 가정; 실제론 통계 결합)
        nodes = ox.graph_to_gdfs(G, edges=False)
        dlon = nodes["x"].values
        dlat = nodes["y"].values
        demand_xy = to_meters(dlon, dlat, lon, lat)
        demand_pop = np.full(len(demand_xy), 100.0)  # TODO: 고령자 통계 결합

        fxy = to_meters(fac["lon"].values, fac["lat"].values, lon, lat)
        fcap = fac["capacity"].values.astype(float)
        catch_m = catch_min * WALK_MPM

        Ai = compute_2sfca(demand_xy, demand_pop, fxy, fcap, catch_m)
        nodes = nodes.copy()
        nodes["access"] = Ai
        st.session_state.access_nodes = nodes

        # 분석지점 접근성 = 가장 가까운 노드값
        ctree = cKDTree(demand_xy)
        _, ci = ctree.query([0, 0])
        score = Ai[ci]
        pct = (Ai < score).mean() * 100 if Ai.max() > 0 else 0
        st.metric("이 지점의 2SFCA 접근성 지수", f"{score:.4f}")
        st.caption(
            f"주변 격자 중 하위 {pct:.0f}% 수준 "
            f"({'사각지대 경향' if pct < 40 else '양호'})"
        )

# 결과 지도 (등시선 + 접근성 히트맵)
if st.session_state.get("iso") is not None:
    st.divider()
    st.subheader("분석 결과 지도")
    rmap = folium.Map(location=[lat, lon], zoom_start=15, tiles="CartoDB positron")

    # 등시선 폴리곤
    folium.GeoJson(
        st.session_state.iso.__geo_interface__,
        style_function=lambda x: {
            "fillColor": "#41ab5d", "color": "#238443",
            "weight": 2, "fillOpacity": 0.25,
        },
        tooltip=f"도보 {trip_min}분 도달 영역",
    ).add_to(rmap)

    # 접근성 히트맵 (낮을수록 사각지대 → 별도 레이어로 분포 표시)
    nodes = st.session_state.get("access_nodes")
    if nodes is not None and nodes["access"].max() > 0:
        heat = [[r["y"], r["x"], r["access"]] for _, r in nodes.iterrows()]
        HeatMap(heat, radius=18, blur=15, min_opacity=0.3,
                name="접근성(높을수록 진함)").add_to(rmap)

    # 도달 시설
    for _, r in st.session_state.get("reachable", fac.iloc[0:0]).iterrows():
        folium.Marker(
            [r["lat"], r["lon"]],
            icon=folium.Icon(color="green", icon="plus-sign"),
            tooltip=r["fcltNm"],
        ).add_to(rmap)

    folium.Marker([lat, lon], icon=folium.Icon(color="red", icon="user"),
                  tooltip="분석 지점").add_to(rmap)
    folium.LayerControl().add_to(rmap)
    st_folium(rmap, height=520, key="result_map", returned_objects=[])

st.divider()
st.caption(
    "※ 2SFCA의 수요(인구)는 현재 균일 가정입니다. 통계청 고령자 인구 격자를 "
    "결합하면 '수요 대비 공급 부족' 사각지대를 정밀 탐지할 수 있습니다. "
    "도보 이동시간은 평지·평균속도 기준으로 경사·신호는 미반영입니다."
)
