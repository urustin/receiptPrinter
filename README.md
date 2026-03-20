# Printer

USB 열영수증 프린터를 위한 웹 기반 태스크 관리 앱.

할 일을 입력하면 영수증으로 출력되고, 진행 중/완료 상태로 관리할 수 있습니다. Jira 서브태스크와 자동으로 연동됩니다.

## 기능

- Google OAuth 로그인
- 할 일 출력 (USB 열영수증 프린터 `/dev/usb/lp0`)
- 진행 중 / 완료 상태 관리 및 드래그 앤 드롭 순서 변경
- Jira 서브태스크 자동 생성 / 완료 / 삭제 연동
- PostgreSQL 기반 히스토리

## 스택

| 레이어 | 기술 |
|--------|------|
| Backend | FastAPI, python-escpos, Pillow, psycopg2 |
| Frontend | Vanilla JS, Nginx |
| Database | PostgreSQL 16 |
| Auth | Google OAuth 2.0 + JWT |
| E2E | Playwright |
| 배포 | Docker Compose |

## 시작하기

### 사전 요구사항

- Docker & Docker Compose
- USB 열영수증 프린터 (`/dev/usb/lp0`)
- Google OAuth 앱 ([Google Cloud Console](https://console.cloud.google.com))
- Jira API 토큰

### 설정

```bash
cp be/.env.example be/.env
# be/.env 편집
```

필요한 환경 변수:

| 변수 | 설명 |
|------|------|
| `SECRET_KEY` | JWT 서명용 랜덤 문자열 |
| `GOOGLE_CLIENT_ID` | Google OAuth 클라이언트 ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth 클라이언트 시크릿 |
| `REDIRECT_URI` | OAuth 콜백 URL (예: `https://your-domain.com/auth/callback`) |
| `JIRA_BASE_URL` | Jira 인스턴스 URL |
| `JIRA_EMAIL` | Jira 계정 이메일 |
| `JIRA_API_TOKEN` | Jira API 토큰 |
| `JIRA_PARENT_KEY` | 서브태스크를 만들 부모 이슈 키 (예: `PROJ-123`) |

### 실행

```bash
docker compose up -d
```

앱은 기본적으로 `http://localhost:60021` 에서 동작합니다.

## E2E 테스트

```bash
cd e2e
npm install
npx playwright install
npm test
```

## 프로젝트 구조

```
├── be/                 # FastAPI 백엔드
│   ├── app.py          # 메인 앱 (프린트, 히스토리, 재정렬 API)
│   ├── auth.py         # Google OAuth + JWT
│   ├── jira.py         # Jira REST API 클라이언트
│   ├── printer.py      # 영수증 이미지 렌더링 유틸
│   ├── Dockerfile
│   └── .env.example
├── fe/                 # Nginx 프론트엔드
│   ├── index.html
│   ├── script.js
│   ├── style.css
│   └── Dockerfile
├── e2e/                # Playwright E2E 테스트
│   └── tests/
├── docker-compose.yml
└── README.md
```
