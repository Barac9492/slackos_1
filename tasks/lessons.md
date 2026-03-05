# SlackOS Lessons Learned

## Architecture
- **Separate Slack Apps = separate event streams.** If using multiple apps, only the one with Socket Mode connected receives events. All channels must be joined by the listening app.
- **Duplicate routing logic is dangerous.** Keep routing in ONE place (`determine_agent()`), not scattered across handler + function.
- **Agents without tools are just chatbots.** Every agent must either have tools or extremely clear output format specs.

## Prompts
- **"Be analytical and precise" is too vague.** Agents need explicit instructions: "You MUST use web_search tool" / "NEVER fabricate data."
- **Numbered steps in system prompts must be complete.** Missing steps (3-5) caused Dev Lead to skip the branch/commit/PR workflow.

## Slack Integration
- **Socket Mode listens per-app.** One `SLACK_APP_TOKEN` = one app's events. Chief of Staff must join ALL channels to hear everything.
- **Thread support requires `thread_ts`.** Without it, every reply starts a new top-level message.
- **Always respond where the user wrote.** Don't force users to watch 5 channels. Reply in the originating channel, log elsewhere.

## Scheduled Tasks
- **Scheduled functions need tools too.** `daily_morning_briefing()` without web search = hallucinated news. Always pass the same tools scheduled agents need for interactive use.
- **Timeouts matter.** Web search tasks need 120s, not 60s.

## Memory
- **Unbounded context injection = token bombs.** Always truncate memory entries before injecting into system prompts.

## Architecture (v0.4.0)
- **Extract execution loops for reuse.** handle_message's 200-line inline loop couldn't be reused by scheduled tasks. Extracting to `run_agent_loop()` enabled both handle_message and dispatch_task to share the same code path.
- **Structured output > free text for automation.** Weekly plan generating free text was useless. Forcing JSON array output → parsed into real dispatched tasks.
- **Reactions as API.** Slack reactions are the lightest-weight approval interface. Map specific emoji to state transitions (✅→approved, ❌→rejected, 🔄→rework).
- **Task state = accountability.** Without tasks.jsonl, no way to know what was done, what's pending, or what failed. Every action now has an audit trail.
