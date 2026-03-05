# SlackOS PRD v0.4
**Slack 기반 AI 팀 운영 시스템**
*최종 수정: 2026.03.05*

---

## 최종 목표 (End Goal)

> Ethan이 Slack에 한 줄 던지면, AI 팀이 실제 결과물(코드, PR, 문서, 분석)을 만들어서 #review에 올려놓는다. Ethan은 ✅/❌ 리액션만 한다.

---

## 현재 상태 (v0.4.0)

| 기능 | v0.2 | v0.3.0 | v0.4.0 |
|---|---|---|---|
| 라우팅 | ✅ 키워드 매칭 | ✅ 3단계 (채널→키워드→LLM) | ✅ 유지 |
| 채널 구조 | ⚠️ 3/7 채널만 연결 | ✅ 7개 전체 연결 | ✅ 유지 |
| GitHub 읽기/쓰기 | ⚠️ PR 생성 버그 | ✅ 자동 PR 생성 | ✅ 유지 |
| 웹 검색 | ❌ | ✅ Claude server-side search | ✅ 유지 |
| 학습 시스템 | ❌ | ✅ 메모리 + 프로필 자동 업데이트 | ✅ 유지 |
| 스케줄 실행 | ❌ | ✅ 아침 브리핑, 주간 계획, 파운더 레이더 | ✅ 태스크 기반으로 전환 |
| 쓰레드 지원 | ❌ | ✅ thread_ts 지원 | ✅ 유지 |
| **태스크 관리** | ❌ | ❌ | ✅ tasks.jsonl 상태 추적 |
| **리액션 승인** | ❌ | ❌ | ✅ ✅/❌/🔄 리액션 기반 |
| **에이전트 핸드오프** | ❌ | ❌ | ✅ dispatch_task() 체인 |
| **태스크 큐** | ❌ | ❌ | ✅ 30분 간격 자동 처리 |

---

## 에이전트 구성

| 에이전트 | 채널 | 역할 | 도구 |
|---|---|---|---|
| **Chief of Staff** | #ops, #review | 라우팅, 주간 계획 | (라우터 전용) |
| **Dev Lead** | #ops-dev | 코드 작업 | GitHub API (읽기/쓰기/PR) |
| **Research Lead** | #ops-research | 리서치 | 웹 검색, GitHub 읽기 |
| **Content Lead** | #ops-content | 글쓰기 | 웹 검색, Obsidian 저장 |
| **Design Lead** | #ops-design | 구조 설계 | (스펙 문서 출력) |

---

## Phase 1: 기반 수정 — ✅ 완료 (v0.3.0)

- ✅ Chief of Staff 7개 채널 전체 연결
- ✅ 중복 라우팅 로직 제거 (determine_agent 단일화)
- ✅ 아침 브리핑 + 파운더 레이더에 웹 검색 추가
- ✅ 에이전트 시스템 프롬프트 강화 (도구 사용 필수 지시)
- ✅ 쓰레드 지원 (thread_ts)
- ✅ 메모리 컨텍스트 제한 (150/200자 절단)
- ✅ 파운더 레이더 타임아웃 수정 (60→120초)
- ✅ 원래 채널에 응답 + 에이전트 채널에 로그

---

## Phase 2: 자율 실행 시스템 — ✅ 완료 (v0.4.0)

### 2-1. 태스크 상태 관리 (TaskManager)
```
tasks.jsonl — 모든 태스크 추적
상태: pending → in_progress → completed → approved | rejected | rework
```

모든 작업(사용자 메시지, 스케줄 작업, 핸드오프)이 태스크로 기록됨.

### 2-2. 실행 엔진 분리
```
run_agent_loop() — 재사용 가능한 Claude 실행 루프
dispatch_task() — 태스크 기반 실행 + #review 승인 요청
```

handle_message, 스케줄 작업, 핸드오프 모두 동일한 실행 경로 사용.

### 2-3. 리액션 기반 승인
```
#review 메시지에 리액션:
✅ (white_check_mark) → 승인
❌ (x) → 거부
🔄 (arrows_counterclockwise) → 재작업 (최대 3회)
```

### 2-4. 주간 계획 → 태스크 배분
```
매주 월요일 8시 KST:
Chief of Staff → JSON 배열 생성 → 각 에이전트에 dispatch_task()
```

### 2-5. 에이전트 핸드오프
태스크에 handoff_to/handoff_prompt 지정 시 자동으로 다음 에이전트에 체인.

### 2-6. 태스크 큐
30분 간격으로 pending 태스크 최대 3개 자동 처리.

---

## Phase 3: GEO 비즈니스 연결 (다음 단계)

Content Lead가 Ethan 콘텐츠 GEO 최적화 자동 실행:
```
Ethan LinkedIn 포스트 발행
    ↓
Content Lead 자동 감지
    ↓
GEO 체크리스트 적용 + 개선안 생성
    ↓
#review에 "이 포스트 이렇게 바꾸면 AI 인용률 올라감" 제안
```

---

## 기술 스택

| 역할 | 도구 | 상태 |
|---|---|---|
| 에이전트 런타임 | Python + Slack Bolt | ✅ 작동 중 |
| 서버 | Railway | ✅ 배포 중 |
| AI | Claude API (Sonnet 4) | ✅ 작동 중 |
| GitHub 연동 | PyGithub | ✅ 자동 PR 생성 |
| 메모리 | JSONL (memory.jsonl) | ✅ 작동 중 |
| 태스크 관리 | JSONL (tasks.jsonl) | ✅ v0.4.0 |
| 스케줄러 | APScheduler | ✅ 작동 중 |
| 웹 검색 | Claude server-side (web_search_20250305) | ✅ 작동 중 |
| 프로필 | JSON (ethan_profile.json) | ✅ 자동 업데이트 |

---

## 성공 기준

**Phase 1 (v0.3.0) ✅:**
- Ethan이 GitHub 링크 던지면 PR이 #review에 올라온다

**Phase 2 (v0.4.0) ✅:**
- 모든 작업이 태스크로 추적되고, Ethan은 리액션으로만 승인/거부
- 주간 계획이 자동으로 태스크 생성 + 배분
- 에이전트 간 핸드오프 자동 실행

**최종 완료 기준:**
- Ethan이 하루에 Slack에 쓰는 메시지가 5개 이하. 나머지는 ✅/❌만.

---

## Slack App 설정 (v0.4.0 필수)

- Event Subscriptions에 `reaction_added` 추가
- OAuth Scopes에 `reactions:read` 추가
- 앱 재설치 필요
