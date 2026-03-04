import os
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from github import Github

load_dotenv()

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
github_client = Github(os.environ.get("GITHUB_TOKEN"))

# Persistence Configuration
DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "./data")
os.makedirs(DATA_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(DATA_DIR, "memory.jsonl")
PROFILE_FILE = os.path.join(DATA_DIR, "ethan_profile.json")

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
            "You are a coordinator ONLY. You never do research, coding, writing, or design yourself. "
            "You always route to the right specialist:\n"
            "- Code, GitHub, technical → Dev Lead\n"
            "- Research, analysis, startups → Research Lead\n"
            "- Writing, LinkedIn, newsletter → Content Lead\n"
            "- Deck, design, visual → Design Lead\n"
            "You coordinate tasks, break them down, and report results back. "
            "Be concise and action-oriented."
        ),
    },
    "RESEARCH_LEAD": {
        "name": "Research Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_RESEARCH"),
        "channel": CHANNELS["RESEARCH"],
        "system_prompt": (
            "You are Research Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You specialize in deal sourcing, market analysis, startup evaluation, and LP intelligence. "
            "You track Korean AI startups, Korean diaspora founders, and investment trends. "
            "Be analytical and precise."
        ),
    },
    "DEV_LEAD": {
        "name": "Dev Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DEV"),
        "channel": CHANNELS["DEV"],
        "system_prompt": (
            "You are Dev Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle all technical tasks: writing code, building automations, managing GitHub repos, and technical architecture. "
            "You have direct access to the GitHub API. You can read repository files, make commits, and open Pull Requests. "
            "When a task involves GitHub, use your tools to perform the work directly. "
            "Always report the Pull Request link when you finish a multi-step GitHub task."
        ),
    },
    "CONTENT_LEAD": {
        "name": "Content Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_CONTENT"),
        "channel": CHANNELS["CONTENT"],
        "system_prompt": (
            "You are Content Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You write in Ethan's voice: short sentences, direct judgment, no AI clichés, "
            "newspaper editorial register in Korean (~다, ~이다), no translation-style Korean. "
            "You handle LinkedIn posts, newsletters (애당초의 미디움 레어), and LP materials."
        ),
    },
    "DESIGN_LEAD": {
        "name": "Design Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_DESIGN"),
        "channel": CHANNELS["DESIGN"],
        "system_prompt": (
            "You are Design Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle deck structure, presentation design specs, wireframes, and visual communication. "
            "You produce clear specifications that others can implement."
        ),
    },
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
    }
]

def execute_github_tool(name, input_data):
    try:
        if name == "list_repository_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            contents = repo.get_contents(input_data.get("path", ""))
            return [c.path for c in contents]
        
        elif name == "get_file_content":
            repo = github_client.get_repo(input_data["repo_full_name"])
            file_content = repo.get_contents(input_data["path"])
            return file_content.decoded_content.decode("utf-8")
        
        elif name == "create_or_update_files":
            repo = github_client.get_repo(input_data["repo_full_name"])
            base_branch = repo.get_branch("main")
            
            # Create branch if it doesn't exist
            try:
                repo.get_branch(input_data["branch_name"])
            except:
                repo.create_git_ref(ref=f"refs/heads/{input_data['branch_name']}", sha=base_branch.commit.sha)
            
            for change in input_data["file_changes"]:
                try:
                    contents = repo.get_contents(change["path"], ref=input_data["branch_name"])
                    repo.update_file(change["path"], input_data["commit_message"], change["content"], contents.sha, branch=input_data["branch_name"])
                except:
                    repo.create_file(change["path"], input_data["commit_message"], change["content"], branch=input_data["branch_name"])
            return f"Successfully committed changes to {input_data['branch_name']}"
        
        elif name == "create_pull_request":
            repo = github_client.get_repo(input_data["repo_full_name"])
            pr = repo.create_pull(title=input_data["title"], body=input_data["body"], head=input_data["head"], base=input_data.get("base", "main"))
            return {"pr_url": pr.html_url, "number": pr.number}
            
    except Exception as e:
        return f"Error executing tool {name}: {str(e)}"

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
                except: continue
        
        agent_name = AGENTS.get(agent_key, {}).get("name", agent_key)
        relevant = [m for m in memories if m["agent"] == agent_name or m["agent"] == "Chief of Staff"]
        context = relevant[-limit:]
        
        if not context: return ""
        
        ctx_str = "\nPrevious relevant context:\n"
        for m in context:
            ctx_str += f"- User: {m['user_message']}\n  {m['agent']}: {m['agent_response']}\n"
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
            except: return {}

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
                "Return the FULL updated JSON profile. Reply ONLY with JSON."
            )
            
            try:
                res = claude.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}]
                )
                txt = res.content[0].text
                new_p = json.loads(txt[txt.find("{"):txt.rfind("}")+1])
                ProfileManager.save(new_p)
                print("✅ Profile updated.")
            except Exception as e:
                print(f"❌ Profile update failed: {e}")

def log_api_call(client, agent_name, status="Success"):
    try:
        msg = f"API Log: {agent_name} | {status} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        client.chat_postMessage(channel=CHANNELS["LOGS"], text=msg)
    except: pass

ROUTER_PROMPT = """
You are a router. Analyze the user task and decide which specialist should handle it.
Reply with a JSON object: {"agent_key": "...", "task_summary": "One line summary of the task"}

Keys:
- RESEARCH_LEAD: market/startup research, analysis, deal sourcing, LP intel
- DEV_LEAD: code, technical architecture, automations, GitHub
- CONTENT_LEAD: LinkedIn posts, newsletter, writing
- DESIGN_LEAD: deck structure, wireframes, visual specs
- CHIEF_OF_STAFF: strategy, coordination, or if it's unclear
"""

app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])

@app.event("message")
def handle_message(event, client, say):
    if event.get("subtype") == "bot_message" or event.get("bot_id"): return
    
    channel_id = event["channel"]
    text = event.get("text", "")
    if not text: return

    print(f"📩 Input: {text[:50]}...")

    # 1. Routing Decision
    try:
        route_res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            system=ROUTER_PROMPT,
            messages=[{"role": "user", "content": text}]
        )
        content = route_res.content[0].text
        data = json.loads(content[content.find("{"):content.rfind("}")+1])
        agent_key = data.get("agent_key", "CHIEF_OF_STAFF").upper()
        task_summary = data.get("task_summary", "Processing request")
        if agent_key not in AGENTS: agent_key = "CHIEF_OF_STAFF"
    except:
        agent_key = "CHIEF_OF_STAFF"
        task_summary = text[:50]
    
    selected = AGENTS[agent_key]
    print(f"🎯 Route: {agent_key}")

    if agent_key != "CHIEF_OF_STAFF":
        say(f"Routing to {selected['name']}. {task_summary}")

    # 2. Context & Profile
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = f"{selected['system_prompt']}\n\nUSER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n{ctx}"

    # 3. Execution (with Tool Support for DEV_LEAD)
    messages = [{"role": "user", "content": text}]
    tools = GITHUB_TOOLS if agent_key == "DEV_LEAD" else []
    
    try:
        while True:
            res = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=sys_prompt,
                tools=tools if tools else anthropic.NOT_GIVEN,
                messages=messages
            )
            
            if res.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": res.content})
                tool_results = []
                for content_block in res.content:
                    if content_block.type == "tool_use":
                        result = execute_github_tool(content_block.name, content_block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": content_block.id,
                            "content": json.dumps(result)
                        })
                messages.append({"role": "user", "content": tool_results})
                continue
            
            reply = res.content[0].text
            break
            
        log_api_call(client, selected["name"])
    except Exception as e:
        reply = f"Error: {e}"
        log_api_call(client, selected["name"], status=f"Error: {e}")

    # 4. Persistence
    MemoryManager.save(selected["name"], text, reply, channel_id)
    try:
        with open(MEMORY_FILE, "r") as f:
            count = sum(1 for _ in f)
        ProfileManager.update_profile(count)
    except: pass

    # 5. Delivery
    target_channel = selected["channel"]
    try:
        client.chat_postMessage(token=selected["token"], channel=target_channel, text=reply)
        
        # 6. Global Review
        review_msg = f"✅ *Task Completed by {selected['name']}*\n*Channel:* {target_channel}\n*Task:* {task_summary}\n\n*Response Summary:*\n{reply[:500]}..."
        client.chat_postMessage(token=AGENTS["CHIEF_OF_STAFF"]["token"], channel=CHANNELS["REVIEW"], text=review_msg)
    except Exception as e:
        say(f"Speciaist post failed: {e}\n\n{reply}")

if __name__ == "__main__":
    print("🚀 SlackOS Router is live.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
