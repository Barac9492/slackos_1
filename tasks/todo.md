# SlackOS v0.4.0 — Autonomous Team

## v0.3.0 Fixes (Completed)
- [x] Fix 1: Chief of Staff joins ALL 7 channels
- [x] Fix 2: Remove duplicate routing
- [x] Fix 3: Add web search to daily briefing
- [x] Fix 4: Strengthen all agent system prompts
- [x] Fix 5: Add thread support
- [x] Fix 6: Bound memory context
- [x] Fix 7: Fix founder radar timeout
- [x] Fix 8: Respond in originating channel + log

## v0.4.0 Autonomous Features (Completed)
- [x] TaskManager class (tasks.jsonl persistence)
- [x] Extract run_agent_loop() from handle_message
- [x] dispatch_task() for programmatic task execution
- [x] Simplify handle_message (uses run_agent_loop + task tracking)
- [x] reaction_added handler (✅ approve / ❌ reject / 🔄 rework)
- [x] Scheduled tasks use dispatch_task (morning briefing, founder radar)
- [x] Weekly plan → structured JSON → real dispatched tasks
- [x] Task queue processor (30min interval, max 3 per cycle)
- [x] Update PRD to v0.4
- [x] Version bump to 0.4.0

## Verification Steps
- [ ] Configure Slack App: add `reaction_added` event + `reactions:read` scope
- [ ] Deploy to Railway
- [ ] Post in #ops → verify task created in tasks.jsonl + agent responds
- [ ] Check #review → verify task result posted with reaction prompt
- [ ] React ✅ on a #review message → verify task status → "approved"
- [ ] React 🔄 → verify agent re-executes with rework context
- [ ] Wait for 7am KST → verify morning briefing creates tracked task
- [ ] Wait for Monday 8am → verify weekly plan dispatches real tasks
- [ ] Check tasks.jsonl → verify task state transitions
