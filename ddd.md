# 도메인 설계 문서 (DDD)

## 개요

Printer 앱은 두 개의 핵심 도메인으로 구성됩니다.

- **Print 도메인**: 할 일을 물리적으로 출력하고, 진행 중 / 완료 상태를 추적
- **Task 도메인**: Jira와 연동된 에픽 / 태스크 / 서브태스크 계층 구조를 관리

---

## 엔티티 및 값 객체

### PrintJob (엔티티)
물리 프린터로 출력된 할 일 하나를 나타냅니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | int | DB 자동 생성 PK |
| `title` | string | 할 일 제목 |
| `printed_by` | string | 사용자 이메일 |
| `status` | `"progress"` \| `"done"` | 현재 상태 |
| `printed_at` | timestamp | 생성 시각 |
| `completed_at` | timestamp? | 완료 처리 시각 |
| `sort_order` | int | 수동 정렬 순서 |
| `jira_key` | string? | 연결된 Jira 이슈 키 (예: `PROJ-42`) |

**불변 규칙**:
- `status`는 `progress → done`으로만 전환 가능 (되돌릴 수 없음)
- `jira_key`가 있으면 완료/삭제 시 Jira에도 동일하게 반영
- `sort_order`는 reorder API가 `0, 1, 2...` 순으로 배정. `init_db()`의 일회성 마이그레이션에서 `WHERE sort_order = 0` 조건 UPDATE는 재시작 시 반복 실행되므로 제거됨

---

### JiraIssue (값 객체 / 읽기 전용 뷰)
Jira API에서 읽어온 이슈 정보입니다. DB에 저장되지 않으며, Jira가 단일 진실 공급원입니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `key` | string | Jira 이슈 키 |
| `summary` | string | 제목 |
| `status` | string | 상태 표시명 (언어 종속) |
| `status_category` | `"new"` \| `"indeterminate"` \| `"done"` | 언어 독립 상태 카테고리 |
| `due_date` | date? | 마감일 |
| `parent_key` | string? | 상위 이슈 키 |
| `sort_order` | int | `jira_order` 테이블 기반 수동 순서 |

**계층 구조**:
```
Epic
  └─ Task
       └─ Subtask  (또는 TEMP 영역의 미배정 서브태스크)
```

---

### User (값 객체)
Google OAuth로 인증된 사용자. 상태를 직접 저장하지 않고 JWT 클레임에서 파생됩니다.

| 필드 | 출처 |
|------|------|
| `email` | JWT payload |
| `name` | JWT payload |
| `picture` | JWT payload (선택) |

---

### JiraConfig (엔티티)
사용자별 Jira 연동 설정. `email`을 PK로 `user_jira_config` 테이블에 저장됩니다.

| 필드 | 설명 |
|------|------|
| `jira_base_url` | Jira 인스턴스 URL |
| `jira_email` | Jira 계정 이메일 |
| `jira_api_token` | API 토큰 |
| `jira_project_key` | 프로젝트 키 (예: `PROJ`) |
| `jira_parent_key` | SUBTASK 모드 시 서브태스크를 만들 부모 이슈 |
| `jira_board_id` | 스프린트 보드 ID (태스크 이동용) |
| `jira_temp_key` | 미배정 서브태스크를 담는 TEMP 태스크 키 |
| `ticket_mode` | `"SUBTASK"` \| `"TASK"` |

---

## 바운디드 컨텍스트

### Print Context
**책임**: 잡 생성 · 상태 전환 · 물리 출력 · 히스토리 조회

```
사용자 입력 (title)
  → POST /api/print
      ├─ DB: print_jobs INSERT (status='progress')
      ├─ Printer: ESC/POS 영수증 출력 (print_enabled=true 시)
      └─ Jira Context: 이슈 생성 요청 (Jira 설정 있을 때)
```

**핵심 유스케이스**:
1. 할 일 출력 (`sendPrint`)
2. 기존 잡 재출력 (`reprintJob`) — DB·Jira 변경 없이 프린터로만 출력
3. 잡 완료 처리 (`markDone`) — Jira `mark_done_issue` 연동
4. 잡 삭제 (`deleteJob`) — Jira `delete_issue` 연동
5. Jira 동기화 (`syncJira`) — Jira 이슈 → print_jobs upsert
6. 수동 순서 변경 (`reorder`)

---

### Task Context
**책임**: 에픽/태스크/서브태스크 CRUD · 계층 배열 · 상태 전환

```
Jira API (단일 진실 공급원)
  ↕ httpx (be/jira.py)
FastAPI 엔드포인트 (/jira/*)
  ↕ authFetch (fe/auth.js)
프론트엔드 페이지들
  - manage_task.html  (CRUD)
  - arrange_task.html (에픽-태스크 할당)
  - arrange_subtask.html (태스크-서브태스크 할당)
```

**핵심 유스케이스**:
1. 이슈 생성 (에픽 / 태스크 / 서브태스크)
2. 이슈 제목 수정 / 마감일 설정 / 삭제
3. 상태 전환 (available transitions → apply transition)
4. 에픽에 태스크 할당 (`assign_task_to_epic`)
5. 태스크에 서브태스크 할당 (`assign_subtask_to_task`)
6. 수동 정렬 순서 저장 (`jira_order` 테이블)

---

### Auth Context
**책임**: Google OAuth 흐름 · JWT 발급 · 토큰 검증

```
/ (index.html)
  └─ initAuth()
       ├─ 토큰 없음 → #login-modal 표시
       └─ 토큰 있음 → #app 표시

서브페이지 (print, manage_task, ...)
  └─ initSubPageAuth(path)
       ├─ 토큰 없음 → localStorage[auth_redirect]=path → / 리다이렉트
       └─ 토큰 있음 → #app 표시

/auth/callback
  └─ JWT 발급 → /?token={jwt}
       └─ initAuth() → 토큰 저장 → localStorage[auth_redirect] 있으면 복귀
```

---

## 도메인 이벤트

| 이벤트 | 트리거 | 사이드 이펙트 |
|--------|--------|---------------|
| `JobPrinted` | POST /api/print | 영수증 출력, Jira 이슈 생성 |
| `JobReprinted` | POST /jobs/{id}/reprint | 영수증 재출력 (DB·Jira 변경 없음) |
| `JobCompleted` | PATCH /jobs/{id}/done | Jira `mark_done_issue` |
| `JobDeleted` | DELETE /jobs/{id} | Jira `delete_issue` |
| `JiraSynced` | POST /jobs/sync-jira | Jira 이슈 → print_jobs upsert |
| `IssueCreated` | POST /jira/{epics,tasks,subtasks} | 보드 이동, TO DO 전환 (TASK 모드) |
| `IssueTransitioned` | POST /jira/issues/{key}/transitions | Jira 상태 변경 |
| `EpicAssigned` | PATCH /jira/tasks/{key}/epic | Jira parent/customfield 업데이트 |
| `SubtaskAssigned` | PATCH /jira/subtasks/{key}/parent | Jira parent 업데이트 |

---

## 언어 독립 설계

Jira 인스턴스의 언어 설정(한국어/영어)에 무관하게 동작하도록 다음 규칙을 따릅니다.

1. **상태 판별**: `statusCategory.key` 사용 (`"done"` / `"indeterminate"` / `"new"`)
   → `status` 표시명("완료", "Done", "진행 중") 에 의존하지 않음

2. **이슈 타입**: `_discover_types(project_key, cfg)` 로 런타임에 탐색
   → 에픽/태스크/서브태스크 타입명 하드코딩 없음
   → `issuetype in subTaskIssueTypes()` JQL 사용

3. **에픽 판별**: `ptype == "Epic"` 대신 known_epics 집합으로 부모 키 조회
   → 한국어 Jira의 "에픽" 타입명에 의존하지 않음

---

## DB 스키마

### print_jobs
```sql
CREATE TABLE print_jobs (
  id           SERIAL PRIMARY KEY,
  title        TEXT NOT NULL,
  printed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  printed_by   TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'done',
  completed_at TIMESTAMPTZ,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  jira_key     TEXT
);
```

### jira_order
```sql
CREATE TABLE jira_order (
  issue_key  TEXT PRIMARY KEY,
  sort_order INTEGER NOT NULL DEFAULT 0
);
```

### user_jira_config
```sql
CREATE TABLE user_jira_config (
  email             TEXT PRIMARY KEY,
  jira_base_url     TEXT NOT NULL DEFAULT '',
  jira_email        TEXT NOT NULL DEFAULT '',
  jira_api_token    TEXT NOT NULL DEFAULT '',
  jira_project_key  TEXT NOT NULL DEFAULT '',
  jira_parent_key   TEXT NOT NULL DEFAULT '',
  jira_board_id     TEXT NOT NULL DEFAULT '',
  jira_temp_key     TEXT NOT NULL DEFAULT '',
  ticket_mode       TEXT NOT NULL DEFAULT 'SUBTASK'
);
```

---

## 프론트엔드 컨벤션

### 인증 패턴

| 페이지 유형 | 초기화 방식 | 미인증 처리 |
|------------|-------------|-------------|
| 메인 (`/`) | `initAuth()` | `#login-modal` 표시 |
| 서브페이지 | `initSubPageAuth(path)` | `/` 로 리다이렉트 |

### `#app` 표시/숨김

- 초기 HTML: `<div id="app">` — CSS에서 `display: flex` (기본 표시)
- 숨김: `_showLoginModal()` → `app.style.display = 'none'` (인라인 오버라이드)
- 표시: `_showApp()` → `app.style.removeProperty('display')` (인라인 제거 → CSS 폴백)

서브페이지에는 `#login-modal` / `#login-screen` 없음. 미인증 시 `/` 로 리다이렉트.

### 스크립트 초기화 패턴

모든 `<script>` 태그는 `</body>` 직전에 위치하므로 DOM이 이미 준비된 상태.
`DOMContentLoaded` 래퍼 없이 직접 호출:

```html
<!-- 메인 페이지 -->
<script src="/auth.js"></script>
<script>initAuth();</script>

<!-- 서브페이지 -->
<script src="/auth.js"></script>
<script>
  function initPage() { ... }
  initPage();
</script>
```

### 드래그 앤 드롭

Pointer Events API 사용 (마우스 + 터치 통합):
- `pointerdown` → 드래그 시작, `setPointerCapture`
- `pointermove` → 드롭 라인 표시
- `pointerup` → 재정렬 또는 부모 변경, API 저장

### 상태 표시

- 완료(`done`) 항목: `opacity: 0.5` 또는 CSS `done-item` 클래스
- 상태 배지: `statusCategory` 기반으로 클래스 결정 (`done` / `in-progress` / `todo`)
