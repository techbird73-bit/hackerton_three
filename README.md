# 복지시설 접근성 분석 — 도보 10분 등시선 + 2SFCA

특정 지점에서 주변 복지시설까지의 **실제 도보 이동거리 기반 접근성**을 분석하고,
접근성 사각지대를 지도에 시각화하는 웹서비스.

## 차별점
- 직선거리가 아닌 **OSMnx 도로 네트워크 기반 도보 이동시간**
- **등시선(Isochrone)**: 도보 10분 도달 영역을 폴리곤으로 표출
- **2SFCA**(Two-Step Floating Catchment Area): 수요(인구) 대비 공급(시설 수용력)
  불균형을 반영한 접근성 지수 → "시설이 먼데 수요는 많은" 진짜 사각지대 탐지

## 구성
- `collect_facilities.py` : 복지시설 API 수집 + VWorld 지오코딩 → CSV
- `app.py` : Streamlit 분석·시각화 앱

## 실행
```bash
pip install -r requirements.txt
export DATA_GO_KR_KEY="공공데이터포털_키"
export VWORLD_KEY="VWorld_키"
python collect_facilities.py     # facilities_geocoded.csv 생성
streamlit run app.py
```

## Streamlit Cloud 배포 (CSV 동봉 방식)
앱은 실행 시 API를 호출하지 않고 `facilities_geocoded.csv`만 읽으므로,
**배포 환경에 키가 필요 없습니다.**

1. 로컬에서 `collect_facilities.py`로 본인 키를 써서 `facilities_geocoded.csv` 생성
   (키는 환경변수로만 사용 — 소스/레포에 넣지 않음)
2. 생성된 `facilities_geocoded.csv`를 레포에 커밋(현재 동봉된 샘플을 교체)
3. share.streamlit.io → 레포 연결 → Main file: `app.py` → Deploy

> 동봉된 `facilities_geocoded.csv`는 구조 확인용 **샘플**입니다.
> 실서비스 시 위 1~2단계로 실데이터로 교체하세요.

## 데이터 (모두 공개)
- 사회복지시설정보 (공공데이터포털, B554287)
- VWorld 지오코딩 (국토교통부)
- OpenStreetMap 도보 네트워크 (OSMnx)
- (확장) 통계청 고령자 인구 격자 → 2SFCA 수요 정밀화

## 고도화 포인트
현재 2SFCA 수요는 균일 가정. 통계청 고령인구 격자를 결합하면
"고령자 밀집 + 시설 부재" 취약지역을 정밀 탐지 가능.
AlphaEarth 임베딩 결합 시 환경 특성까지 다차원 분석 가능.
