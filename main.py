import os
import sys
import re
import json
import threading
import anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from github import Github
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

load_dotenv()
VERSION = "0.5.0"
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
github_client = Github(os.environ.get("GITHUB_TOKEN"))

executor = ThreadPoolExecutor(max_workers=8)
_agent_inflight = {}
_agent_inflight_lock = threading.Lock()
MAX_AGENT_CONCURRENCY = 2

def extract_text(content):
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if hasattr(block, "text"))

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(DATA_DIR, "memory.jsonl")
PROFILE_FILE = os.path.join(DATA_DIR, "ethan_profile.json")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.jsonl")

CHANNELS = {
    "MAIN": "#ops",
    "RESEARCH": "#ops-research",
    "DEV": "#ops-dev",
    "CONTENT": "#ops-content",
    "DESIGN": "#ops-design",
    "LOGS": "#ops-logs",
    "REVIEW": "#review"
}

AGENTS = {
    "CHIEF_OF_STAFF": {
        "name": "Chief of Staff",
        "token": os.environ.get("SLACK_BOT_TOKEN"),
        "channel": CHANNELS["MAIN"],
        "system_prompt": (
            f"[v{VERSION}] You are a ROUTER ONLY. Your only job is to say 'Routing to [Agent]'. "
            "NEVER explain, NEVER plan, NEVER suggest. 1 sentence MAX."
        ),
    },
    "RESEARCH_LEAD": {
        "name": "Research Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_RESEARCH"),
        "channel": CHANNELS["RESEARCH"],
        "system_prompt": (
            "You are Research Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "Specialize in deal sourcing, market analysis, startup evaluation, LP intelligence. "
            "Track Korean AI startups, Korean diaspora founders, investment trends.\n\n"
            "CRITICAL RULES:\n"
            "- Use web_search for real, current data. NEVER hallucinate facts.\n"
            "- Cite sources with URLs. Be analytical. Numbers over narratives.\n"
            "- Output: structured briefing with clear sections and bullet points."
        ),
    },
    "DEV_LEAD": {
        "name": "Dev Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DEV"),
        "channel": CHANNELS["DEV"],
        "system_prompt": (
            "You are Dev Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "Handle technical tasks: code, automations, GitHub repos, architecture.\n\n"
            "WORKFLOW:\n"
            "1. Extract repo name from URL.\n"
            "2. list_repository_files to explore structure.\n"
            "3. get_file_content for key files (max 3).\n"
            "4. Write improved versions.\n"
            "5. create_or_update_files to commit.\n"
            "6. create_pull_request.\n"
            "CRITICAL: Actually USE the GitHub tools."
        ),
    },
    "CONTENT_LEAD": {
        "name": "Content Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_CONTENT"),
        "channel": CHANNELS["CONTENT"],
        "system_prompt": (
            "You are Content Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "Handle LinkedIn posts, newsletters, LP materials.\n\n"
            "RULES: Use web_search before writing. Save drafts via save_to_obsidian. "
            "Apply GEO: authoritative citations, real stats, unique insights, structured format. "
            "Output: publication-ready drafts, not outlines."
        ),
    },
    "DESIGN_LEAD": {
        "name": "Design Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DESIGN"),
        "channel": CHANNELS["DESIGN"],
        "system_prompt": (
            "You are Design Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "Handle deck structure, presentation specs, wireframes, visual communication.\n\n"
            "RULES: Complete actionable specs only. For decks: slide count, layout, content, visual direction. "
            "Include hex color palettes, typography, spacing. Output: structured markdown spec."
        ),
    },
}

REVERSE_CHANNELS = {}
AGENT_CLIENTS = {key: WebClient(token=data["token"]) for key, data in AGENTS.items()}

GITHUB_TOOLS = [
    {"name": "list_repository_files", "description": "List files in a GitHub repo.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "path": {"type": "string", "default": ""}}, "required": ["repo_full_name"]}},
    {"name": "get_file_content", "description": "Get file content from GitHub.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "path": {"type": "string"}}, "required": ["repo_full_name", "path"]}},
    {"name": "create_or_update_files", "description": "Commit files to a branch.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "branch_name": {"type": "string"}, "commit_message": {"type": "string"}, "file_changes": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}, "required": ["repo_full_name", "branch_name", "commit_message", "file_changes"]}},
    {"name": "create_pull_request", "description": "Create a PR on GitHub.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string", "default": "main"}}, "required": ["repo_full_name", "title", "body", "head"]}},
]
SAVE_TO_OBSIDIAN_TOOL = {"name": "save_to_obsidian", "description": "Save content as markdown in Obsidian vault.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
RESEARCH_TOOLS = [t for t in GITHUB_TOOLS if t["name"] in ("list_repository_files", "get_file_content")]
CONTENT_TOOLS = [SAVE_TO_OBSIDIAN_TOOL]


def alert_error(agent_name, error_msg, channel_id=None):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"CRITICAL ERROR | Agent: {agent_name} | {timestamp}\nError: {error_msg}"
    try:
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"], text=log_msg)
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["REVIEW"], text=f"System Alert: {agent_name} error. Check #ops-logs.")
    except Exception as e:
        print(f"Failed to post error: {e}")


def execute_github_tool(name, input_data):
    try:
        if name == "list_repository_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            contents = repo.get_contents(input_data.get("path", ""))
            return [c.path for c in contents]
        elif name == "get_file_content":
            repo = github_client.get_repo(input_data["repo_full_name"])
            def get_recursive(path, depth=0):
                if depth > 3:
                    return []
                found = []
                try:
                    contents = repo.get_contents(path)
                    if not isinstance(contents, list):
                        return [contents.path] if contents.name.lower().endswith(('.py', '.md', '.txt', '.json')) else []
                    for item in contents:
                        if item.type == "file" and item.name.lower().endswith(('.py', '.md', '.txt', '.json')):
                            found.append(item.path)
                        elif item.type == "dir":
                            found.extend(get_recursive(item.path, depth + 1))
                except Exception:
                    pass
                return found
            contents = repo.get_contents(input_data["path"])
            if isinstance(contents, list) or (hasattr(contents, "type") and contents.type == "dir"):
                return {"type": "directory", "files": get_recursive(input_data["path"])}
            return contents.decoded_content.decode("utf-8")
        elif name == "create_or_update_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            base_branch = repo.get_branch("main")
            try:
                repo.get_branch(input_data["branch_name"])
            except Exception:
                repo.create_git_ref(ref=f"refs/heads/{input_data['branch_name']}", sha=base_branch.commit.sha)
            for change in input_data["file_changes"]:
                try:
                    existing = repo.get_contents(change["path"], ref=input_data["branch_name"])
                    repo.update_file(change["path"], input_data["commit_message"], change["content"], existing.sha, branch=input_data["branch_name"])
                except Exception:
                    repo.create_file(change["path"], input_data["commit_message"], change["content"], branch=input_data["branch_name"])
            return f"Committed to {input_data['branch_name']}"
        elif name == "create_pull_request":
            repo = github_client.get_repo(input_data["repo_full_name"])
            pr = repo.create_pull(title=input_data["title"], body=input_data["body"], head=input_data["head"], base=input_data.get("base", "main"))
            return {"pr_url": pr.html_url, "number": pr.number}
    except Exception as e:
        return f"Error: {str(e)}"


def execute_storage_tool(name, input_data):
    try:
        if name == "save_to_obsidian":
            vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", os.path.join(DATA_DIR, "obsidian"))
            os.makedirs(vault_path, exist_ok=True)
            file_path = os.path.join(vault_path, input_data["filename"])
            with open(file_path, "w") as f:
                f.write(input_data["content"])
            return f"Saved to {file_path}"
    except Exception as e:
        return f"Storage error: {str(e)}"


class MemoryManager:
    @staticmethod
    def save(agent_name, user_msg, response, channel):
        entry = {"timestamp": datetime.now().isoformat(), "channel": channel, "agent": agent_name, "user_message": user_msg, "agent_response": response}
        with open(MEMORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def get_context(agent_key, limit=20):
        if not os.path.exists(MEMORY_FILE):
            return ""
        memories = []
        with open(MEMORY_FILE, "r") as f:
            for line in f:
                try:
                    memories.append(json.loads(line))
                except Exception:
                    continue
        agent_name = AGENTS.get(agent_key, {}).get("name", agent_key)
        relevant = [m for m in memories if m["agent"] == agent_name or m["agent"] == "Chief of Staff"]
        context = relevant[-limit:]
        if not context:
            return ""
        ctx_str = "\nPrevious context:\n"
        for m in context:
            ctx_str += f"- User: {m['user_message'][:150]}\n  {m['agent']}: {m['agent_response'][:200]}\n"
        return ctx_str


class ProfileManager:
    @staticmethod
    def load():
        if not os.path.exists(PROFILE_FILE):
            return {"style_preferences": "Short sentences, direct judgment, no AI cliches", "recurring_topics": [], "decision_patterns": []}
        with open(PROFILE_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}

    @staticmethod
    def save(profile):
        with open(PROFILE_FILE, "w") as f:
            json.dump(profile, f, indent=4, ensure_ascii=False)

    @staticmethod
    def update_profile(memories_count):
        if memories_count > 0 and memories_count % 10 == 0:
            with open(MEMORY_FILE, "r") as f:
                lines = f.readlines()
            recent = [json.loads(l) for l in lines[-10:]]
            curr = ProfileManager.load()
            prompt = f"Analyze these interactions and update user profile.\nCurrent: {json.dumps(curr)}\nRecent: {json.dumps(recent)}\nReturn ONLY JSON."
            try:
                res = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=1000, messages=[{"role": "user", "content": prompt}])
                txt = extract_text(res.content)
                new_p = json.loads(txt[txt.find("{"):txt.rfind("}")+1])
                ProfileManager.save(new_p)
            except Exception as e:
                print(f"Profile update failed: {e}")


class TaskManager:
    _counter = 0
    _lock = threading.Lock()

    @staticmethod
    def _next_id():
        with TaskManager._lock:
            TaskManager._counter += 1
            return f"T-{datetime.now().strftime('%Y%m%d')}-{TaskManager._counter:03d}"

    @staticmethod
    def create(agent_key, description, source, channel_id, thread_ts=None, priority="P1", parent_task_id=None, handoff_to=None, handoff_prompt=None):
        task = {
            "task_id": TaskManager._next_id(),
            "agent_key": agent_key,
            "description": description,
            "status": "pending",
            "priority": priority,
            "source": source,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "result_summary": None,
            "review_message_ts": None,
            "parent_task_id": parent_task_id,
            "handoff_to": handoff_to,
            "handoff_prompt": handoff_prompt,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "rework_count": 0,
        }
        with TaskManager._lock:
            with open(TASKS_FILE, "a") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        print(f"Task created: {task['task_id']} -> {agent_key}")
        return task["task_id"]

    @staticmethod
    def _load_all():
        if not os.path.exists(TASKS_FILE):
            return []
        tasks = []
        with open(TASKS_FILE, "r") as f:
            for line in f:
                try:
                    tasks.append(json.loads(line))
                except Exception:
                    continue
        return tasks

    @staticmethod
    def _save_all(tasks):
        with TaskManager._lock:
            with open(TASKS_FILE, "w") as f:
                for t in tasks:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")

    @staticmethod
    def update(task_id, **fields):
        tasks = TaskManager._load_all()
        for t in tasks:
            if t["task_id"] == task_id:
                t.update(fields)
                break
        TaskManager._save_all(tasks)

    @staticmethod
    def get(task_id):
        for t in TaskManager._load_all():
            if t["task_id"] == task_id:
                return t
        return None

    @staticmethod
    def get_pending(agent_key=None):
        tasks = TaskManager._load_all()
        pending = [t for t in tasks if t["status"] == "pending"]
        if agent_key:
            pending = [t for t in pending if t["agent_key"] == agent_key]
        pending.sort(key=lambda t: t.get("priority", "P1"))
        return pending

    @staticmethod
    def find_by_review_ts(message_ts):
        for t in TaskManager._load_all():
            if t.get("review_message_ts") == message_ts:
                return t
        return None


def log_api_call(agent_name, status="Success"):
    try:
        msg = f"API Log: {agent_name} | {status} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"], text=msg)
    except Exception:
        pass


def determine_agent(text, channel_id=None):
    text_lower = text.lower()
    if channel_id and channel_id in REVERSE_CHANNELS:
        affinity_agent = REVERSE_CHANNELS[channel_id]
        if affinity_agent != "CHIEF_OF_STAFF":
            return affinity_agent, f"Channel affinity ({affinity_agent})"
    if re.search(r"github\.com/[\w-]+/[\w-]+", text_lower) or any(k in text_lower for k in ["code", "github", "technical", "develop", "build", "fix", "bug", "python", "script", "api", "개선"]):
        return "DEV_LEAD", "Technical/GitHub keyword"
    if any(k in text_lower for k in ["research", "analyze", "market", "startup", "investor", "fund", "vc", "news", "분석", "조사"]):
        return "RESEARCH_LEAD", "Research keyword"
    if any(k in text_lower for k in ["write", "post", "linkedin", "newsletter", "content", "draft", "article"]):
        return "CONTENT_LEAD", "Content keyword"
    if any(k in text_lower for k in ["deck", "design", "slide", "visual", "ui", "ux", "wireframe", "ppt"]):
        return "DESIGN_LEAD", "Design keyword"
    print("Routing Guard: Consulting Claude...")
    prompt = ("Decide agent: DEV_LEAD, RESEARCH_LEAD, CONTENT_LEAD, DESIGN_LEAD, or CHIEF_OF_STAFF.\n"
              "DEV_LEAD: technical/code. RESEARCH_LEAD: market/startups. CONTENT_LEAD: writing. DESIGN_LEAD: decks.\n"
              f"Message: {text}\nReturn ONLY the agent key.")
    try:
        res = claude.messages.create(model="claude-3-haiku-20240307", max_tokens=10, messages=[{"role": "user", "content": prompt}])
        decision = extract_text(res.content).strip().upper()
        if decision in AGENTS:
            return decision, f"LLM Guard ({decision})"
    except Exception as e:
        print(f"Routing Guard failed: {e}")
    return "CHIEF_OF_STAFF", "Default"


def run_agent_loop(agent_key, text):
    """Execute agent Claude loop with tools. Returns final reply text."""
    agent_config = AGENTS[agent_key]
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = (
        f"{agent_config['system_prompt']}\n\n"
        f"CURRENT TIME: {datetime.now().isoformat()}\n\n"
        f"USER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n"
        f"{ctx}"
    )
    messages = [{"role": "user", "content": text}]
    client_tools, server_tools = [], []
    if agent_key == "DEV_LEAD":
        client_tools = GITHUB_TOOLS
    elif agent_key == "RESEARCH_LEAD":
        client_tools = RESEARCH_TOOLS
        server_tools = [WEB_SEARCH_TOOL]
    elif agent_key == "CONTENT_LEAD":
        client_tools = CONTENT_TOOLS
        server_tools = [WEB_SEARCH_TOOL]
    all_tools = client_tools + server_tools

    files_read_count = 0
    last_read_paths = []
    max_files = 3
    max_chars = 2000
    repo_full_name = "unknown"

    while True:
        print(f"Calling Claude for {agent_key} (msgs: {len(messages)})...", flush=True)
        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192 if agent_key == "DEV_LEAD" else (2048 if agent_key != "CHIEF_OF_STAFF" else 100),
            system=sys_prompt,
            tools=all_tools if all_tools else anthropic.NOT_GIVEN,
            messages=messages,
            timeout=120.0
        )
        print(f"Claude responded: stop_reason={res.stop_reason}", flush=True)

        if res.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": res.content})
            tool_results = []
            for block in res.content:
                if block.type == "tool_use":
                    if "repo_full_name" in block.input:
                        repo_full_name = block.input["repo_full_name"]
                    if agent_key == "DEV_LEAD" and block.name == "get_file_content":
                        if "path" in block.input:
                            last_read_paths.append(block.input["path"])
                        if files_read_count >= max_files:
                            result = "Limit of 3 files reached. Proceed with available info."
                        else:
                            raw = execute_github_tool(block.name, block.input)
                            result = (raw[:max_chars] + "... [truncated]") if isinstance(raw, str) and len(raw) > max_chars else raw
                            files_read_count += 1
                    else:
                        if block.name == "save_to_obsidian":
                            result = execute_storage_tool(block.name, block.input)
                        else:
                            result = execute_github_tool(block.name, block.input)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            messages.append({"role": "user", "content": tool_results})
            if agent_key == "DEV_LEAD" and files_read_count == max_files:
                if not any("Improve this code" in str(m.get("content", "")) for m in messages):
                    messages.append({"role": "user", "content": "File limit reached. Provide improved code now using ```python:path/to/file.py format."})
            continue

        if res.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": res.content})
            messages.append({"role": "user", "content": "Continue."})
            continue

        reply = extract_text(res.content)

        if agent_key == "DEV_LEAD" and files_read_count > 0:
            code_matches = re.findall(r"```(?:\w+)?[:\s]?([\w\./-]+)?\n(.*?)\n```", reply, re.DOTALL)
            if not code_matches:
                raw_matches = re.findall(r"```(?:\w+)?\n(.*?)\n```", reply, re.DOTALL)
                if raw_matches:
                    code_matches = [(last_read_paths[0] if last_read_paths else "main.py", c) for c in raw_matches]
            if code_matches and repo_full_name != "unknown":
                ts = datetime.now().strftime("%Y%m%d%H%M%S")
                branch_name = f"slackos-fix-{ts}"
                file_changes = [{"path": p or (last_read_paths[0] if last_read_paths else "main.py"), "content": c} for p, c in code_matches]
                if file_changes:
                    execute_github_tool("create_or_update_files", {"repo_full_name": repo_full_name, "branch_name": branch_name, "commit_message": "SlackOS: Code improvements", "file_changes": file_changes})
                    pr_res = execute_github_tool("create_pull_request", {"repo_full_name": repo_full_name, "title": "SlackOS: Code improvements", "body": "Automated improvements by SlackOS Dev Lead.", "head": branch_name})
                    if isinstance(pr_res, dict) and "pr_url" in pr_res:
                        reply += f"\n\nPR created: {pr_res['pr_url']}"
        return reply


def _can_dispatch(agent_key):
    """Returns True if agent has capacity for another in-flight task."""
    with _agent_inflight_lock:
        count = _agent_inflight.get(agent_key, 0)
        if count < MAX_AGENT_CONCURRENCY:
            _agent_inflight[agent_key] = count + 1
            return True
        return False


def _release_inflight(agent_key):
    with _agent_inflight_lock:
        _agent_inflight[agent_key] = max(0, _agent_inflight.get(agent_key, 1) - 1)


def dispatch_task(task_id):
    """
    Execute a tracked task in the CURRENT thread.
    Always called from inside the ThreadPoolExecutor.
    """
    task = TaskManager.get(task_id)
    if not task:
        print(f"dispatch_task: task {task_id} not found")
        return

    agent_key = task["agent_key"]
    agent_config = AGENTS[agent_key]
    agent_client = AGENT_CLIENTS[agent_key]

    TaskManager.update(task_id, status="in_progress")
    print(f"Dispatching {task_id} to {agent_key}...", flush=True)

    try:
        prompt = task["description"]
        if task.get("rework_count", 0) > 0 and task.get("result_summary"):
            prompt = (
                f"REWORK REQUEST (attempt {task['rework_count'] + 1}):\n"
                f"Previous result was not approved.\n"
                f"Previous output: {task['result_summary']}\n\n"
                f"Original task: {task['description']}"
            )

        reply = run_agent_loop(agent_key, prompt)
        if not reply or not reply.strip():
            reply = "Agent action completed (no text response)."

        TaskManager.update(task_id, status="completed", result_summary=reply[:500], completed_at=datetime.now().isoformat())
        log_api_call(agent_config["name"])

        review_msg = (
            f"*Task {task_id}* by *{agent_config['name']}*\n"
            f"*Priority:* {task.get('priority', 'P1')} | *Source:* {task.get('source', 'unknown')}\n\n"
            f"{reply[:1500]}\n\nReact: approve | reject | rework"
        )
        result = agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=review_msg)
        TaskManager.update(task_id, review_message_ts=result["ts"])

        if task.get("channel_id") and task["channel_id"] != CHANNELS["REVIEW"]:
            agent_client.chat_postMessage(
                channel=task["channel_id"],
                text=f"*[{agent_config['name']} v{VERSION}]*\n{reply}",
                thread_ts=task.get("thread_ts")
            )

        MemoryManager.save(agent_config["name"], task["description"], reply, task.get("channel_id", ""))
        try:
            with open(MEMORY_FILE, "r") as f:
                count = sum(1 for _ in f)
            ProfileManager.update_profile(count)
        except Exception:
            pass

        if task.get("handoff_to") and task["handoff_to"] in AGENTS:
            child_id = TaskManager.create(
                agent_key=task["handoff_to"],
                description=task.get("handoff_prompt") or f"Continue from {task_id}: {reply[:200]}",
                source=f"handoff:{agent_key}",
                channel_id=task.get("channel_id", CHANNELS["REVIEW"]),
                parent_task_id=task_id
            )
            submit_task(child_id)

    except Exception as e:
        TaskManager.update(task_id, status="failed", result_summary=str(e)[:500])
        alert_error(agent_key, f"Task {task_id} failed: {e}", task.get("channel_id"))
        print(f"dispatch_task failed for {task_id}: {e}")
    finally:
        _release_inflight(agent_key)


def submit_task(task_id):
    """
    Submit a task to the thread pool for async execution.
    Respects per-agent concurrency limit; leaves as pending if at capacity.
    """
    task = TaskManager.get(task_id)
    if not task:
        return
    agent_key = task["agent_key"]
    if _can_dispatch(agent_key):
        TaskManager.update(task_id, status="queued")
        executor.submit(dispatch_task, task_id)
        print(f"Submitted {task_id} to executor", flush=True)
    else:
        print(f"Agent {agent_key} at capacity. Task {task_id} stays pending.")


app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])


@app.event("message")
def handle_message(event, client, say):
    """
    Receives Slack message, immediately creates a task, submits to executor.
    Returns instantly — no blocking on agent execution.
    """
    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return

    channel_id = event["channel"]
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event.get("ts")

    if not text:
        return

    try:
        print(f"Input: {text[:50]}...", flush=True)
        agent_key, task_summary = determine_agent(text, channel_id)

        try:
            with open(os.path.join(DATA_DIR, "routing_debug.txt"), "a") as f:
                f.write(f"[{datetime.now().isoformat()}] CH:{channel_id} | AGENT:{agent_key} | MSG:{text[:50]}\n")
        except OSError:
            pass

        print(f"Route: {agent_key}", flush=True)

        if agent_key != "CHIEF_OF_STAFF":
            AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(
                channel=CHANNELS["MAIN"],
                text=f"Routing to {AGENTS[agent_key]['name']}: {task_summary}"
            )

        task_id = TaskManager.create(
            agent_key=agent_key,
            description=text,
            source=f"user:{event.get('user', 'unknown')}",
            channel_id=channel_id,
            thread_ts=thread_ts
        )
        submit_task(task_id)

    except Exception as e:
        alert_error("handle_message", str(e), channel_id)
        print(f"handle_message failed: {e}")
    sys.stdout.flush()


@app.event("reaction_added")
def handle_reaction(event, client):
    reaction = event.get("reaction", "")
    item = event.get("item", {})
    message_ts = item.get("ts")

    if not message_ts:
        return

    task = TaskManager.find_by_review_ts(message_ts)
    if not task:
        return

    task_id = task["task_id"]
    agent_client = AGENT_CLIENTS.get(task["agent_key"])

    if reaction == "white_check_mark":
        TaskManager.update(task_id, status="approved")
        print(f"Task {task_id} approved")
        if agent_client:
            agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"Task {task_id} approved.", thread_ts=message_ts)

    elif reaction == "x":
        TaskManager.update(task_id, status="rejected")
        print(f"Task {task_id} rejected")
        if agent_client:
            agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"Task {task_id} rejected.", thread_ts=message_ts)

    elif reaction == "arrows_counterclockwise":
        rework_count = task.get("rework_count", 0)
        if rework_count >= 3:
            if agent_client:
                agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"Task {task_id} hit max rework limit (3). Manual intervention needed.", thread_ts=message_ts)
            return
        TaskManager.update(task_id, status="pending", rework_count=rework_count + 1)
        if agent_client:
            agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"Task {task_id} queued for rework (attempt {rework_count + 2}/4).", thread_ts=message_ts)
        submit_task(task_id)


def join_channels():
    print("\nStarting Channel Joining...")
    channel_cache = {}

    def get_channel_id(client, name):
        if name in channel_cache:
            return channel_cache[name]
        try:
            clean_name = name.lstrip("#")
            cursor = None
            while True:
                response = client.conversations_list(types="public_channel", cursor=cursor, limit=1000)
                for channel in response["channels"]:
                    if channel["name"] == clean_name:
                        channel_cache[name] = channel["id"]
                        for agent_key, data in AGENTS.items():
                            if data["channel"] == name:
                                REVERSE_CHANNELS[channel["id"]] = agent_key
                        return channel["id"]
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as e:
            print(f"Failed to find {name}: {e}")
        return None

    JOIN_MAP = {
        "CHIEF_OF_STAFF": [CHANNELS["MAIN"], CHANNELS["REVIEW"], CHANNELS["LOGS"], CHANNELS["RESEARCH"], CHANNELS["DEV"], CHANNELS["CONTENT"], CHANNELS["DESIGN"]],
        "RESEARCH_LEAD": [CHANNELS["MAIN"], CHANNELS["RESEARCH"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "DEV_LEAD": [CHANNELS["MAIN"], CHANNELS["DEV"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "CONTENT_LEAD": [CHANNELS["MAIN"], CHANNELS["CONTENT"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "DESIGN_LEAD": [CHANNELS["MAIN"], CHANNELS["DESIGN"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
    }

    for agent_key, channels in JOIN_MAP.items():
        client = AGENT_CLIENTS[agent_key]
        agent_name = AGENTS[agent_key]["name"]
        for ch_name in channels:
            ch_id = get_channel_id(client, ch_name)
            if ch_id:
                try:
                    client.conversations_join(channel=ch_id)
                    print(f"{agent_name} joined {ch_name}")
                except Exception as e:
                    print(f"{agent_name} failed to join {ch_name}: {e}")
            else:
                print(f"Could not resolve {ch_name}")
    print("Channel joining complete.\n")


def _schedule_task(agent_key, description, source, priority="P0"):
    try:
        task_id = TaskManager.create(
            agent_key=agent_key,
            description=description,
            source=source,
            channel_id=CHANNELS["REVIEW"],
            priority=priority
        )
        submit_task(task_id)
    except Exception as e:
        alert_error(agent_key, f"Scheduled job {source} failed: {e}")


def daily_morning_briefing():
    print("Daily Morning Briefing...")
    _schedule_task(
        "RESEARCH_LEAD",
        "Ethan의 CIO 역할을 돕기 위해 오늘 아침 한국 AI 스타트업 동향과 주요 뉴스를 요약해서 브리핑해줘.",
        "scheduled:daily_briefing",
        "P0"
    )


WEEKLY_PLANNING_PROMPT = (
    "You are Chief of Staff for Ethan's AI team at TheVentures. "
    "Generate a weekly task plan. Return ONLY a valid JSON array. No markdown, no explanation.\n"
    "Each task: {agent_key, description, priority (P0/P1/P2)}.\n"
    "Agents: RESEARCH_LEAD, DEV_LEAD, CONTENT_LEAD, DESIGN_LEAD.\n"
    "Generate 4-8 specific, actionable tasks referencing real recurring workflows."
)


def weekly_task_coordination():
    print("Weekly Task Coordination...")
    try:
        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=WEEKLY_PLANNING_PROMPT,
            messages=[{"role": "user", "content": "이번 주 Ethan을 위한 AI 팀 주간 태스크를 JSON 배열로 생성해줘."}]
        )
        reply_text = extract_text(res.content)
        tasks_dispatched = []
        try:
            json_str = reply_text
            if "```" in json_str:
                match = re.search(r'\[.*\]', json_str, re.DOTALL)
                json_str = match.group(0) if match else reply_text
            elif "[" in json_str:
                start = json_str.index("[")
                end = json_str.rindex("]") + 1
                json_str = json_str[start:end]
            task_list = json.loads(json_str)
            for t in task_list:
                agent_key = t.get("agent_key", "").upper()
                if agent_key not in AGENTS or agent_key == "CHIEF_OF_STAFF":
                    continue
                task_id = TaskManager.create(
                    agent_key=agent_key,
                    description=t.get("description", "Weekly task"),
                    source="scheduled:weekly_plan",
                    channel_id=CHANNELS["REVIEW"],
                    priority=t.get("priority", "P1")
                )
                tasks_dispatched.append({"task_id": task_id, "agent": agent_key, "desc": t.get("description", "")[:80]})
            for td in tasks_dispatched:
                submit_task(td["task_id"])
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Weekly plan parse failed: {e}")

        client = AGENT_CLIENTS["CHIEF_OF_STAFF"]
        if tasks_dispatched:
            lines = [f"- *{td['agent']}*: {td['desc']} (`{td['task_id']}`)" for td in tasks_dispatched]
            client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"[v{VERSION}] *Weekly Coordination*\n{len(tasks_dispatched)} tasks queued:\n" + "\n".join(lines))
        else:
            client.chat_postMessage(channel=CHANNELS["REVIEW"], text=f"[v{VERSION}] *Weekly Coordination*\n{reply_text}")
    except Exception as e:
        alert_error("CHIEF_OF_STAFF", f"Weekly Coordination failed: {e}")


def founder_radar():
    print("Founder Radar...")
    _schedule_task(
        "RESEARCH_LEAD",
        (
            "Search for recently funded Korean and Korean-diaspora founders. Focus on: "
            "1. Recent YC batches (last 6 months). "
            "2. Korean-founded startups that raised seed or Series A. "
            "3. Korean AI/tech founders globally. "
            "For each: Name, company, one-line description, funding stage, why relevant to TheVentures, confidence (High/Medium/Low). "
            "Only include founders with real, verifiable data."
        ),
        "scheduled:founder_radar",
        "P1"
    )


def process_pending_tasks():
    """
    Pick up pending tasks and submit to executor.
    Runs every 5 minutes. Processes up to 5 tasks per cycle.
    Skips agents at capacity (retried next cycle).
    """
    pending = TaskManager.get_pending()
    if not pending:
        return
    print(f"Task queue: {len(pending)} pending. Processing up to 5...", flush=True)
    submitted = 0
    for task in pending:
        if submitted >= 5:
            break
        agent_key = task["agent_key"]
        with _agent_inflight_lock:
            inflight = _agent_inflight.get(agent_key, 0)
        if inflight >= MAX_AGENT_CONCURRENCY:
            print(f"Skipping {task['task_id']}: {agent_key} at capacity")
            continue
        submit_task(task["task_id"])
        submitted += 1


scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Seoul'))
scheduler.add_job(daily_morning_briefing, 'cron', hour=7, minute=0)
scheduler.add_job(weekly_task_coordination, 'cron', day_of_week='mon', hour=8, minute=0)
scheduler.add_job(founder_radar, 'interval', hours=12)
scheduler.add_job(process_pending_tasks, 'interval', minutes=5)


if __name__ == "__main__":
    join_channels()
    scheduler.start()
    print(f"SlackOS v{VERSION} is live.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
