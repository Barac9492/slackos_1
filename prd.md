# SlackOS PRD v0.2
**Slack 기반 AI 팀 운영 시스템**
*작성일: 2026.03.04*

---

## 최종 목표 (End Goal)

> Ethan이 Slack에 한 줄 던지면, AI 팀이 실제 결과물(코드, PR, 문서, 분석)을 만들어서 #review에 올려놓는다. Ethan은 승인만 한다.

---

## 현재 상태 vs 목표 상태

| | 지금 | 목표 |
|---|---|---|
| 라우팅 | ✅ 키워드 매칭으로 작동 | ✅ 유지 |
| 채널 구조 | ✅ 7개 채널 운영 중 | ✅ 유지 |
| GitHub 읽기 | ✅ 파일 읽기 됨 | ✅ 유지 |
| GitHub 쓰기 | ❌ PR 생성 안 됨 | ✅ 실제 PR 생성 |
| 학습 시스템 | ❌ 없음 | ✅ Ethan 패턴 학습 |
| 자율 실행 | ❌ 없음 | ✅ 스케줄 기반 자동 실행 |

---

## 에이전트 구성

| 에이전트 | 채널 | 역할 | 실제 실행 가능한 것 |
|---|---|---|---|
| **Chief of Staff** | #ops, #review | 라우팅, 조율 | 키워드 매칭 라우팅 |
| **Dev Lead** | #ops-dev | 코드 작업 | GitHub 읽기/쓰기, PR 생성 |
| **Research Lead** | #ops-research | 리서치 | 웹 검색, 데이터 수집 |
| **Content Lead** | #ops-content | 글쓰기 | 초안 작성, Obsidian 저장 |
| **Design Lead** | #ops-design | 구조 설계 | 덱 구조, 스펙 문서 |

---

## Phase 1: 지금 당장 고쳐야 할 것 (이번 주)

### 1-1. Dev Lead GitHub 완성
```
현재: 파일 읽기까지 됨, PR 생성 안 됨
목표: 파일 읽기 → Claude 개선 → 브랜치 생성 → PR 오픈 → #review에 PR 링크
```

버그 목록:
- 파일 너무 많이 읽어서 타임아웃 → 최대 3파일, 2000자 제한
- 폴더를 파일로 읽으려는 에러 → type == "file" 체크
- Claude 응답 후 커밋 안 됨 → 커밋 로직 추가

### 1-2. #review 완성
모든 에이전트가 작업 완료 후 #review에 요약 + 결과물 링크 포스팅.

### 1-3. 에러 핸들링
지금은 에러 나면 조용히 죽음. 에러 시 #ops-logs에 기록 + Ethan에게 #review로 알림.

---

## Phase 2: 학습 시스템 (다음 주)

### 2-1. 대화 메모리
```python
memory.jsonl
{
  "timestamp": "2026-03-04T11:57:13",
  "agent": "DEV_LEAD",
  "task": "GitHub 개선",
  "result": "PR #3 생성",
  "ethan_feedback": "승인"
}
```

### 2-2. Ethan 프로필
Chief of Staff가 10회 대화마다 자동 업데이트:
```json
ethan_profile.json
{
  "preferred_response_style": "간결, 결과물 중심",
  "recurring_tasks": ["GitHub 개선", "리서치", "콘텐츠"],
  "feedback_patterns": ["PR 링크 먼저", "설명 짧게"],
  "timezone": "KST"
}
```

### 2-3. 프로필 주입
모든 에이전트 System Prompt에 ethan_profile.json 자동 주입.

---

## Phase 3: 자율 실행 (2주 후)

### 3-1. 스케줄 트리거
```
매일 오전 7시 KST:
→ Research Lead: 한국 AI 스타트업 동향 수집 → #review 브리핑

매주 월요일 오전 8시:
→ Chief of Staff: 주간 태스크 자동 생성 → 각 에이전트 배분
```

### 3-2. Founder Radar 자동화
```
YC 배치 발표 감지
    ↓
Research Lead 자동 실행
    ↓
한국계 창업자 필터링 + Founder Intelligence 스코어링
    ↓
#review에 "이 사람 볼만함" 브리핑
```

---

## Phase 4: GEO 비즈니스 연결 (한 달 후)

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
| AI | Claude API (Sonnet) | ✅ 작동 중 |
| GitHub 연동 | PyGithub | 🔧 PR 생성 버그 수정 중 |
| 메모리 | JSONL | ❌ 미구현 |
| 스케줄러 | APScheduler | ❌ 미구현 |
| 웹 검색 | Perplexity API | ❌ 미구현 |

---

## 성공 기준

**Phase 1 완료 기준:**
- Ethan이 GitHub 링크 던지면 30분 안에 PR이 #review에 올라온다

**Phase 2 완료 기준:**
- 에이전트가 Ethan의 이전 피드백을 반영해서 응답 스타일이 달라진다

**Phase 3 완료 기준:**
- Ethan이 아무것도 안 해도 매일 아침 #review에 브리핑이 올라와 있다

**최종 완료 기준:**
- Ethan이 하루에 Slack에 쓰는 메시지가 5개 이하. 나머지는 승인/거부만.

---

*지금 당장 할 것: Dev Lead GitHub PR 생성 버그 수정*