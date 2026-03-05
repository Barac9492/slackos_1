import os
import sys
import re
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from github import Github
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

load_dotenv()
VERSION = "0.4.0"

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
github_client = Github(os.environ.get("GITHUB_TOKEN"))

def extract_text(content):
    """Safely extract all text from a list of Anthropic content blocks."""
    if isinstance(content, str): return content
    return "".join(block.text for block in content if hasattr(block, 'text'))

# Persistence Configuration
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(DATA_DIR, "memory.jsonl")
PROFILE_FILE = os.path.join(DATA_DIR, "ethan_profile.json")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.jsonl")

# Channel Configuration
CHANNELS = {
    "MAIN": "#ops",
    "RESEARCH": "#ops-research",
    "DEV": "#ops-dev",
    "CONTENT": "#ops-content",
    "DESIGN": "#ops-design",
    "LOGS": "#ops-logs",
    "REVIEW": "#review"
}

# Agent Configuration
AGENTS = {
    "CHIEF_OF_STAFF": {
        "name": "Chief of Staff",
        "token": os.environ.get("SLACK_BOT_TOKEN"),
        "channel": CHANNELS["MAIN"],
        "system_prompt": (
            f"[v{VERSION}] You are a ROUTER ONLY. Your only job is to say '🎯 Routing to [Agent]'. "
            "NEVER explain, NEVER plan, NEVER suggest. 1 sentence MAX."
        ),
    },
    "RESEARCH_LEAD": {
        "name": "Research Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_RESEARCH"),
        "channel": CHANNELS["RESEARCH"],
        "system_prompt": (
            "You are Research Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You specialize in deal sourcing, market analysis, startup evaluation, and LP intelligence. "
            "You track Korean AI startups, Korean diaspora founders, and investment trends.\n\n"
            "CRITICAL RULES:\n"
            "- You MUST use the web_search tool to find real, current data. NEVER fabricate or hallucinate facts.\n"
            "- Always cite sources with URLs when available.\n"
            "- Use GitHub tools to read repos when analyzing technical startups.\n"
            "- Be analytical and precise. Numbers over narratives.\n"
            "- Output format: structured briefing with clear sections and bullet points."
        ),
    },
    "DEV_LEAD": {
        "name": "Dev Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DEV"),
        "channel": CHANNELS["DEV"],
        "system_prompt": (
            "You are Dev Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle all technical tasks: writing code, building automations, managing GitHub repos, and technical architecture. "
            "You have direct access to the GitHub API.\n\n"
            "WORKFLOW — follow these steps in order:\n"
            "1. Extract the repository name (owner/repo) from the URL if provided.\n"
            "2. Use list_repository_files to explore the repo structure.\n"
            "3. Use get_file_content to read key files (max 3 files).\n"
            "4. Analyze the code and write improved versions.\n"
            "5. Use create_or_update_files to commit changes to a new branch.\n"
            "6. Use create_pull_request to open a PR.\n"
            "7. Report the PR link.\n\n"
            "CRITICAL: You MUST use the GitHub tools to commit and create PRs. Do NOT just describe what you would do — actually DO it.\n"
            "If you include code blocks in your response, format them as ```python:path/to/file.py for automatic commit."
        ),
    },
    "CONTENT_LEAD": {
        "name": "Content Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_CONTENT"),
        "channel": CHANNELS["CONTENT"],
        "system_prompt": (
            "You are Content Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle LinkedIn posts, newsletters (애당초의 미디움 레어), and LP materials.\n\n"
            "CRITICAL RULES:\n"
            "- Use web_search to research topics before writing. Ground content in real data.\n"
            "- Always save final drafts using the save_to_obsidian tool.\n"
            "- Apply GEO (Generative Engine Optimization) checklist to all content:\n"
            "  1. USE authoritative citations with real sources.\n"
            "  2. ADD clear statistics and data points.\n"
            "  3. ENSURE unique insights not found in generic AI outputs.\n"
            "  4. OPTIMIZE for AI citation using structured lists and clear headers.\n"
            "- Output: polished, publication-ready drafts. Not outlines."
        ),
    },
    "DESIGN_LEAD": {
        "name": "Design Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DESIGN"),
        "channel": CHANNELS["DESIGN"],
        "system_prompt": (
            "You are Design Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle deck structure, presentation design specs, wireframes, and visual communication.\n\n"
            "CRITICAL RULES:\n"
            "- Produce COMPLETE, actionable specs — not vague suggestions.\n"
            "- For decks: specify exact slide count, layout per slide, content per section, and visual direction.\n"
            "- For wireframes: describe layout in structured format (grid, sections, components).\n"
            "- Include color palette (hex codes), typography recommendations, and spacing guidelines.\n"
            "- Output format: structured markdown spec that a designer or tool can directly implement."
        ),
    },
}

# Reverse Channel Map for Affinity
REVERSE_CHANNELS = {}

# Initialize WebClients for each agent
AGENT_CLIENTS = {
    key: WebClient(token=data["token"]) 
    for key, data in AGENTS.items()
}

# GitHub Tools Definition
GITHUB_TOOLS = [
    {
        "name": "list_repository_files",
        "description": "List files and directories in a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_full_name": {"type": "string", "description": "The full name of the repo (e.g., 'owner/repo')."},
                "path": {"type": "string", "description": "The path within the repo.", "default": ""}
            },
            "required": ["repo_full_name"]
        }
    },
    {
        "name": "get_file_content",
        "description": "Get the content of a file from a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_full_name": {"type": "string", "description": "The full name of the repo (e.g., 'owner/repo')."},
                "path": {"type": "string", "description": "The path to the file."}
            },
            "required": ["repo_full_name", "path"]
        }
    },
    {
        "name": "create_or_update_files",
        "description": "Commit one or more file changes to a branch and push to GitHub.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_full_name": {"type": "string", "description": "The full name of the repo."},
                "branch_name": {"type": "string", "description": "The branch to commit to."},
                "commit_message": {"type": "string", "description": "The commit message."},
                "file_changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"}
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            "required": ["repo_full_name", "branch_name", "commit_message", "file_changes"]
        }
    },
    {
        "name": "create_pull_request",
        "description": "Create a Pull Request on GitHub.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_full_name": {"type": "string", "description": "The full name of the repo."},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "head": {"type": "string", "description": "The name of the branch where your changes are implemented."},
                "base": {"type": "string", "description": "The name of the branch you want the changes pulled into (e.g., 'main').", "default": "main"}
            },
            "required": ["repo_full_name", "title", "body", "head"]
        }
    },
]

# Save-to-Obsidian tool definition (used by Content Lead)
SAVE_TO_OBSIDIAN_TOOL = {
    "name": "save_to_obsidian",
    "description": "Save content as a markdown file in the Obsidian vault directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "The name of the file (e.g. 'post_v1.md')."},
            "content": {"type": "string", "description": "The markdown content to save."}
        },
        "required": ["filename", "content"]
    }
}

# Claude API server-side web search tool
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

# Read-only GitHub tools for Research Lead
RESEARCH_TOOLS = [t for t in GITHUB_TOOLS if t["name"] in ("list_repository_files", "get_file_content")]

# Content Lead tools: save_to_obsidian only (web search added server-side)
CONTENT_TOOLS = [SAVE_TO_OBSIDIAN_TOOL]

def alert_error(agent_name, error_msg, channel_id=None):
    """Logs error to #ops-logs and alerts Ethan in #review."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"‼️ *CRITICAL ERROR* | Agent: {agent_name} | Channel: {channel_id or 'Unknown'}\n*Time:* {timestamp}\n*Error:* {error_msg}"
    review_alert = f"⚠️ *System Alert:* {agent_name} encountered a critical error. Ethan, please check #ops-logs for details."
    
    try:
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"], text=log_msg)
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["REVIEW"], text=review_alert)
    except Exception as e:
        print(f"❌ Failed to post error alerts: {e}")

def execute_github_tool(name, input_data):
    repo_name = input_data.get("repo_full_name", "unknown repo")
    try:
        if name == "list_repository_files":
            print(f"🔧 GitHub: accessing {repo_name} (listing files)...")
            repo = github_client.get_repo(input_data["repo_full_name"])
            contents = repo.get_contents(input_data.get("path", ""))
            return [c.path for c in contents]
        
        elif name == "get_file_content":
            print(f"🔧 GitHub: accessing {repo_name} (reading {input_data['path']})...")
            repo = github_client.get_repo(input_data["repo_full_name"])
            
            def get_recursive_files(path, current_depth=0, max_depth=3):
                if current_depth > max_depth: return []
                found_files = []
                try:
                    contents = repo.get_contents(path)
                    if not isinstance(contents, list):
                        # Single file
                        if contents.name.lower().endswith(('.py', '.md', '.txt', '.json')):
                            return [contents.path]
                        return []
                    
                    for item in contents:
                        if item.type == "file":
                            if item.name.lower().endswith(('.py', '.md', '.txt', '.json')):
                                found_files.append(item.path)
                        elif item.type == "dir":
                            print(f"📁 GitHub: entering directory {item.path}...")
                            found_files.extend(get_recursive_files(item.path, current_depth + 1))
                except Exception:
                    pass
                return found_files

            contents = repo.get_contents(input_data["path"])
            if isinstance(contents, list) or (hasattr(contents, "type") and contents.type == "dir"):
                # It's a directory (or the call returned a list)
                all_files = get_recursive_files(input_data["path"])
                return {"type": "directory", "files": all_files}
            else:
                # It's a file
                return contents.decoded_content.decode("utf-8")
        
        elif name == "create_or_update_files":
            print(f"✅ Committing improved code to {repo_name}...")
            repo = github_client.get_repo(input_data["repo_full_name"])
            base_branch = repo.get_branch("main")
            
            # Create branch if it doesn't exist
            try:
                repo.get_branch(input_data["branch_name"])
            except Exception:
                repo.create_git_ref(ref=f"refs/heads/{input_data['branch_name']}", sha=base_branch.commit.sha)
            
            for change in input_data["file_changes"]:
                try:
                    contents = repo.get_contents(change["path"], ref=input_data["branch_name"])
                    repo.update_file(change["path"], input_data["commit_message"], change["content"], contents.sha, branch=input_data["branch_name"])
                except Exception:
                    repo.create_file(change["path"], input_data["commit_message"], change["content"], branch=input_data["branch_name"])
            return f"Successfully committed changes to {input_data['branch_name']}"
        
        elif name == "create_pull_request":
            repo = github_client.get_repo(input_data["repo_full_name"])
            pr = repo.create_pull(title=input_data["title"], body=input_data["body"], head=input_data["head"], base=input_data.get("base", "main"))
            print(f"🔗 PR created: {pr.html_url}")
            return {"pr_url": pr.html_url, "number": pr.number}
            
    except Exception as e:
        print(f"❌ GitHub Error for {repo_name}: {str(e)}")
        return f"Error executing tool {name}: {str(e)}"

def execute_storage_tool(name, input_data):
    try:
        if name == "save_to_obsidian":
            vault_path = os.environ.get("OBSIDIAN_VAULT_PATH", os.path.join(DATA_DIR, "obsidian"))
            os.makedirs(vault_path, exist_ok=True)
            file_path = os.path.join(vault_path, input_data["filename"])
            with open(file_path, "w") as f:
                f.write(input_data["content"])
            print(f"📁 Saved to Obsidian: {file_path}")
            return f"Successfully saved to {file_path}"
    except Exception as e:
        print(f"❌ Storage Error: {str(e)}")
        return f"Error executing storage tool {name}: {str(e)}"


class MemoryManager:
    @staticmethod
    def save(agent_name, user_msg, response, channel):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "channel": channel,
            "agent": agent_name,
            "user_message": user_msg,
            "agent_response": response
        }
        with open(MEMORY_FILE, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def get_context(agent_key, limit=20):
        if not os.path.exists(MEMORY_FILE):
            return ""
        
        memories = []
        with open(MEMORY_FILE, "r") as f:
            for line in f:
                try: memories.append(json.loads(line))
                except (json.JSONDecodeError, ValueError): continue
        
        agent_name = AGENTS.get(agent_key, {}).get("name", agent_key)
        relevant = [m for m in memories if m["agent"] == agent_name or m["agent"] == "Chief of Staff"]
        context = relevant[-limit:]
        
        if not context: return ""

        ctx_str = "\nPrevious relevant context:\n"
        for m in context:
            user_msg = m['user_message'][:150]
            agent_resp = m['agent_response'][:200]
            ctx_str += f"- User: {user_msg}\n  {m['agent']}: {agent_resp}\n"
        return ctx_str

class ProfileManager:
    @staticmethod
    def load():
        if not os.path.exists(PROFILE_FILE):
            return {
                "style_preferences": "Short sentences, direct judgment, no AI clichés",
                "recurring_topics": [],
                "decision_patterns": [],
                "liked_responses": [],
                "disliked_responses": []
            }
        with open(PROFILE_FILE, "r") as f:
            try: return json.load(f)
            except (json.JSONDecodeError, ValueError): return {}

    @staticmethod
    def save(profile):
        with open(PROFILE_FILE, "w") as f:
            json.dump(profile, f, indent=4, ensure_ascii=False)

    @staticmethod
    def update_profile(memories_count):
        if memories_count > 0 and memories_count % 10 == 0:
            print("🔄 Updating Ethan's profile...")
            with open(MEMORY_FILE, "r") as f:
                lines = f.readlines()
                recent = [json.loads(l) for l in lines[-10:]]
            
            curr = ProfileManager.load()
            prompt = (
                f"Analyze these 10 interactions and update the user profile.\n"
                f"Current profile: {json.dumps(curr)}\n"
                f"Recent interactions: {json.dumps(recent)}\n"
                "Focus on extracting:\n"
                "- preferred_response_style: e.g. 'Concise, result-oriented'\n"
                "- recurring_tasks: e.g. ['GitHub Improve', 'Market Research']\n"
                "- feedback_patterns: e.g. ['PR links first', 'Short explanations']\n"
                "- timezone: detection from context or default to 'KST'\n\n"
                "Return the FULL updated JSON profile including existing fields. Reply ONLY with JSON."
            )
            
            try:
                res = claude.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}]
                )
                txt = extract_text(res.content)
                new_p = json.loads(txt[txt.find("{"):txt.rfind("}")+1])
                ProfileManager.save(new_p)
                print("✅ Profile updated.")
            except Exception as e:
                print(f"❌ Profile update failed: {e}")

class TaskManager:
    _counter = 0

    @staticmethod
    def _next_id():
        TaskManager._counter += 1
        return f"T-{datetime.now().strftime('%Y%m%d')}-{TaskManager._counter:03d}"

    @staticmethod
    def create(agent_key, description, source, channel_id, thread_ts=None,
               priority="P1", parent_task_id=None, handoff_to=None, handoff_prompt=None):
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
        with open(TASKS_FILE, "a") as f:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
        print(f"📝 Task created: {task['task_id']} -> {agent_key}")
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
                except (json.JSONDecodeError, ValueError):
                    continue
        return tasks

    @staticmethod
    def _save_all(tasks):
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
        # Sort by priority (P0 first)
        pending.sort(key=lambda t: t.get("priority", "P1"))
        return pending

    @staticmethod
    def get_recent(n=10):
        tasks = TaskManager._load_all()
        return tasks[-n:]

    @staticmethod
    def find_by_review_ts(message_ts):
        for t in TaskManager._load_all():
            if t.get("review_message_ts") == message_ts:
                return t
        return None


def log_api_call(agent_name, status="Success"):
    try:
        msg = f"API Log: {agent_name} | {status} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        # Logs are posted by Chief of Staff
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"], text=msg)
    except Exception:
        pass

def determine_agent(text, channel_id=None):
    text_lower = text.lower()
    
    # STAGE 1: Channel Affinity
    if channel_id and channel_id in REVERSE_CHANNELS:
        affinity_agent = REVERSE_CHANNELS[channel_id]
        if affinity_agent != "CHIEF_OF_STAFF":
            return affinity_agent, f"Channel affinity ({affinity_agent})"

    # STAGE 2: Keyword Match (Hardened)
    # 1. GitHub/Technical -> DEV_LEAD
    if re.search(r"github\.com/[\w-]+/[\w-]+", text_lower) or \
       any(k in text_lower for k in ["code", "github", "technical", "develop", "build", "fix", "bug", "python", "script", "api", "개선"]):
        return "DEV_LEAD", "Technical/GitHub keyword"
    
    # 2. Research Keywords -> RESEARCH_LEAD
    if any(k in text_lower for k in ["research", "analyze", "market", "startup", "investor", "fund", "vc", "news", "분석", "조사"]):
        return "RESEARCH_LEAD", "Market/Startup research keyword"
    
    # 3. Content Keywords -> CONTENT_LEAD
    if any(k in text_lower for k in ["write", "post", "linkedin", "newsletter", "content", "draft", "article"]):
        return "CONTENT_LEAD", "Content/Writing keyword"
    
    # 4. Design Keywords -> DESIGN_LEAD
    if any(k in text_lower for k in ["deck", "design", "slide", "visual", "ui", "ux", "wireframe", "ppt"]):
        return "DESIGN_LEAD", "Design/Visual keyword"
    
    # STAGE 3: LLM Guard (if Stage 2 defaults to CoS)
    print("🧠 Routing Guard: Consulting Claude for decision...")
    prompt = (
        "Decide which specialist agent should handle this message.\n"
        "Options: DEV_LEAD, RESEARCH_LEAD, CONTENT_LEAD, DESIGN_LEAD, CHIEF_OF_STAFF\n"
        "- DEV_LEAD: Technical, code, GitHub, automations\n"
        "- RESEARCH_LEAD: Market research, startup analysis, scouting\n"
        "- CONTENT_LEAD: Writing, LinkedIn, newsletters\n"
        "- DESIGN_LEAD: Decks, UI/UX, visuals\n"
        "- CHIEF_OF_STAFF: Only if it's general greeting or pure management\n\n"
        f"Message: {text}\n\n"
        "Return ONLY the agent key (e.g. DEV_LEAD)."
    )
    try:
        res = claude.messages.create(
            model="claude-3-haiku-20240307", # Use fast model for routing
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )
        decision = extract_text(res.content).strip().upper()
        if decision in AGENTS:
            return decision, f"LLM Guard decision ({decision})"
    except Exception as e:
        print(f"❌ Routing Guard failed: {e}")
    
    # Default
    return "CHIEF_OF_STAFF", "Default to Coordination"

def run_agent_loop(agent_key, text):
    """Execute an agent's Claude loop with tools. Returns the final reply text."""
    agent_config = AGENTS[agent_key]

    # Context & Profile
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = (
        f"{agent_config['system_prompt']}\n\n"
        f"CURRENT TIME: {datetime.now().isoformat()}\n\n"
        f"USER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n"
        f"{ctx}"
    )

    # Tool selection
    messages = [{"role": "user", "content": text}]
    client_tools = []
    server_tools = []
    if agent_key == "DEV_LEAD":
        client_tools = GITHUB_TOOLS
    elif agent_key == "RESEARCH_LEAD":
        client_tools = RESEARCH_TOOLS
        server_tools = [WEB_SEARCH_TOOL]
    elif agent_key == "CONTENT_LEAD":
        client_tools = CONTENT_TOOLS
        server_tools = [WEB_SEARCH_TOOL]

    all_tools = client_tools + server_tools

    # Dev Lead file tracking
    files_read_count = 0
    last_read_paths = []
    max_files = 3
    max_chars = 2000
    repo_full_name = "unknown"

    while True:
        print(f"🔄 Calling Claude API for {agent_key} (msgs: {len(messages)})...", flush=True)
        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192 if agent_key == "DEV_LEAD" else (2048 if agent_key != "CHIEF_OF_STAFF" else 100),
            system=sys_prompt,
            tools=all_tools if all_tools else anthropic.NOT_GIVEN,
            messages=messages,
            timeout=120.0
        )
        print(f"✅ Claude responded: stop_reason={res.stop_reason}", flush=True)

        if res.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": res.content})
            tool_results = []
            for content_block in res.content:
                if content_block.type == "tool_use":
                    if "repo_full_name" in content_block.input:
                        repo_full_name = content_block.input["repo_full_name"]

                    if agent_key == "DEV_LEAD" and content_block.name == "get_file_content":
                        if "path" in content_block.input:
                            last_read_paths.append(content_block.input["path"])
                        if files_read_count >= max_files:
                            result = "Limit of 3 files reached. Please proceed with available info."
                        else:
                            raw_result = execute_github_tool(content_block.name, content_block.input)
                            if isinstance(raw_result, str):
                                result = raw_result[:max_chars] + ("... [truncated]" if len(raw_result) > max_chars else "")
                                files_read_count += 1
                            else:
                                result = raw_result
                    else:
                        if content_block.name == "save_to_obsidian":
                            result = execute_storage_tool(content_block.name, content_block.input)
                        else:
                            result = execute_github_tool(content_block.name, content_block.input)

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": content_block.id,
                        "content": json.dumps(result)
                    })
            messages.append({"role": "user", "content": tool_results})

            if agent_key == "DEV_LEAD" and files_read_count == max_files:
                if not any("Improve this code" in str(m.get("content", "")) for m in messages):
                    print("🤖 File limit reached. Prompting for improvement...")
                    messages.append({
                        "role": "user",
                        "content": "Limit of 3 files reached. Based on the files you've read, please provide the improved code now. Formatting: ```python:path/to/file.py\n[code]\n```"
                    })
            continue

        if res.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": res.content})
            messages.append({"role": "user", "content": "Continue."})
            continue

        reply = extract_text(res.content)

        # Dev Lead: auto-commit + PR from code blocks
        if agent_key == "DEV_LEAD" and files_read_count > 0:
            code_matches = re.findall(r"```(?:\w+)?[:\s]?([\w\./-]+)?\n(.*?)\n```", reply, re.DOTALL)
            if not code_matches:
                code_matches = re.findall(r"```(?:\w+)?\n(.*?)\n```", reply, re.DOTALL)
                if code_matches:
                    code_matches = [(last_read_paths[0] if last_read_paths else "main.py", c) for c in code_matches]

            if code_matches and repo_full_name != "unknown":
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                branch_name = f"slackos-fix-{timestamp}"
                file_changes = []
                for path_hint, improved_code in code_matches:
                    target_path = path_hint or (last_read_paths[0] if last_read_paths else "main.py")
                    file_changes.append({"path": target_path, "content": improved_code})

                if file_changes:
                    execute_github_tool("create_or_update_files", {
                        "repo_full_name": repo_full_name,
                        "branch_name": branch_name,
                        "commit_message": "SlackOS: Code improvements",
                        "file_changes": file_changes
                    })
                    pr_res = execute_github_tool("create_pull_request", {
                        "repo_full_name": repo_full_name,
                        "title": "SlackOS: Code improvements",
                        "body": "Automated code improvements by SlackOS Dev Lead.",
                        "head": branch_name
                    })
                    if isinstance(pr_res, dict) and "pr_url" in pr_res:
                        reply += f"\n\n🔗 PR created: {pr_res['pr_url']}"

        return reply


def dispatch_task(task_id):
    """Execute a tracked task: run agent loop, post to #review for approval."""
    task = TaskManager.get(task_id)
    if not task:
        print(f"❌ dispatch_task: task {task_id} not found")
        return

    agent_key = task["agent_key"]
    agent_config = AGENTS[agent_key]
    agent_client = AGENT_CLIENTS[agent_key]

    TaskManager.update(task_id, status="in_progress")
    print(f"🚀 Dispatching {task_id} to {agent_key}...")

    try:
        # Build prompt — include rework context if re-dispatched
        prompt = task["description"]
        if task.get("rework_count", 0) > 0 and task.get("result_summary"):
            prompt = (
                f"REWORK REQUEST (attempt {task['rework_count'] + 1}):\n"
                f"Previous result was not approved. Please improve.\n"
                f"Previous output summary: {task['result_summary']}\n\n"
                f"Original task: {task['description']}"
            )

        reply = run_agent_loop(agent_key, prompt)

        if not reply or not reply.strip():
            reply = "✅ Agent action completed (no text response)."

        TaskManager.update(task_id, status="completed",
                          result_summary=reply[:500],
                          completed_at=datetime.now().isoformat())

        log_api_call(agent_config["name"])

        # Post to #review for approval
        review_msg = (
            f"📋 *Task {task_id}* by *{agent_config['name']}*\n"
            f"*Priority:* {task.get('priority', 'P1')} | *Source:* {task.get('source', 'unknown')}\n\n"
            f"{reply[:1500]}\n\n"
            f"React: ✅ approve | ❌ reject | 🔄 rework"
        )
        result = agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=review_msg)
        TaskManager.update(task_id, review_message_ts=result["ts"])

        # Post to originating channel if specified and different from #review
        if task.get("channel_id") and task["channel_id"] != CHANNELS["REVIEW"]:
            tagged_reply = f"*[Agent: {agent_config['name']} v{VERSION}]*\n{reply}"
            agent_client.chat_postMessage(
                channel=task["channel_id"], text=tagged_reply,
                thread_ts=task.get("thread_ts")
            )

        # Memory persistence
        MemoryManager.save(agent_config["name"], task["description"], reply, task.get("channel_id", ""))

        # Handle handoff if specified
        if task.get("handoff_to") and task["handoff_to"] in AGENTS:
            child_id = TaskManager.create(
                agent_key=task["handoff_to"],
                description=task.get("handoff_prompt") or f"Continue from {task_id}: {reply[:200]}",
                source=f"handoff:{agent_key}",
                channel_id=task.get("channel_id", CHANNELS["REVIEW"]),
                parent_task_id=task_id
            )
            dispatch_task(child_id)

    except Exception as e:
        TaskManager.update(task_id, status="failed", result_summary=str(e)[:500])
        alert_error(agent_key, f"Task {task_id} failed: {e}", task.get("channel_id"))
        print(f"❌ dispatch_task failed for {task_id}: {e}")


app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])

@app.event("message")
def handle_message(event, client, say):
    if event.get("subtype") == "bot_message" or event.get("bot_id"): return

    channel_id = event["channel"]
    text = event.get("text", "")
    thread_ts = event.get("thread_ts") or event.get("ts")
    if not text: return

    try:
        print(f"📩 Input: {text[:50]}...")

        agent_key, task_summary = determine_agent(text, channel_id)

        # Debug logging
        try:
            with open(os.path.join(DATA_DIR, "routing_debug.txt"), "a") as f:
                f.write(f"[{datetime.now().isoformat()}] CH:{channel_id} | AGENT:{agent_key} | MSG:{text[:50]}...\n")
        except OSError:
            pass

        selected_agent_config = AGENTS[agent_key]
        selected_agent_client = AGENT_CLIENTS[agent_key]
        print(f"🎯 Route: {agent_key}")

        # Chief of Staff notifies routing
        if agent_key != "CHIEF_OF_STAFF":
            AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(
                channel=CHANNELS["MAIN"],
                text=f"🎯 Routing to {selected_agent_config['name']}: {task_summary}"
            )

        # Create tracked task
        task_id = TaskManager.create(
            agent_key=agent_key, description=text,
            source=f"user:{event.get('user', 'unknown')}",
            channel_id=channel_id, thread_ts=thread_ts
        )

        # Execute
        reply = run_agent_loop(agent_key, text)

        if not reply or not reply.strip():
            reply = "✅ Agent action completed (no text response)."

        # Update task
        TaskManager.update(task_id, status="completed",
                          result_summary=reply[:500],
                          completed_at=datetime.now().isoformat())

        print(f"📤 Reply ready ({len(reply)} chars). Posting...", flush=True)
        log_api_call(selected_agent_config["name"])

        # Persistence
        MemoryManager.save(selected_agent_config["name"], text, reply, channel_id)
        try:
            with open(MEMORY_FILE, "r") as f:
                count = sum(1 for _ in f)
            ProfileManager.update_profile(count)
        except Exception:
            pass

        # Delivery
        tagged_reply = f"*[Agent: {selected_agent_config['name']} v{VERSION}]*\n{reply}"

        selected_agent_client.chat_postMessage(
            channel=channel_id, text=tagged_reply, thread_ts=thread_ts
        )
        print(f"✅ Posted reply to originating channel {channel_id}", flush=True)

        # Log to agent workspace if different channel
        agent_channel = selected_agent_config["channel"]
        if channel_id != agent_channel:
            try:
                selected_agent_client.chat_postMessage(
                    channel=agent_channel,
                    text=f"📋 *Task from <#{channel_id}>:*\n_{text[:100]}_\n\n{tagged_reply}"
                )
            except Exception:
                pass

        # Post to #review for approval (specialists only)
        if agent_key != "CHIEF_OF_STAFF":
            review_msg = (
                f"📋 *Task {task_id}* by *{selected_agent_config['name']}*\n"
                f"*Channel:* <#{channel_id}> | *Source:* user message\n\n"
                f"{reply[:1000]}\n\n"
                f"React: ✅ approve | ❌ reject | 🔄 rework"
            )
            result = selected_agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=review_msg)
            TaskManager.update(task_id, review_message_ts=result["ts"])

    except Exception as e:
        alert_error(agent_key if 'agent_key' in locals() else "Unknown", str(e), channel_id)
        print(f"❌ handle_message failed: {e}")
        sys.stdout.flush()


@app.event("reaction_added")
def handle_reaction(event, client):
    """Handle approval/rejection via Slack reactions on #review messages."""
    reaction = event.get("reaction", "")
    item = event.get("item", {})
    message_ts = item.get("ts")
    channel = item.get("channel")

    if not message_ts:
        return

    task = TaskManager.find_by_review_ts(message_ts)
    if not task:
        return

    task_id = task["task_id"]
    agent_config = AGENTS.get(task["agent_key"], {})
    agent_client = AGENT_CLIENTS.get(task["agent_key"])

    if reaction == "white_check_mark":  # ✅
        TaskManager.update(task_id, status="approved")
        print(f"✅ Task {task_id} approved")
        if agent_client:
            agent_client.chat_postMessage(
                channel=CHANNELS["REVIEW"],
                text=f"✅ *Task {task_id}* approved by Ethan.",
                thread_ts=message_ts
            )

    elif reaction == "x":  # ❌
        TaskManager.update(task_id, status="rejected")
        print(f"❌ Task {task_id} rejected")
        if agent_client:
            agent_client.chat_postMessage(
                channel=CHANNELS["REVIEW"],
                text=f"❌ *Task {task_id}* rejected.",
                thread_ts=message_ts
            )

    elif reaction == "arrows_counterclockwise":  # 🔄
        rework_count = task.get("rework_count", 0)
        if rework_count >= 3:
            print(f"⚠️ Task {task_id} hit max rework limit (3)")
            if agent_client:
                agent_client.chat_postMessage(
                    channel=CHANNELS["REVIEW"],
                    text=f"⚠️ *Task {task_id}* has reached the maximum rework limit (3 attempts). Manual intervention needed.",
                    thread_ts=message_ts
                )
            return

        TaskManager.update(task_id, status="pending",
                          rework_count=rework_count + 1)
        print(f"🔄 Task {task_id} queued for rework (attempt {rework_count + 2})")
        if agent_client:
            agent_client.chat_postMessage(
                channel=CHANNELS["REVIEW"],
                text=f"🔄 *Task {task_id}* queued for rework (attempt {rework_count + 2}/4).",
                thread_ts=message_ts
            )
        # Dispatch rework
        dispatch_task(task_id)

def join_channels():
    print("\nâï¸ Starting Automated Channel Joining...")
    
    # Simple name-to-ID cache for the session
    channel_cache = {}

    def get_channel_id(client, name):
        if name in channel_cache: return channel_cache[name]
        try:
            # Note: conversations_list requires public_channels scope
            clean_name = name.lstrip("#")
            cursor = None
            while True:
                response = client.conversations_list(types="public_channel", cursor=cursor, limit=1000)
                for channel in response["channels"]:
                    if channel["name"] == clean_name:
                        channel_cache[name] = channel["id"]
                        # Populate Reverse Map
                        for agent_key, data in AGENTS.items():
                            if data["channel"] == name:
                                REVERSE_CHANNELS[channel["id"]] = agent_key
                        return channel["id"]
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor: break
        except Exception as e:
            print(f"â Failed to find channel {name}: {e}")
        return None

    # Define required channels per agent
    JOIN_MAP = {
        "CHIEF_OF_STAFF": [CHANNELS["MAIN"], CHANNELS["REVIEW"], CHANNELS["LOGS"],
                           CHANNELS["RESEARCH"], CHANNELS["DEV"], CHANNELS["CONTENT"], CHANNELS["DESIGN"]],
        "RESEARCH_LEAD": [CHANNELS["RESEARCH"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "DEV_LEAD": [CHANNELS["DEV"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "CONTENT_LEAD": [CHANNELS["CONTENT"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
        "DESIGN_LEAD": [CHANNELS["DESIGN"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
    }

    for agent_key, channels in JOIN_MAP.items():
        client = AGENT_CLIENTS[agent_key]
        agent_name = AGENTS[agent_key]["name"]
        
        for ch_name in channels:
            ch_id = get_channel_id(client, ch_name)
            if ch_id:
                try:
                    client.conversations_join(channel=ch_id)
                    print(f"â {agent_name} joined {ch_name}")
                except Exception as e:
                    print(f"â {agent_name} failed to join {ch_name}: {e}")
            else:
                print(f"â ï¸ Could not resolve ID for {ch_name}")
    print("â Startup joining routine complete.\n")

def daily_morning_briefing():
    """Create and dispatch a tracked task for the morning briefing."""
    print("⏰ Executing Daily Morning Briefing (Research Lead)...")
    try:
        task_id = TaskManager.create(
            agent_key="RESEARCH_LEAD",
            description="Ethan의 CIO 역할을 돕기 위해 오늘 아침 한국 AI 스타트업 동향과 주요 뉴스를 요약해서 브리핑해줘. 결과는 #review 채널에 어울리는 형식으로.",
            source="scheduled:daily_briefing",
            channel_id=CHANNELS["REVIEW"],
            priority="P0"
        )
        dispatch_task(task_id)
    except Exception as e:
        alert_error("RESEARCH_LEAD", f"Daily Briefing failed: {e}")

WEEKLY_PLANNING_PROMPT = (
    "You are the Chief of Staff for Ethan's AI team at TheVentures. "
    "Generate a weekly task plan for the team. Assign concrete, actionable tasks to each agent:\n"
    "- RESEARCH_LEAD: deal sourcing, market scans, LP intelligence\n"
    "- DEV_LEAD: GitHub automations, code improvements, infra\n"
    "- CONTENT_LEAD: LinkedIn posts, newsletter drafts, GEO optimization\n"
    "- DESIGN_LEAD: deck specs, wireframes, visual assets\n\n"
    "IMPORTANT: You MUST return ONLY a valid JSON array. No markdown, no explanation.\n"
    "Each task object must have: agent_key, description, priority (P0/P1/P2).\n"
    "Example:\n"
    '[{"agent_key": "RESEARCH_LEAD", "description": "Scan Korean AI startups that raised this week", "priority": "P0"},'
    ' {"agent_key": "DEV_LEAD", "description": "Review and improve SlackOS error handling", "priority": "P1"}]\n\n'
    "Generate 4-8 tasks total. Be specific — reference real recurring workflows."
)

def weekly_task_coordination():
    """Generate weekly plan as structured tasks and dispatch them."""
    print("⏰ Executing Weekly Task Coordination (Chief of Staff)...")
    try:
        client = AGENT_CLIENTS["CHIEF_OF_STAFF"]

        prompt = "이번 주 Ethan을 위해 AI 팀 주간 태스크를 JSON 배열로 생성해줘. 각 에이전트에게 구체적이고 실행 가능한 태스크를 배분해."

        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=WEEKLY_PLANNING_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )

        reply_text = extract_text(res.content)

        # Parse JSON array from response
        tasks_dispatched = []
        try:
            # Extract JSON array (handle potential markdown wrapping)
            json_str = reply_text
            if "```" in json_str:
                json_str = re.search(r'\[.*\]', json_str, re.DOTALL)
                json_str = json_str.group(0) if json_str else reply_text
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

            # Dispatch all created tasks
            for td in tasks_dispatched:
                try:
                    dispatch_task(td["task_id"])
                except Exception as dispatch_err:
                    print(f"❌ Failed to dispatch {td['task_id']}: {dispatch_err}")

        except (json.JSONDecodeError, ValueError) as parse_err:
            print(f"⚠️ Weekly plan JSON parse failed: {parse_err}. Posting as text.")
            tasks_dispatched = []

        # Summary to #review
        if tasks_dispatched:
            summary_lines = [f"- *{td['agent']}*: {td['desc']} (`{td['task_id']}`)" for td in tasks_dispatched]
            summary = "\n".join(summary_lines)
            client.chat_postMessage(
                channel=CHANNELS["REVIEW"],
                text=f"📅 [v{VERSION}] *Weekly Team Coordination*\n\n{len(tasks_dispatched)} tasks dispatched:\n{summary}"
            )
        else:
            client.chat_postMessage(
                channel=CHANNELS["REVIEW"],
                text=f"📅 [v{VERSION}] *Weekly Team Coordination*\n\n{reply_text}"
            )

    except Exception as e:
        alert_error("CHIEF_OF_STAFF", f"Weekly Coordination failed: {e}")

def founder_radar():
    """Create and dispatch a tracked task for founder radar scanning."""
    print("⏰ Executing Founder Radar (Research Lead)...")
    try:
        task_id = TaskManager.create(
            agent_key="RESEARCH_LEAD",
            description=(
                "Search for recently funded Korean and Korean-diaspora founders. Focus on:\n"
                "1. Recent Y Combinator batches (last 6 months)\n"
                "2. Notable Korean-founded startups that raised seed or Series A\n"
                "3. Korean AI/tech founders in the US, Europe, or Southeast Asia\n\n"
                "For each founder found, provide:\n"
                "- Name, company, one-line description\n"
                "- Funding stage and amount (if available)\n"
                "- Why they're relevant to TheVentures\n"
                "- Confidence score (High/Medium/Low) based on data quality\n\n"
                "Format as a structured briefing. Only include founders with real, verifiable data."
            ),
            source="scheduled:founder_radar",
            channel_id=CHANNELS["REVIEW"],
            priority="P1"
        )
        dispatch_task(task_id)
    except Exception as e:
        alert_error("RESEARCH_LEAD", f"Founder Radar failed: {e}")

def process_pending_tasks():
    """Process up to 3 pending tasks per cycle. Runs every 30 minutes."""
    pending = TaskManager.get_pending()
    if not pending:
        return
    print(f"⏰ Task queue: {len(pending)} pending tasks. Processing up to 3...")
    for task in pending[:3]:
        try:
            dispatch_task(task["task_id"])
        except Exception as e:
            print(f"❌ Task queue failed for {task['task_id']}: {e}")


scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Seoul'))
scheduler.add_job(daily_morning_briefing, 'cron', hour=7, minute=0)
scheduler.add_job(weekly_task_coordination, 'cron', day_of_week='mon', hour=8, minute=0)
scheduler.add_job(founder_radar, 'interval', hours=12)
scheduler.add_job(process_pending_tasks, 'interval', minutes=30)

if __name__ == "__main__":
    join_channels()
    scheduler.start()
    print("ð SlackOS Router is live.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
