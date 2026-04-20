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

이벤트 수를 조정하려면:

```bash
EVENT_COUNT=5000 docker compose up --build
```

---

## 이벤트 설계

### 이벤트 타입

| 타입 | 설명 | 주요 properties |
|------|------|----------------|
| `page_view` | 사용자가 페이지를 방문 | `page_path`, `referrer`, `duration_ms` |
| `purchase` | 결제 완료 | `item_id`, `amount_krw`, `payment_method` |
| `error` | 서버/클라이언트 에러 발생 | `error_code`, `message`, `stack_trace_hash` |

### 설계 근거

**왜 이 3가지 이벤트인가?**

실제 e-commerce 서비스의 핵심 지표를 커버하도록 선정했습니다.
- `page_view`는 트래픽의 대부분을 차지하며 UX 분석의 기반이 됩니다.
- `purchase`는 서비스의 핵심 비즈니스 지표(전환율)를 측정합니다.
- `error`는 서비스 안정성 모니터링에 필수적입니다.

**세션 기반 시뮬레이션**

단순 랜덤 생성 대신 **세션 단위**로 이벤트를 생성했습니다. 실제 사용자는 `page_view → page_view → purchase` 같은 자연스러운 퍼널을 따르기 때문입니다.

- 한 세션은 반드시 1–5개의 `page_view`로 시작 (브라우징)
- 20% 확률로 `purchase` 발생 (전환)
- 10% 확률로 `error` 발생 (장애)
- 세션 내 이벤트는 수 초 간격으로 타임스탬프가 증가

이를 통해 생성된 데이터가 실제 서비스 데이터와 유사한 통계적 분포를 갖습니다.

**피크타임 반영**

한국 서비스 기준 비즈니스 아워(09:00–18:00 KST)에 트래픽 70%를 집중시켜, 실제 서비스의 시간대별 패턴을 재현했습니다.

**50명 유저 풀 재사용**

50개의 UUID를 고정 풀로 만들어 반복 사용합니다. 완전 랜덤 UUID를 쓰면 모든 유저가 단 1번만 방문하는 비현실적인 데이터가 됩니다. 유저 풀을 고정하면 재방문, Top 유저 집계 등 의미있는 분석이 가능합니다.

---

## 스키마 설명

`events` 테이블은 이벤트의 공통 필드(타입, 사용자, 세션, 타임스탬프)를 정형 컬럼으로 분리하고, 이벤트별로 다른 메타데이터는 `properties JSONB` 컬럼에 저장합니다. 이렇게 하면 공통 필드에 인덱스를 걸어 집계 쿼리 성능을 확보하면서도, 이벤트 타입이 늘어날 때 스키마 변경 없이 확장할 수 있습니다. `created_at`을 별도로 두어 데이터 적재 시각과 이벤트 발생 시각(`timestamp`)을 구분했습니다.

---

## 구현하면서 고민한 점

**1. 단순 랜덤 vs 세션 기반 생성**

처음에는 이벤트를 단순 랜덤으로 생성하는 방식을 고려했습니다. 하지만 이 방식은 같은 `session_id`에 `purchase`만 5개 들어가는 등 비현실적인 데이터를 만듭니다. 실제 분석 시스템에서 의미 있는 패턴(전환율, 세션당 페이지뷰)을 시뮬레이션하려면 세션 단위 흐름이 필요하다고 판단했습니다.

**2. generator의 종료 보장**

`depends_on: condition: service_completed_successfully`가 동작하려면 generator가 exit 0으로 끝나야 합니다. DB 연결 재시도를 최대 5회(지수 백오프)로 제한하고, 실패 시 예외를 발생시켜 무한 대기 없이 명확하게 종료되도록 했습니다.

**3. 자격증명 관리**

DB 비밀번호를 코드에 하드코딩하지 않고 환경변수로 주입합니다. `docker-compose.yml`의 `${VAR:-default}` 문법으로 `.env` 파일 없이도 기본값으로 즉시 실행 가능하게 했습니다.

**4. properties 컬럼 설계**

이벤트 타입마다 필드가 달라 별도 테이블로 나누면 JOIN 비용이 발생합니다. JSONB를 선택해 유연성과 쿼리 편의성을 동시에 확보했고, 이벤트 전체를 단일 JSON blob으로 저장하는 안티패턴은 의도적으로 피했습니다.

---

## 프로젝트 구조

```
.
├── db/
│   └── init.sql          # 테이블 및 인덱스 생성
├── generator/
│   ├── main.py           # 세션 기반 이벤트 생성 및 DB 저장
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
