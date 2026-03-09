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
VERSION = "0.6.0"
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

# ── Persistence ──────────────────────────────────────────────────
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE      = os.path.join(DATA_DIR, "memory.jsonl")
PROFILE_FILE     = os.path.join(DATA_DIR, "ethan_profile.json")
TASKS_FILE       = os.path.join(DATA_DIR, "tasks.jsonl")
DELIBERATION_FILE = os.path.join(DATA_DIR, "deliberations.jsonl")

# ── Channels ─────────────────────────────────────────────────────
CHANNELS = {
    "MAIN":          "#ops",
    "RESEARCH":      "#ops-research",
    "DEV":           "#ops-dev",
    "CONTENT":       "#ops-content",
    "DESIGN":        "#ops-design",
    "LOGS":          "#ops-logs",
    "REVIEW":        "#review",
    "DELIBERATION":  "#ops-deliberation",   # NEW: agents discuss here
}

# ── Agent Configuration ──────────────────────────────────────────
AGENTS = {
    "CHIEF_OF_STAFF": {
        "name": "Chief of Staff",
        "emoji": ":shield:",
        "token": os.environ.get("SLACK_BOT_TOKEN"),
        "channel": CHANNELS["MAIN"],
        "system_prompt": (
            f"[v{VERSION}] You are Chief of Staff for Ethan's AI team at TheVentures. "
            "You orchestrate the team: assign tasks, synthesize agent outputs, and present "
            "a single unified recommendation to Ethan for approval. "
            "Be concise. Lead the team, don't do the work yourself."
        ),
    },
    "RESEARCH_LEAD": {
        "name": "Research Lead",
        "emoji": ":mag:",
        "token": os.environ.get("SLACK_BOT_TOKEN_RESEARCH"),
        "channel": CHANNELS["RESEARCH"],
        "system_prompt": (
            "You are Research Lead on Ethan's AI team at TheVentures. "
            "Specialize in deal sourcing, market analysis, startup evaluation, LP intelligence. "
            "Track Korean AI startups, Korean diaspora founders, investment trends.\n\n"
            "RULES: Use web_search for current data. Never hallucinate. Cite sources with URLs. "
            "Numbers over narratives. Output: structured briefing with clear sections."
        ),
    },
    "DEV_LEAD": {
        "name": "Dev Lead",
        "emoji": ":computer:",
        "token": os.environ.get("SLACK_BOT_TOKEN_DEV"),
        "channel": CHANNELS["DEV"],
        "system_prompt": (
            "You are Dev Lead on Ethan's AI team at TheVentures. "
            "Handle technical tasks: code, automations, GitHub repos, architecture.\n\n"
            "WORKFLOW: 1. Extract repo from URL. 2. list_repository_files. "
            "3. get_file_content (max 3). 4. Write improved code. "
            "5. create_or_update_files. 6. create_pull_request. "
            "CRITICAL: Actually USE the GitHub tools."
        ),
    },
    "CONTENT_LEAD": {
        "name": "Content Lead",
        "emoji": ":pencil:",
        "token": os.environ.get("SLACK_BOT_TOKEN_CONTENT"),
        "channel": CHANNELS["CONTENT"],
        "system_prompt": (
            "You are Content Lead on Ethan's AI team at TheVentures. "
            "Handle LinkedIn posts, newsletters, LP materials.\n\n"
            "RULES: Use web_search before writing. Save via save_to_obsidian. "
            "Apply GEO: authoritative citations, real stats, unique insights, structured format. "
            "Output: publication-ready drafts."
        ),
    },
    "DESIGN_LEAD": {
        "name": "Design Lead",
        "emoji": ":art:",
        "token": os.environ.get("SLACK_BOT_TOKEN_DESIGN"),
        "channel": CHANNELS["DESIGN"],
        "system_prompt": (
            "You are Design Lead on Ethan's AI team at TheVentures. "
            "Handle deck structure, presentation specs, wireframes, visual communication.\n\n"
            "RULES: Complete actionable specs only. Include hex colors, typography, spacing. "
            "Output: structured markdown spec a designer can directly implement."
        ),
    },
}

REVERSE_CHANNELS = {}
AGENT_CLIENTS = {key: WebClient(token=data["token"]) for key, data in AGENTS.items()}

# ── Tools ────────────────────────────────────────────────────────
GITHUB_TOOLS = [
    {"name": "list_repository_files", "description": "List files in a GitHub repo.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "path": {"type": "string", "default": ""}}, "required": ["repo_full_name"]}},
    {"name": "get_file_content", "description": "Get file content from GitHub.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "path": {"type": "string"}}, "required": ["repo_full_name", "path"]}},
    {"name": "create_or_update_files", "description": "Commit files to a branch.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "branch_name": {"type": "string"}, "commit_message": {"type": "string"}, "file_changes": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}}, "required": ["repo_full_name", "branch_name", "commit_message", "file_changes"]}},
    {"name": "create_pull_request", "description": "Create a PR on GitHub.", "input_schema": {"type": "object", "properties": {"repo_full_name": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}, "head": {"type": "string"}, "base": {"type": "string", "default": "main"}}, "required": ["repo_full_name", "title", "body", "head"]}},
]
SAVE_TO_OBSIDIAN_TOOL = {"name": "save_to_obsidian", "description": "Save content as markdown in Obsidian vault.", "input_schema": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}
WEB_SEARCH_TOOL    = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
RESEARCH_TOOLS     = [t for t in GITHUB_TOOLS if t["name"] in ("list_repository_files", "get_file_content")]
CONTENT_TOOLS      = [SAVE_TO_OBSIDIAN_TOOL]


def alert_error(agent_name, error_msg, channel_id=None):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"],   text=f"CRITICAL | {agent_name} | {ts}\n{error_msg}")
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["REVIEW"], text=f"System Alert: {agent_name} error. Check #ops-logs.")
    except Exception as e:
        print(f"alert_error failed: {e}")


def execute_github_tool(name, input_data):
    try:
        if name == "list_repository_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            return [c.path for c in repo.get_contents(input_data.get("path", ""))]
        elif name == "get_file_content":
            repo = github_client.get_repo(input_data["repo_full_name"])
            def get_recursive(path, depth=0):
                if depth > 3: return []
                found = []
                try:
                    items = repo.get_contents(path)
                    if not isinstance(items, list):
                        return [items.path] if items.name.lower().endswith(('.py','.md','.txt','.json')) else []
                    for item in items:
                        if item.type == "file" and item.name.lower().endswith(('.py','.md','.txt','.json')):
                            found.append(item.path)
                        elif item.type == "dir":
                            found.extend(get_recursive(item.path, depth+1))
                except Exception: pass
                return found
            contents = repo.get_contents(input_data["path"])
            if isinstance(contents, list) or (hasattr(contents, "type") and contents.type == "dir"):
                return {"type": "directory", "files": get_recursive(input_data["path"])}
            return contents.decoded_content.decode("utf-8")
        elif name == "create_or_update_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            base = repo.get_branch("main")
            try: repo.get_branch(input_data["branch_name"])
            except: repo.create_git_ref(ref=f"refs/heads/{input_data['branch_name']}", sha=base.commit.sha)
            for ch in input_data["file_changes"]:
                try:
                    ex = repo.get_contents(ch["path"], ref=input_data["branch_name"])
                    repo.update_file(ch["path"], input_data["commit_message"], ch["content"], ex.sha, branch=input_data["branch_name"])
                except: repo.create_file(ch["path"], input_data["commit_message"], ch["content"], branch=input_data["branch_name"])
            return f"Committed to {input_data['branch_name']}"
        elif name == "create_pull_request":
            repo = github_client.get_repo(input_data["repo_full_name"])
            pr = repo.create_pull(title=input_data["title"], body=input_data["body"], head=input_data["head"], base=input_data.get("base","main"))
            return {"pr_url": pr.html_url, "number": pr.number}
    except Exception as e:
        return f"Error: {str(e)}"


def execute_storage_tool(name, input_data):
    try:
        if name == "save_to_obsidian":
            vault = os.environ.get("OBSIDIAN_VAULT_PATH", os.path.join(DATA_DIR, "obsidian"))
            os.makedirs(vault, exist_ok=True)
            fp = os.path.join(vault, input_data["filename"])
            with open(fp, "w") as f: f.write(input_data["content"])
            return f"Saved to {fp}"
    except Exception as e:
        return f"Storage error: {str(e)}"


# ── Memory / Profile ─────────────────────────────────────────────
class MemoryManager:
    @staticmethod
    def save(agent_name, user_msg, response, channel):
        entry = {"timestamp": datetime.now().isoformat(), "channel": channel,
                 "agent": agent_name, "user_message": user_msg, "agent_response": response}
        with open(MEMORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def get_context(agent_key, limit=20):
        if not os.path.exists(MEMORY_FILE): return ""
        memories = []
        with open(MEMORY_FILE, "r") as f:
            for line in f:
                try: memories.append(json.loads(line))
                except: continue
        agent_name = AGENTS.get(agent_key, {}).get("name", agent_key)
        relevant = [m for m in memories if m["agent"] == agent_name or m["agent"] == "Chief of Staff"]
        ctx = relevant[-limit:]
        if not ctx: return ""
        out = "\nPrevious context:\n"
        for m in ctx:
            out += f"- User: {m['user_message'][:150]}\n  {m['agent']}: {m['agent_response'][:200]}\n"
        return out


class ProfileManager:
    @staticmethod
    def load():
        if not os.path.exists(PROFILE_FILE):
            return {"style_preferences": "Short sentences, direct judgment, no AI cliches",
                    "recurring_topics": [], "decision_patterns": []}
        with open(PROFILE_FILE, "r") as f:
            try: return json.load(f)
            except: return {}

    @staticmethod
    def save(profile):
        with open(PROFILE_FILE, "w") as f:
            json.dump(profile, f, indent=4, ensure_ascii=False)

    @staticmethod
    def update_profile(count):
        if count > 0 and count % 10 == 0:
            with open(MEMORY_FILE, "r") as f: lines = f.readlines()
            recent = [json.loads(l) for l in lines[-10:]]
            curr = ProfileManager.load()
            try:
                res = claude.messages.create(
                    model="claude-sonnet-4-20250514", max_tokens=1000,
                    messages=[{"role":"user","content":
                        f"Update user profile.\nCurrent:{json.dumps(curr)}\nRecent:{json.dumps(recent)}\nReturn ONLY JSON."}])
                txt = extract_text(res.content)
                ProfileManager.save(json.loads(txt[txt.find("{"):txt.rfind("}")+1]))
            except Exception as e:
                print(f"Profile update failed: {e}")


# ── Task Manager ─────────────────────────────────────────────────
class TaskManager:
    _counter = 0
    _lock = threading.Lock()

    @staticmethod
    def _next_id():
        with TaskManager._lock:
            TaskManager._counter += 1
            return f"T-{datetime.now().strftime('%Y%m%d')}-{TaskManager._counter:03d}"

    @staticmethod
    def create(agent_key, description, source, channel_id, thread_ts=None,
               priority="P1", parent_task_id=None, handoff_to=None, handoff_prompt=None):
        task = {
            "task_id": TaskManager._next_id(), "agent_key": agent_key,
            "description": description, "status": "pending", "priority": priority,
            "source": source, "channel_id": channel_id, "thread_ts": thread_ts,
            "result_summary": None, "review_message_ts": None,
            "parent_task_id": parent_task_id, "handoff_to": handoff_to,
            "handoff_prompt": handoff_prompt, "created_at": datetime.now().isoformat(),
            "completed_at": None, "rework_count": 0,
        }
        with TaskManager._lock:
            with open(TASKS_FILE, "a") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")
        print(f"Task created: {task['task_id']} -> {agent_key}")
        return task["task_id"]

    @staticmethod
    def _load_all():
        if not os.path.exists(TASKS_FILE): return []
        tasks = []
        with open(TASKS_FILE, "r") as f:
            for line in f:
                try: tasks.append(json.loads(line))
                except: continue
        return tasks

    @staticmethod
    def _save_all(tasks):
        with TaskManager._lock:
            with open(TASKS_FILE, "w") as f:
                for t in tasks: f.write(json.dumps(t, ensure_ascii=False) + "\n")

    @staticmethod
    def update(task_id, **fields):
        tasks = TaskManager._load_all()
        for t in tasks:
            if t["task_id"] == task_id:
                t.update(fields); break
        TaskManager._save_all(tasks)

    @staticmethod
    def get(task_id):
        for t in TaskManager._load_all():
            if t["task_id"] == task_id: return t
        return None

    @staticmethod
    def get_pending(agent_key=None):
        tasks = [t for t in TaskManager._load_all() if t["status"] == "pending"]
        if agent_key: tasks = [t for t in tasks if t["agent_key"] == agent_key]
        tasks.sort(key=lambda t: t.get("priority", "P1"))
        return tasks

    @staticmethod
    def find_by_review_ts(ts):
        for t in TaskManager._load_all():
            if t.get("review_message_ts") == ts: return t
        return None


# ── Deliberation Manager ─────────────────────────────────────────
class DeliberationManager:
    """
    Manages a multi-agent deliberation session.
    Flow:
      1. CoS receives task → opens deliberation session
      2. Each relevant agent posts their take to #ops-deliberation
      3. CoS synthesizes → posts proposal to #review
      4. Ethan reacts: white_check_mark=approve, x=reject,
         speech_balloon=comment (replies in thread, agents re-deliberate)
    """
    _lock = threading.Lock()

    @staticmethod
    def _next_id():
        return f"D-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    @staticmethod
    def create(trigger_text, source_channel, thread_ts=None, ethan_comment=None):
        session = {
            "deliberation_id": DeliberationManager._next_id(),
            "trigger": trigger_text,
            "source_channel": source_channel,
            "thread_ts": thread_ts,
            "status": "deliberating",   # deliberating | pending_approval | approved | rejected | revised
            "contributions": [],         # [{agent, content, ts}]
            "synthesis": None,
            "review_message_ts": None,
            "ethan_comment": ethan_comment,
            "revision_count": 0,
            "created_at": datetime.now().isoformat(),
        }
        with DeliberationManager._lock:
            with open(DELIBERATION_FILE, "a") as f:
                f.write(json.dumps(session, ensure_ascii=False) + "\n")
        print(f"Deliberation created: {session['deliberation_id']}")
        return session["deliberation_id"]

    @staticmethod
    def _load_all():
        if not os.path.exists(DELIBERATION_FILE): return []
        sessions = []
        with open(DELIBERATION_FILE, "r") as f:
            for line in f:
                try: sessions.append(json.loads(line))
                except: continue
        return sessions

    @staticmethod
    def _save_all(sessions):
        with DeliberationManager._lock:
            with open(DELIBERATION_FILE, "w") as f:
                for s in sessions: f.write(json.dumps(s, ensure_ascii=False) + "\n")

    @staticmethod
    def update(deliberation_id, **fields):
        sessions = DeliberationManager._load_all()
        for s in sessions:
            if s["deliberation_id"] == deliberation_id:
                s.update(fields); break
        DeliberationManager._save_all(sessions)

    @staticmethod
    def get(deliberation_id):
        for s in DeliberationManager._load_all():
            if s["deliberation_id"] == deliberation_id: return s
        return None

    @staticmethod
    def find_by_review_ts(ts):
        for s in DeliberationManager._load_all():
            if s.get("review_message_ts") == ts: return s
        return None


# ── Agent Loop (unchanged) ────────────────────────────────────────
def run_agent_loop(agent_key, text):
    agent_config = AGENTS[agent_key]
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = (
        f"{agent_config['system_prompt']}\n\n"
        f"CURRENT TIME: {datetime.now().isoformat()}\n\n"
        f"USER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n{ctx}"
    )
    messages = [{"role": "user", "content": text}]
    client_tools, server_tools = [], []
    if agent_key == "DEV_LEAD":       client_tools = GITHUB_TOOLS
    elif agent_key == "RESEARCH_LEAD": client_tools = RESEARCH_TOOLS; server_tools = [WEB_SEARCH_TOOL]
    elif agent_key == "CONTENT_LEAD":  client_tools = CONTENT_TOOLS;  server_tools = [WEB_SEARCH_TOOL]
    all_tools = client_tools + server_tools
    files_read_count = 0; last_read_paths = []; max_files = 3; max_chars = 2000; repo_full_name = "unknown"

    while True:
        print(f"Claude [{agent_key}] (msgs:{len(messages)})...", flush=True)
        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192 if agent_key=="DEV_LEAD" else (2048 if agent_key!="CHIEF_OF_STAFF" else 512),
            system=sys_prompt,
            tools=all_tools if all_tools else anthropic.NOT_GIVEN,
            messages=messages, timeout=120.0
        )
        print(f"  stop_reason={res.stop_reason}", flush=True)
        if res.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": res.content})
            tool_results = []
            for block in res.content:
                if block.type == "tool_use":
                    if "repo_full_name" in block.input: repo_full_name = block.input["repo_full_name"]
                    if agent_key == "DEV_LEAD" and block.name == "get_file_content":
                        if "path" in block.input: last_read_paths.append(block.input["path"])
                        if files_read_count >= max_files:
                            result = "Limit of 3 files reached. Proceed with available info."
                        else:
                            raw = execute_github_tool(block.name, block.input)
                            result = (raw[:max_chars]+"...[truncated]") if isinstance(raw,str) and len(raw)>max_chars else raw
                            files_read_count += 1
                    else:
                        result = execute_storage_tool(block.name, block.input) if block.name=="save_to_obsidian" else execute_github_tool(block.name, block.input)
                    tool_results.append({"type":"tool_result","tool_use_id":block.id,"content":json.dumps(result)})
            messages.append({"role":"user","content":tool_results})
            if agent_key=="DEV_LEAD" and files_read_count==max_files:
                if not any("Improve this code" in str(m.get("content","")) for m in messages):
                    messages.append({"role":"user","content":"File limit reached. Provide improved code now."})
            continue
        if res.stop_reason == "pause_turn":
            messages.append({"role":"assistant","content":res.content})
            messages.append({"role":"user","content":"Continue."})
            continue
        reply = extract_text(res.content)
        # Dev Lead: auto-commit from code blocks
        if agent_key == "DEV_LEAD" and files_read_count > 0:
            code_matches = re.findall(r"```(?:\w+)?[:\s]?([\w\./-]+)?\n(.*?)\n```", reply, re.DOTALL)
            if not code_matches:
                raw_m = re.findall(r"```(?:\w+)?\n(.*?)\n```", reply, re.DOTALL)
                if raw_m: code_matches = [(last_read_paths[0] if last_read_paths else "main.py", c) for c in raw_m]
            if code_matches and repo_full_name != "unknown":
                ts = datetime.now().strftime("%Y%m%d%H%M%S"); branch = f"slackos-fix-{ts}"
                fc = [{"path": p or (last_read_paths[0] if last_read_paths else "main.py"), "content": c} for p,c in code_matches]
                if fc:
                    execute_github_tool("create_or_update_files", {"repo_full_name":repo_full_name,"branch_name":branch,"commit_message":"SlackOS improvements","file_changes":fc})
                    pr = execute_github_tool("create_pull_request", {"repo_full_name":repo_full_name,"title":"SlackOS improvements","body":"By Dev Lead.","head":branch})
                    if isinstance(pr,dict) and "pr_url" in pr: reply += f"\n\nPR: {pr['pr_url']}"
        return reply

# ── Deliberation Engine ───────────────────────────────────────────
def _pick_agents_for_task(trigger_text):
    text = trigger_text.lower()
    agents = []
    if re.search(r"github\.com/[\w-]+/[\w-]+", text) or any(k in text for k in ["code","github","develop","build","fix","bug","python","script","api","개선","기술"]):
        agents.append("DEV_LEAD")
    if any(k in text for k in ["research","analyze","market","startup","investor","fund","vc","news","분석","조사","리서치"]):
        agents.append("RESEARCH_LEAD")
    if any(k in text for k in ["write","post","linkedin","newsletter","content","draft","article","글","콘텐츠"]):
        agents.append("CONTENT_LEAD")
    if any(k in text for k in ["deck","design","slide","visual","ui","ux","wireframe","ppt","디자인","슬라이드"]):
        agents.append("DESIGN_LEAD")
    if not agents:
        agents = ["RESEARCH_LEAD", "DEV_LEAD", "CONTENT_LEAD", "DESIGN_LEAD"]
    return agents


def run_deliberation(deliberation_id):
    session = DeliberationManager.get(deliberation_id)
    if not session:
        print(f'Deliberation {deliberation_id} not found'); return

    trigger       = session['trigger']
    src_ch        = session['source_channel']
    ethan_comment = session.get('ethan_comment')
    revision      = session.get('revision_count', 0)
    cos_client    = AGENT_CLIENTS['CHIEF_OF_STAFF']

    revision_tag = f' (Revision {revision})' if revision > 0 else ''
    announce_msg = f':busts_in_silhouette: *Team Deliberation{revision_tag}* `{deliberation_id}`\n*Topic:* {trigger[:300]}'
    if ethan_comment:
        announce_msg += f'\n\n*Ethan feedback:* _{ethan_comment}_'

    ann = cos_client.chat_postMessage(channel=CHANNELS['DELIBERATION'], text=announce_msg)
    delib_ts = ann['ts']

    selected_agents = _pick_agents_for_task(trigger)
    contributions   = []
    prior_context   = ''

    for agent_key in selected_agents:
        cfg    = AGENTS[agent_key]
        client = AGENT_CLIENTS[agent_key]
        prompt = f'TEAM DELIBERATION\nTASK: {trigger}\n'
        if ethan_comment:
            prompt += f'ETHAN FEEDBACK: {ethan_comment}\n'
        if prior_context:
            prompt += f'\nPREVIOUS AGENTS SAID:\n{prior_context}\n'
        prompt += (
            f'\nYour role: {cfg["name"]}\n'
            'Give your expert analysis/recommendation. '
            'Reference other agents where relevant. Under 400 words.'
        )
        try:
            contribution = run_agent_loop(agent_key, prompt)
        except Exception as e:
            contribution = f'[Error from {cfg["name"]}]: {str(e)[:200]}'

        post_text = f'{cfg["emoji"]} *{cfg["name"]}*\n{contribution}'
        result = client.chat_postMessage(channel=CHANNELS['DELIBERATION'], text=post_text, thread_ts=delib_ts)
        contributions.append({'agent': agent_key, 'content': contribution, 'ts': result['ts']})
        prior_context += f'[{cfg["name"]}]: {contribution[:400]}\n\n'

    DeliberationManager.update(deliberation_id, contributions=contributions)
    contrib_text = '\n\n'.join(
        f'[{AGENTS[c["agent"]]["name"]}]: {c["content"]}' for c in contributions
    )
    synthesis_prompt = (
        f'You are Chief of Staff. Synthesize the team deliberation into ONE clear recommendation for Ethan.\n'
        f'ORIGINAL TASK: {trigger}\n'
    )
    if ethan_comment:
        synthesis_prompt += f'ETHAN FEEDBACK: {ethan_comment}\n'
    synthesis_prompt += (
        f'\nTEAM INPUT:\n{contrib_text}\n\n'
        'Write a synthesis with:\n'
        '1. Recommended ACTION (1-2 sentences)\n'
        '2. Key points per agent (bullet each)\n'
        '3. Open questions or risks\n'
        '4. What happens next if approved\n'
        'Be concise — Ethan reads this in 30 seconds.'
    )
    try:
        synthesis = run_agent_loop('CHIEF_OF_STAFF', synthesis_prompt)
    except Exception as e:
        synthesis = f'Synthesis failed: {str(e)[:300]}'

    DeliberationManager.update(deliberation_id, synthesis=synthesis, status='pending_approval')
    cos_client.chat_postMessage(channel=CHANNELS['DELIBERATION'], text=f':shield: *Chief of Staff — Synthesis*\n{synthesis}', thread_ts=delib_ts)

    review_text = (
        f':busts_in_silhouette: *Team Proposal* `{deliberation_id}`{revision_tag}\n'
        f'*Topic:* {trigger[:200]}\n\n'
        f'{synthesis[:1800]}\n\n'
        'React: :white_check_mark: approve  |  :x: reject  |  :speech_balloon: add comment (reply in thread)'
    )
    rv = cos_client.chat_postMessage(channel=CHANNELS['REVIEW'], text=review_text)
    DeliberationManager.update(deliberation_id, review_message_ts=rv['ts'])

    if src_ch and src_ch != CHANNELS['REVIEW']:
        cos_client.chat_postMessage(
            channel=src_ch,
            text=':busts_in_silhouette: Team deliberation complete. Proposal in #review.',
            thread_ts=session.get('thread_ts')
        )

    print(f'Deliberation {deliberation_id} done. Review ts: {rv["ts"]}')
    return rv['ts']

# ── Concurrency helpers ──────────────────────────────────────────
def _can_dispatch(agent_key):
    with _agent_inflight_lock:
        count = _agent_inflight.get(agent_key, 0)
        if count < MAX_AGENT_CONCURRENCY:
            _agent_inflight[agent_key] = count + 1
            return True
        return False

def _release_inflight(agent_key):
    with _agent_inflight_lock:
        _agent_inflight[agent_key] = max(0, _agent_inflight.get(agent_key, 1) - 1)


# ── Task-based dispatch (for scheduled / handoff jobs) ────────────
def dispatch_task(task_id):
    task = TaskManager.get(task_id)
    if not task:
        print(f'dispatch_task: {task_id} not found'); return
    agent_key   = task['agent_key']
    agent_cfg   = AGENTS[agent_key]
    agent_client = AGENT_CLIENTS[agent_key]
    TaskManager.update(task_id, status='in_progress')
    try:
        prompt = task['description']
        if task.get('rework_count', 0) > 0 and task.get('result_summary'):
            prompt = (f'REWORK (attempt {task["rework_count"]+1}):\nPrev: {task["result_summary"]}\nTask: {task["description"]}')
        reply = run_agent_loop(agent_key, prompt)
        if not reply or not reply.strip(): reply = 'Agent completed (no text).'
        TaskManager.update(task_id, status='completed', result_summary=reply[:500], completed_at=datetime.now().isoformat())
        rv = agent_client.chat_postMessage(channel=CHANNELS['REVIEW'],
            text=f'*Task {task_id}* by *{agent_cfg["name"]}*\n{reply[:1500]}\nReact: approve | reject | rework')
        TaskManager.update(task_id, review_message_ts=rv['ts'])
        if task.get('channel_id') and task['channel_id'] != CHANNELS['REVIEW']:
            agent_client.chat_postMessage(channel=task['channel_id'],
                text=f'*[{agent_cfg["name"]} v{VERSION}]*\n{reply}', thread_ts=task.get('thread_ts'))
        MemoryManager.save(agent_cfg['name'], task['description'], reply, task.get('channel_id',''))
        try:
            with open(MEMORY_FILE,'r') as f: c = sum(1 for _ in f)
            ProfileManager.update_profile(c)
        except Exception: pass
        if task.get('handoff_to') and task['handoff_to'] in AGENTS:
            child = TaskManager.create(agent_key=task['handoff_to'],
                description=task.get('handoff_prompt') or f'Continue from {task_id}: {reply[:200]}',
                source=f'handoff:{agent_key}', channel_id=task.get('channel_id', CHANNELS['REVIEW']),
                parent_task_id=task_id)
            submit_task(child)
    except Exception as e:
        TaskManager.update(task_id, status='failed', result_summary=str(e)[:500])
        alert_error(agent_key, f'Task {task_id} failed: {e}', task.get('channel_id'))
    finally:
        _release_inflight(agent_key)


def submit_task(task_id):
    task = TaskManager.get(task_id)
    if not task: return
    agent_key = task['agent_key']
    if _can_dispatch(agent_key):
        TaskManager.update(task_id, status='queued')
        executor.submit(dispatch_task, task_id)
        print(f'Submitted task {task_id}', flush=True)
    else:
        print(f'Agent {agent_key} at capacity. Task {task_id} stays pending.')


def submit_deliberation(deliberation_id):
    executor.submit(_run_deliberation_safe, deliberation_id)
    print(f'Submitted deliberation {deliberation_id}', flush=True)


def _run_deliberation_safe(deliberation_id):
    try:
        run_deliberation(deliberation_id)
    except Exception as e:
        alert_error('CHIEF_OF_STAFF', f'Deliberation {deliberation_id} failed: {e}')
        print(f'Deliberation {deliberation_id} failed: {e}')

# ── Slack App ────────────────────────────────────────────────────
app = App(token=AGENTS['CHIEF_OF_STAFF']['token'])


@app.event('message')
def handle_message(event, client, say):
    if event.get('subtype') == 'bot_message' or event.get('bot_id'):
        return
    channel_id = event['channel']
    text       = event.get('text', '')
    thread_ts  = event.get('thread_ts') or event.get('ts')
    if not text: return

    # If this is a reply in a #review thread, check if it's Ethan's comment
    if event.get('thread_ts') and channel_id == _resolve_channel_name(CHANNELS['REVIEW']):
        _handle_review_thread_reply(event)
        return

    try:
        print(f'Input: {text[:50]}...', flush=True)
        try:
            with open(os.path.join(DATA_DIR, 'routing_debug.txt'), 'a') as f:
                f.write(f'[{datetime.now().isoformat()}] CH:{channel_id} | MSG:{text[:80]}\n')
        except OSError: pass

        # Create deliberation session and notify
        delib_id = DeliberationManager.create(
            trigger_text=text,
            source_channel=channel_id,
            thread_ts=thread_ts
        )
        AGENT_CLIENTS['CHIEF_OF_STAFF'].chat_postMessage(
            channel=CHANNELS['MAIN'],
            text=f':busts_in_silhouette: Deliberation started: `{delib_id}`'
        )
        submit_deliberation(delib_id)

    except Exception as e:
        alert_error('handle_message', str(e), channel_id)
        print(f'handle_message failed: {e}')
    sys.stdout.flush()


def _resolve_channel_name(channel_name):
    """Strip # for comparison with event channel names (which may be IDs)."""
    return channel_name.lstrip('#')


def _handle_review_thread_reply(event):
    """
    Called when Ethan replies in a #review thread.
    If the parent message belongs to a deliberation, treat the reply as Ethan's comment
    and trigger a revision deliberation.
    """
    parent_ts = event.get('thread_ts')
    text      = event.get('text', '').strip()
    if not text or event.get('bot_id'): return

    session = DeliberationManager.find_by_review_ts(parent_ts)
    if not session: return
    if session.get('status') not in ('pending_approval', 'rejected'): return

    delib_id = session['deliberation_id']
    revision = session.get('revision_count', 0)
    if revision >= 3:
        AGENT_CLIENTS['CHIEF_OF_STAFF'].chat_postMessage(
            channel=CHANNELS['REVIEW'],
            text=f'Max revisions (3) reached for `{delib_id}`. Please resolve manually.',
            thread_ts=parent_ts
        )
        return

    print(f'Ethan comment on {delib_id}: {text[:100]}')
    AGENT_CLIENTS['CHIEF_OF_STAFF'].chat_postMessage(
        channel=CHANNELS['REVIEW'],
        text=f':arrows_counterclockwise: Got it. Team will revise with your feedback.',
        thread_ts=parent_ts
    )
    # Create new deliberation with Ethan's comment baked in
    new_id = DeliberationManager.create(
        trigger_text=session['trigger'],
        source_channel=session['source_channel'],
        thread_ts=session.get('thread_ts'),
        ethan_comment=text
    )
    DeliberationManager.update(new_id, revision_count=revision + 1)
    DeliberationManager.update(delib_id, status='revised')
    submit_deliberation(new_id)

@app.event('reaction_added')
def handle_reaction(event, client):
    reaction   = event.get('reaction', '')
    item       = event.get('item', {})
    message_ts = item.get('ts')
    if not message_ts: return

    # ── Check if it's a deliberation proposal ─────────────────────
    session = DeliberationManager.find_by_review_ts(message_ts)
    if session:
        delib_id   = session['deliberation_id']
        cos_client = AGENT_CLIENTS['CHIEF_OF_STAFF']
        if reaction == 'white_check_mark':
            DeliberationManager.update(delib_id, status='approved')
            cos_client.chat_postMessage(channel=CHANNELS['REVIEW'],
                text=f':white_check_mark: Proposal `{delib_id}` approved. Team is executing.',
                thread_ts=message_ts)
            # Dispatch execution tasks based on synthesis
            _execute_approved_deliberation(session)
        elif reaction == 'x':
            DeliberationManager.update(delib_id, status='rejected')
            cos_client.chat_postMessage(channel=CHANNELS['REVIEW'],
                text=f':x: Proposal `{delib_id}` rejected.',
                thread_ts=message_ts)
        elif reaction == 'speech_balloon':
            # Ethan wants to add a comment — prompt them to reply in thread
            DeliberationManager.update(delib_id, status='pending_approval')
            cos_client.chat_postMessage(channel=CHANNELS['REVIEW'],
                text=':speech_balloon: Please reply in this thread with your feedback. The team will revise.',
                thread_ts=message_ts)
        return

    # ── Fallback: legacy task-based approval ──────────────────────
    task = TaskManager.find_by_review_ts(message_ts)
    if not task: return
    task_id      = task['task_id']
    agent_client = AGENT_CLIENTS.get(task['agent_key'])
    if reaction == 'white_check_mark':
        TaskManager.update(task_id, status='approved')
        if agent_client: agent_client.chat_postMessage(channel=CHANNELS['REVIEW'], text=f'Task {task_id} approved.', thread_ts=message_ts)
    elif reaction == 'x':
        TaskManager.update(task_id, status='rejected')
        if agent_client: agent_client.chat_postMessage(channel=CHANNELS['REVIEW'], text=f'Task {task_id} rejected.', thread_ts=message_ts)
    elif reaction == 'arrows_counterclockwise':
        rework_count = task.get('rework_count', 0)
        if rework_count >= 3:
            if agent_client: agent_client.chat_postMessage(channel=CHANNELS['REVIEW'], text=f'Task {task_id} hit max rework (3).', thread_ts=message_ts)
            return
        TaskManager.update(task_id, status='pending', rework_count=rework_count+1)
        submit_task(task_id)


def _execute_approved_deliberation(session):
    """
    After Ethan approves, dispatch execution tasks for each contributing agent.
    Each agent executes the actual work (not just deliberation).
    """
    delib_id = session['deliberation_id']
    trigger  = session['trigger']
    synthesis = session.get('synthesis', trigger)
    contributions = session.get('contributions', [])

    for contrib in contributions:
        agent_key = contrib['agent']
        task_id = TaskManager.create(
            agent_key=agent_key,
            description=(
                f'APPROVED TASK (from deliberation {delib_id})\n\n'
                f'ORIGINAL REQUEST: {trigger}\n\n'
                f'TEAM SYNTHESIS: {synthesis[:500]}\n\n'
                f'YOUR CONTRIBUTION DURING DELIBERATION: {contrib["content"][:300]}\n\n'
                'Now execute the actual work. Produce concrete deliverables.'
            ),
            source=f'deliberation:{delib_id}',
            channel_id=session.get('source_channel', CHANNELS['REVIEW']),
            priority='P0'
        )
        submit_task(task_id)

# ── Channel Joining ─────────────────────────────────────────────
def join_channels():
    print('\nStarting Channel Joining...')
    channel_cache = {}

    def get_channel_id(client, name):
        if name in channel_cache: return channel_cache[name]
        try:
            clean = name.lstrip('#'); cursor = None
            while True:
                resp = client.conversations_list(types='public_channel', cursor=cursor, limit=1000)
                for ch in resp['channels']:
                    if ch['name'] == clean:
                        channel_cache[name] = ch['id']
                        for ak, data in AGENTS.items():
                            if data['channel'] == name: REVERSE_CHANNELS[ch['id']] = ak
                        return ch['id']
                cursor = resp.get('response_metadata', {}).get('next_cursor')
                if not cursor: break
        except Exception as e:
            print(f'Failed to find {name}: {e}')
        return None

    JOIN_MAP = {
        'CHIEF_OF_STAFF': [CHANNELS['MAIN'], CHANNELS['REVIEW'], CHANNELS['LOGS'],
                            CHANNELS['RESEARCH'], CHANNELS['DEV'], CHANNELS['CONTENT'],
                            CHANNELS['DESIGN'], CHANNELS['DELIBERATION']],
        'RESEARCH_LEAD':  [CHANNELS['MAIN'], CHANNELS['RESEARCH'], CHANNELS['REVIEW'],
                            CHANNELS['LOGS'], CHANNELS['DELIBERATION']],
        'DEV_LEAD':       [CHANNELS['MAIN'], CHANNELS['DEV'], CHANNELS['REVIEW'],
                            CHANNELS['LOGS'], CHANNELS['DELIBERATION']],
        'CONTENT_LEAD':   [CHANNELS['MAIN'], CHANNELS['CONTENT'], CHANNELS['REVIEW'],
                            CHANNELS['LOGS'], CHANNELS['DELIBERATION']],
        'DESIGN_LEAD':    [CHANNELS['MAIN'], CHANNELS['DESIGN'], CHANNELS['REVIEW'],
                            CHANNELS['LOGS'], CHANNELS['DELIBERATION']],
    }
    for ak, channels in JOIN_MAP.items():
        c = AGENT_CLIENTS[ak]; aname = AGENTS[ak]['name']
        for ch in channels:
            cid = get_channel_id(c, ch)
            if cid:
                try: c.conversations_join(channel=cid); print(f'{aname} joined {ch}')
                except Exception as e: print(f'{aname} failed {ch}: {e}')
            else: print(f'Could not resolve {ch}')
    print('Channel joining complete.\n')


# ── Scheduled Jobs ──────────────────────────────────────────────
def _schedule_task(agent_key, description, source, priority='P0'):
    try:
        tid = TaskManager.create(agent_key=agent_key, description=description,
            source=source, channel_id=CHANNELS['REVIEW'], priority=priority)
        submit_task(tid)
    except Exception as e:
        alert_error(agent_key, f'Scheduled {source} failed: {e}')


def _schedule_deliberation(trigger, source):
    try:
        did = DeliberationManager.create(trigger_text=trigger, source_channel=CHANNELS['REVIEW'])
        submit_deliberation(did)
    except Exception as e:
        alert_error('CHIEF_OF_STAFF', f'Scheduled deliberation {source} failed: {e}')


def daily_morning_briefing():
    print('Daily Morning Briefing...')
    _schedule_deliberation(
        'Morning briefing: summarize today\'s Korean AI startup news, key investment signals, and any urgent items for Ethan (CIO at TheVentures). Each agent should contribute their domain perspective.',
        'scheduled:daily_briefing'
    )


WEEKLY_PLANNING_PROMPT = (
    'You are Chief of Staff for Ethan\'s AI team at TheVentures. '
    'Generate a weekly task plan. Return ONLY a valid JSON array.\n'
    'Each task: {agent_key, description, priority (P0/P1/P2)}.\n'
    'Agents: RESEARCH_LEAD, DEV_LEAD, CONTENT_LEAD, DESIGN_LEAD.\n'
    'Generate 4-8 specific actionable tasks.'
)


def weekly_task_coordination():
    print('Weekly Task Coordination...')
    _schedule_deliberation(
        'Weekly planning: each agent propose their top priorities for this week. Research: deal sourcing targets. Dev: automations or infra improvements. Content: posts/newsletters. Design: any decks or visuals needed. CoS: synthesize into a weekly plan.',
        'scheduled:weekly_plan'
    )


def founder_radar():
    print('Founder Radar...')
    _schedule_task(
        'RESEARCH_LEAD',
        ('Search for recently funded Korean and Korean-diaspora founders. '
         'YC batches (last 6mo), seed/Series A. '
         'For each: name, company, description, funding, relevance to TheVentures, confidence.'),
        'scheduled:founder_radar', 'P1'
    )


def process_pending_tasks():
    pending = TaskManager.get_pending()
    if not pending: return
    print(f'Task queue: {len(pending)} pending. Processing up to 5...', flush=True)
    submitted = 0
    for task in pending:
        if submitted >= 5: break
        ak = task['agent_key']
        with _agent_inflight_lock: inf = _agent_inflight.get(ak, 0)
        if inf >= MAX_AGENT_CONCURRENCY:
            print(f'Skipping {task["task_id"]}: {ak} at capacity'); continue
        submit_task(task['task_id']); submitted += 1


scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Seoul'))
scheduler.add_job(daily_morning_briefing,  'cron', hour=7, minute=0)
scheduler.add_job(weekly_task_coordination,'cron', day_of_week='mon', hour=8, minute=0)
scheduler.add_job(founder_radar,           'interval', hours=12)
scheduler.add_job(process_pending_tasks,   'interval', minutes=5)


if __name__ == '__main__':
    join_channels()
    scheduler.start()
    print(f'SlackOS v{VERSION} is live.')
    SocketModeHandler(app, os.environ['SLACK_APP_TOKEN']).start()
