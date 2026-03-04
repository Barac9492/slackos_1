import os
import json
import anthropic
from datetime import datetime
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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
            "You are Chief of Staff, an AI agent working for Ethan "
            "(CIO at TheVentures, a Seoul-based VC firm). "
            "Your job is to coordinate tasks, break them down, and report results back. "
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
            "You use Claude Code and implement solutions directly."
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
        # Filter for relevant memories (same agent or CoS)
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
                "Return the FULL updated JSON profile including style_preferences, recurring_topics, "
                "decision_patterns, liked_responses, and disliked_responses. Reply ONLY with JSON."
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
Decide which agent key should respond. Reply with ONLY the key:
- RESEARCH_LEAD: market/startup research, LP intel
- DEV_LEAD: code, technical architecture, automations
- CONTENT_LEAD: LinkedIn posts, newsletter, writing
- DESIGN_LEAD: deck structure, wireframes, visual specs
- CHIEF_OF_STAFF: strategy, coordination, or unclear
"""

app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])

@app.event("message")
def handle_message(event, client, say):
    if event.get("subtype") == "bot_message" or event.get("bot_id"): return
    
    # We assume messages are coming into a channel the bot is in, primarily #ops
    channel_id = event["channel"]
    text = event.get("text", "")
    if not text: return

    print(f"📩 Input: {text[:50]}...")

    # 1. Routing
    try:
        route_res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=20,
            system=ROUTER_PROMPT,
            messages=[{"role": "user", "content": text}]
        )
        agent_key = route_res.content[0].text.strip().upper().rstrip(".")
        if agent_key not in AGENTS: agent_key = "CHIEF_OF_STAFF"
    except:
        agent_key = "CHIEF_OF_STAFF"
    
    selected = AGENTS[agent_key]
    print(f"🎯 Route: {agent_key}")

    # 2. Context & Profile
    ctx = MemoryManager.get_context(agent_key)
    profile = ProfileManager.load()
    sys_prompt = (
        f"{selected['system_prompt']}\n\n"
        f"USER PROFILE (Ethan):\n{json.dumps(profile, indent=2)}\n"
        f"{ctx}"
    )

    # 3. Execution
    try:
        res = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=sys_prompt,
            messages=[{"role": "user", "content": text}]
        )
        reply = res.content[0].text
        log_api_call(client, selected["name"])
    except Exception as e:
        reply = f"Error generating response: {e}"
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
        # Post to the routed agent's channel using their token
        client.chat_postMessage(token=selected["token"], channel=target_channel, text=reply)
        
        # 6. Summary Review for CHIEF_OF_STAFF reviews
        if agent_key != "CHIEF_OF_STAFF":
            summary = (
                f"*New Workspace Activity*\n"
                f"*Agent:* {selected['name']}\n"
                f"*Task:* {text[:200]}...\n"
                f"*Link:* [Posted in {target_channel}]"
            )
            client.chat_postMessage(token=AGENTS["CHIEF_OF_STAFF"]["token"], channel=CHANNELS["REVIEW"], text=summary)
            
    except Exception as e:
        print(f"❌ Delivery failed: {e}")
        # Fallback to the original channel if specialized post fails
        say(f"Notification: Response posted to {target_channel} (or failed: {e})\n\n{reply}")

if __name__ == "__main__":
    print("🚀 SlackOS Router is starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
