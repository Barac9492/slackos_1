import os
import re
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
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
            "You have direct access to the GitHub API. "
            "\nWhen a repository URL or task is received:\n"
            "1. Extract the repository name (owner/repo) from the URL if provided.\n"
            "2. List repository files and read content of ALL Python files (*.py) for analysis.\n"
            "3. Formulate and implement code improvements directly.\n"
            "4. Create a NEW branch named 'slackos-fix-[timestamp]' (replace [timestamp] with current date-time).\n"
            "5. Commit and push the improved files.\n"
            "6. Open a Pull Request with:\n"
            "   - Title: 'SlackOS: Code improvements'\n"
            "   - Description: Detailed summary of changes made.\n"
            "7. Your final response MUST include the Pull Request link prominently.\n"
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
    }
]

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
            contents = repo.get_contents(input_data["path"])
            
            if isinstance(contents, list):
                # It's a directory
                print(f"📁 GitHub: entering directory {input_data['path']}...")
                files = []
                for item in contents:
                    if item.type == "file" and item.name.lower().endswith(('.py', '.md', '.txt', '.json')):
                        files.append(item.path)
                return {"type": "directory", "files": files}
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
            print(f"🔗 PR created: {pr.html_url}")
            return {"pr_url": pr.html_url, "number": pr.number}
            
    except Exception as e:
        print(f"❌ GitHub Error for {repo_name}: {str(e)}")
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

def log_api_call(agent_name, status="Success"):
    try:
        msg = f"API Log: {agent_name} | {status} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        # Logs are posted by Chief of Staff
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(channel=CHANNELS["LOGS"], text=msg)
    except: pass

def determine_agent(text):
    text_lower = text.lower()
    
    # 1. GitHub URL or explicit keywords -> DEV_LEAD
    if re.search(r"github\.com/[\w-]+/[\w-]+", text_lower) or \
       any(k in text_lower for k in ["code", "github", "technical", "develop", "build", "fix", "bug"]):
        return "DEV_LEAD", "Technical/GitHub task"
    
    # 2. Research Keywords -> RESEARCH_LEAD
    if any(k in text_lower for k in ["research", "analyze", "market", "startup", "investor", "fund"]):
        return "RESEARCH_LEAD", "Market/Startup research"
    
    # 3. Content Keywords -> CONTENT_LEAD
    if any(k in text_lower for k in ["write", "post", "linkedin", "newsletter", "content", "draft"]):
        return "CONTENT_LEAD", "Content/Writing task"
    
    # 4. Design Keywords -> DESIGN_LEAD
    if any(k in text_lower for k in ["deck", "design", "slide", "visual", "ui", "ux"]):
        return "DESIGN_LEAD", "Design/Visual task"
    
    # Default
    return "CHIEF_OF_STAFF", "Coordination/General task"

app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])

@app.event("message")
def handle_message(event, client, say):
    if event.get("subtype") == "bot_message" or event.get("bot_id"): return
    
    channel_id = event["channel"]
    text = event.get("text", "")
    if not text: return

    print(f"📩 Input: {text[:50]}...")

    # 1. Hard-coded Routing Decision
    agent_key, task_summary = determine_agent(text)
    
    selected_agent_config = AGENTS[agent_key]
    selected_agent_client = AGENT_CLIENTS[agent_key]
    print(f"🎯 Route: {agent_key}")

    # Chief of Staff notifies routing in #ops
    if agent_key != "CHIEF_OF_STAFF":
        AGENT_CLIENTS["CHIEF_OF_STAFF"].chat_postMessage(
            channel=CHANNELS["MAIN"], 
            text=f"🎯 Routing to {selected_agent_config['name']}: {task_summary}"
        )

    # 2. Context & Profile
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = (
        f"{selected_agent_config['system_prompt']}\n\n"
        f"CURRENT TIME: {datetime.now().isoformat()}\n\n"
        f"USER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n"
        f"{ctx}"
    )

    # 3. Execution (with Tool Support for DEV_LEAD)
    messages = [{"role": "user", "content": text}]
    tools = GITHUB_TOOLS if agent_key == "DEV_LEAD" else []
    
    # Track files read for optimization
    files_read_count = 0
    max_files = 3
    max_chars = 2000
    repo_full_name = "unknown"

    try:
        while True:
            # Set 30s timeout for Claude API call
            res = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=sys_prompt,
                tools=tools if tools else anthropic.NOT_GIVEN,
                messages=messages,
                timeout=30.0
            )
            
            if res.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": res.content})
                tool_results = []
                for content_block in res.content:
                    if content_block.type == "tool_use":
                        # Capture repo name for automation
                        if "repo_full_name" in content_block.input:
                            repo_full_name = content_block.input["repo_full_name"]

                        # Optimization: Limit and Truncate for Dev Lead
                        if agent_key == "DEV_LEAD" and content_block.name == "get_file_content":
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
                            result = execute_github_tool(content_block.name, content_block.input)
                        
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": content_block.id,
                            "content": json.dumps(result)
                        })
                messages.append({"role": "user", "content": tool_results})
                
                # Check if we should now prompt for the final improvement
                if agent_key == "DEV_LEAD" and files_read_count > 0:
                    print("🤖 Sending to Claude for improvement...")
                    # Append a specific instruction to the conversation
                    messages.append({
                        "role": "user", 
                        "content": "Improve this code. Make actual changes. Return improved code only."
                    })
                continue
            
            reply = res.content[0].text
            
            # Post-processing for DEV_LEAD GitHub Automation
            if agent_key == "DEV_LEAD" and files_read_count > 0:
                code_match = re.search(r"```(?:\w+)?\n(.*?)\n```", reply, re.DOTALL)
                if code_match:
                    improved_code = code_match.group(1)
                    if repo_full_name != "unknown":
                        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                        branch_name = f"slackos-fix-{timestamp}"
                        
                        # Automated Commit
                        execute_github_tool("create_or_update_files", {
                            "repo_full_name": repo_full_name,
                            "branch_name": branch_name,
                            "commit_message": "SlackOS: Code improvements",
                            "file_changes": [{"path": "main.py", "content": improved_code}] 
                        })
                        
                        # Automated PR
                        pr_res = execute_github_tool("create_pull_request", {
                            "repo_full_name": repo_full_name,
                            "title": "SlackOS: Code improvements",
                            "body": "Automated code improvements by SlackOS Dev Lead.",
                            "head": branch_name
                        })
                        
                        if isinstance(pr_res, dict) and "pr_url" in pr_res:
                            reply += f"\n\n🔗 PR created: {pr_res['pr_url']}"
            
            break
            
        log_api_call(selected_agent_config["name"])
    except Exception as e:
        reply = f"Error: {e}"
        log_api_call(selected_agent_config["name"], status=f"Error: {e}")

    # 4. Persistence
    MemoryManager.save(selected_agent_config["name"], text, reply, channel_id)
    try:
        with open(MEMORY_FILE, "r") as f:
            count = sum(1 for _ in f)
        ProfileManager.update_profile(count)
    except: pass

    # 5. Delivery
    target_channel = selected_agent_config["channel"]
    try:
        # Agent posts in their own workspace using their own token
        selected_agent_client.chat_postMessage(channel=target_channel, text=reply)
        
        # 6. Global Review (Only for specialists)
        if agent_key != "CHIEF_OF_STAFF":
            review_msg = f"✅ *Task Completed by {selected_agent_config['name']}*\n*Channel:* {target_channel}\n*Task:* {task_summary}\n\n*Response Summary:*\n{reply[:500]}..."
            selected_agent_client.chat_postMessage(channel=CHANNELS["REVIEW"], text=review_msg)
            
    except Exception as e:
        print(f"❌ Delivery failed for {agent_key}: {e}")

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
                        return channel["id"]
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor: break
        except Exception as e:
            print(f"â Failed to find channel {name}: {e}")
        return None

    # Define required channels per agent
    JOIN_MAP = {
        "CHIEF_OF_STAFF": [CHANNELS["MAIN"], CHANNELS["REVIEW"], CHANNELS["LOGS"]],
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

if __name__ == "__main__":
    join_channels()
    print("ð SlackOS Router is live.")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
