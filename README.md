# Printer

USB 열영수증 프린터와 Jira를 연동한 웹 기반 태스크 관리 앱.

할 일을 입력하면 영수증으로 출력되고, 진행 중/완료 상태로 관리하며, Jira 이슈와 자동으로 동기화됩니다.

## 기능

- **Google OAuth 로그인** — 메인 페이지(`/`)에서 모달로 인증, 서브페이지는 미인증 시 메인으로 리다이렉트
- **프린트 보드** (`/print`) — 할 일 입력 → 영수증 출력 → 진행 중 / 완료 칸반 보드
- **태스크 관리** (`/manage_task`) — 에픽 / 태스크 / 서브태스크 생성 · 수정 · 삭제, 상태 전환, 마감일 설정
- **에픽 → 태스크 배열** (`/arrange_task`) — 드래그 앤 드롭으로 태스크를 에픽에 할당
- **태스크 → 서브태스크 배열** (`/arrange_subtask`) — 드래그 앤 드롭으로 서브태스크를 태스크에 할당
- **Jira 동기화** — 프린트 시 Jira 이슈 자동 생성 / 완료 / 삭제 연동, 수동 동기화 지원
- **사용자별 Jira 설정** (`/settings`) — 각 사용자가 독립된 Jira 계정과 프로젝트를 설정

## 스택

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI, python-escpos, Pillow, psycopg2, httpx |
| Frontend | Vanilla JS, Nginx |
| Database | PostgreSQL 16 |
| Auth | Google OAuth 2.0 + JWT (HS256) |
| E2E | Playwright |
| 배포 | Docker Compose |

## 시작하기

### 사전 요구사항

- Docker & Docker Compose
- USB 열영수증 프린터 (`/dev/usb/lp0`, Sewoo 또는 ESC/POS 호환)
- Google OAuth 앱 ([Google Cloud Console](https://console.cloud.google.com))

### 설정

```bash
cp be/.env.example be/.env
# be/.env 편집
```

| 변수 | 설명 |
|------|------|
| `SECRET_KEY` | JWT 서명용 랜덤 문자열 |
| `GOOGLE_CLIENT_ID` | Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 클라이언트 시크릿 |
| `REDIRECT_URI` | OAuth 콜백 URL (예: `https://your-domain.com/auth/callback`) |

> Jira 설정은 환경 변수 대신 `/settings` 페이지에서 사용자별로 입력합니다.

### 실행

```bash
docker compose up -d
```

기본 포트: `http://localhost:60021`

---

## 빌드 및 배포

### 운영 인프라 구조

```
인터넷
  └─ print.ericfromkorea.com (DNS)
       └─ 공유기 포트포워딩  WAN 80/443 → 192.168.1.121 80/443
            └─ 시스템 nginx  (/etc/nginx/sites-enabled/print.ericfromkorea.com)
                 ├─ HTTP(80)  → 301 HTTPS 리다이렉트
                 └─ HTTPS(443) SSL 종료 (Let's Encrypt)
                      └─ proxy_pass → http://localhost:60021
                           └─ Docker: fe 컨테이너 (nginx:alpine, 60021:80)
                                └─ proxy → be:8000 (FastAPI)
                                     └─ db:5432 (PostgreSQL)
```

### 시스템 nginx 설정

파일: `/etc/nginx/sites-enabled/print.ericfromkorea.com`

```nginx
server {
    listen 80;
    server_name print.ericfromkorea.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name print.ericfromkorea.com;

    ssl_certificate     /etc/letsencrypt/live/print.ericfromkorea.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/print.ericfromkorea.com/privkey.pem;

    location / {
        proxy_pass http://localhost:60021;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

SSL 인증서 갱신: `certbot renew` (Let's Encrypt, 자동 갱신 권장)

### 컨테이너 구성

| 서비스 | 이미지 | 포트 | 역할 |
|--------|--------|------|------|
| `fe` | nginx:alpine | 60021:80 | 정적 파일 서빙 + API 프록시 |
| `be` | python:3.11-slim | 8000 (내부) | FastAPI 백엔드 |
| `db` | postgres:16-alpine | 5432 (내부) | PostgreSQL, 볼륨 `pgdata` |

### 앱 내부 nginx 라우팅 (`fe/nginx.conf`)

| 경로 패턴 | 처리 |
|-----------|------|
| `/api/`, `/history`, `/jobs`, `/jira`, `/settings/` | FastAPI 백엔드로 프록시 |
| `/auth/` | FastAPI 백엔드로 프록시 |
| 그 외 | 정적 파일 → 없으면 `index.html` (SPA 폴백) |

캐시: 모든 경로에 `Cache-Control: no-store, no-cache, must-revalidate`

### 빌드 및 재기동

```bash
cd /home/son/prj/printer

# 코드 업데이트 후 재빌드·재기동
git pull
docker compose up -d --build

# 로그 확인
docker compose logs -f

# 컨테이너 상태 확인
docker compose ps
```

> **프린터 없이 실행할 때**: `docker-compose.yml`의 `devices` 항목을 주석처리. 백엔드가 `/dev/usb/lp0` 접근 실패 시에도 잡 생성은 동작합니다.

## 인증 흐름

```
사용자가 / 접근
  └─ 토큰 없음 → 로그인 모달 표시 → Google 로그인 버튼 클릭
       └─ /auth/login → Google OAuth → /auth/callback
            └─ JWT 발급 → /?token={jwt} → localStorage 저장 → 앱 표시

서브페이지 접근 (예: /print)
  └─ 토큰 없음 → localStorage에 목적지 저장 → / 로 리다이렉트
       └─ 로그인 완료 → 저장된 경로로 복귀
```

## E2E 테스트

```bash
cd e2e
npm install
npx playwright install
npm test
```

테스트는 실제 백엔드(`localhost:60021`)에 요청합니다. `docker compose up -d` 후 실행하세요.

## 프로젝트 구조

```
├── be/                        # FastAPI 백엔드
│   ├── app.py                 # 메인 API (프린트, 잡 관리, Jira 연동)
│   ├── auth.py                # Google OAuth + JWT
│   ├── jira.py                # Jira REST API v3 클라이언트
│   ├── printer.py             # ESC/POS 영수증 유틸
│   ├── Dockerfile
│   └── .env.example
├── fe/                        # Nginx 프론트엔드 (Vanilla JS)
│   ├── index.html             # 메인 허브 (인증 포함)
│   ├── print.html             # 프린트 보드
│   ├── manage_task.html       # 태스크 관리
│   ├── arrange_task.html      # 에픽 → 태스크 배열
│   ├── arrange_subtask.html   # 태스크 → 서브태스크 배열
│   ├── settings.html          # Jira 설정
│   ├── script.js              # 프린트 페이지 로직
│   ├── auth.js                # 인증 헬퍼 (모든 페이지에서 공유)
│   ├── style.css              # 프린트 페이지 스타일시트
│   ├── nginx.conf
│   └── Dockerfile
├── e2e/                       # Playwright E2E 테스트
│   ├── tests/
│   │   ├── auth.spec.js       # 인증 흐름
│   │   ├── board.spec.js      # 프린트 보드 UI
│   │   ├── jobs.spec.js       # 잡 CRUD API
│   │   └── lifecycle.spec.js  # 전체 워크플로우
│   ├── helpers/auth.js        # JWT 생성 + API 요청 헬퍼
│   └── playwright.config.js
├── docker-compose.yml
└── README.md
```

## API 개요

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/print` | 잡 생성 + 영수증 출력 + Jira 이슈 생성 |
| `POST` | `/jobs/{id}/reprint` | 기존 잡 재출력 (DB·Jira 변경 없음) |
| `GET` | `/history` | 사용자의 진행 중 / 완료 잡 목록 |
| `PATCH` | `/jobs/{id}/done` | 잡 완료 처리 |
| `DELETE` | `/jobs/{id}` | 잡 삭제 |
| `POST` | `/jobs/sync-jira` | Jira → DB 동기화 |
| `GET` | `/jira/all-items` | 에픽 / 태스크 / 서브태스크 전체 조회 |
| `POST` | `/jira/epics` | 에픽 생성 |
| `POST` | `/jira/tasks` | 태스크 생성 |
| `POST` | `/jira/subtasks` | 서브태스크 생성 |
| `POST` | `/jira/order` | 수동 순서 저장 |
| `GET` | `/settings/jira` | Jira 설정 조회 |
| `PUT` | `/settings/jira` | Jira 설정 저장 |
| `GET` | `/auth/login` | Google OAuth 시작 |
| `GET` | `/auth/callback` | OAuth 콜백 + JWT 발급 |

> 모든 `/api`, `/history`, `/jobs`, `/jira`, `/settings` 경로는 Bearer 토큰 인증 필요.

## 주요 설계 결정

- **언어 독립적 Jira 상태**: `statusCategory.key` (`"done"` / `"indeterminate"` / `"new"`)를 사용해 한국어/영어 Jira 모두 지원
- **이슈 타입 자동 감지**: `_discover_types()`로 에픽/태스크/서브태스크 타입명을 런타임에 탐색해 언어 무관하게 동작
- **사용자별 데이터 격리**: 모든 잡·설정이 `email` 기준으로 분리됨
- **수동 순서**: `jira_order` 테이블에 별도 저장, Jira 기본 순서 오버라이드
- **티켓 모드**: SUBTASK (부모 이슈 하위 생성) / TASK (독립 태스크 + 보드 이동) 전환 가능
- **인증 UI 단일화**: 로그인 UI는 메인(`/`)의 `#login-modal` 하나만 존재. 서브페이지는 미인증 시 `/`로 리다이렉트하며 별도 로그인 화면 없음
- **`#app` 표시 전략**: CSS 기본 `display: flex`, 미인증 시 인라인 `display: none` 오버라이드, 인증 후 `removeProperty('display')`로 제거
- **스크립트 초기화**: `</body>` 직전 `<script>` 배치로 DOM 준비 보장 → `DOMContentLoaded` 래퍼 불필요
- **sort_order 안전성**: `init_db()` 마이그레이션의 `UPDATE ... WHERE sort_order = 0` 재시작 시 반복 실행 문제 → 해당 라인 제거. reorder API는 `enumerate(0, 1, 2...)` 기준으로 배정
