# Event Pipeline

Docker Compose 기반의 이벤트 데이터 생성 및 시각화 파이프라인입니다.

---

## 실행 방법

**Prerequisites**
- Docker Desktop 설치 및 실행 중

```bash
git clone https://github.com/hurjun/futurescole.git
cd futurescole
docker compose up --build
```

파이프라인이 완료되면 `./output/` 폴더에 PNG 파일 2개가 생성됩니다.

| 파일 | 설명 |
|------|------|
| `event_type_distribution.png` | 이벤트 타입별 건수 막대 차트 |
| `hourly_trend.png` | 시간대별 이벤트 추이 라인 차트 |

이벤트 수를 바꾸고 싶다면:
```bash
EVENT_COUNT=5000 docker compose up --build
```

---

## 스키마 설명

`events` 테이블은 이벤트의 공통 필드(타입, 사용자, 세션, 타임스탬프)를 정형 컬럼으로 분리하고, 이벤트별로 다른 메타데이터는 `properties JSONB` 컬럼에 저장합니다. 이렇게 하면 공통 필드에 인덱스를 걸어 집계 쿼리 성능을 확보하면서도, 이벤트 타입이 늘어날 때 스키마 변경 없이 확장할 수 있습니다. `created_at`을 별도로 두어 데이터 적재 시각과 이벤트 발생 시각(`timestamp`)을 구분했습니다.

---

## 구현하면서 고민한 점

**1. generator의 종료 보장**
Docker Compose에서 `depends_on: condition: service_completed_successfully`를 사용하려면 generator가 exit 0으로 끝나야 합니다. DB 연결 재시도를 최대 5회로 제한하고 실패 시 예외를 발생시켜, 무한 대기 없이 명확하게 종료되도록 했습니다.

**2. 자격증명 관리**
DB 비밀번호를 코드에 하드코딩하지 않고 환경변수로 주입받습니다. `docker-compose.yml`의 `${VAR:-default}` 문법으로 `.env` 파일 없이도 기본값으로 바로 실행할 수 있게 했습니다.

**3. properties 컬럼 설계**
이벤트 타입마다 필드가 달라 별도 테이블로 나누면 JOIN 비용이 생깁니다. JSONB를 선택해 유연성과 쿼리 편의성(→ 연산자)을 동시에 얻었고, 단일 JSON blob 전체를 events 컬럼에 넣는 안티패턴은 피했습니다.

---

## 프로젝트 구조

```
.
├── db/
│   └── init.sql          # 테이블 및 인덱스 생성
├── generator/
│   ├── main.py           # 이벤트 생성 및 DB 저장
│   ├── requirements.txt
│   └── Dockerfile
├── visualizer/
│   ├── main.py           # 쿼리 실행 및 PNG 저장
│   ├── requirements.txt
│   └── Dockerfile
├── analysis/
│   └── queries.sql       # 분석 쿼리 4종
├── output/               # PNG 출력 디렉터리 (volume mount)
└── docker-compose.yml
```
