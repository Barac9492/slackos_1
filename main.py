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

class MemoryManager:
    @staticmethod
    def save(agent, user_msg, response, channel):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent,
            "user_message": user_msg,
            "agent_response": response,
            "channel": channel
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
                try:
                    memories.append(json.loads(line))
                except:
                    continue
        
        # Filter relevant memories (either same agent or shared context)
        # For now, we'll take the most recent ones across all agents for broader context
        relevant = memories[-limit:]
        
        context_str = "\nRelevant past interactions:\n"
        for m in relevant:
            context_str += f"- [{m['agent']}] User: {m['user_message']}\n  Response: {m['agent_response']}\n"
        return context_str

class ProfileManager:
    @staticmethod
    def load():
        if not os.path.exists(PROFILE_FILE):
            return {}
        with open(PROFILE_FILE, "r") as f:
            return json.load(f)

    @staticmethod
    def save(profile):
        with open(PROFILE_FILE, "w") as f:
            json.dump(profile, f, indent=4, ensure_ascii=False)

    @staticmethod
    def update_profile():
        # Only update every 10 messages
        if not os.path.exists(MEMORY_FILE):
            return
        
        with open(MEMORY_FILE, "r") as f:
            count = sum(1 for _ in f)
        
        if count > 0 and count % 10 == 0:
            print("🔄 Updating Ethan's profile based on recent 10 conversations...")
            memories = []
            with open(MEMORY_FILE, "r") as f:
                lines = f.readlines()
                recent_lines = lines[-10:]
                for line in recent_lines:
                    memories.append(json.loads(line))
            
            current_profile = ProfileManager.load()
            
            update_prompt = f"""
            Analyze the following 10 recent interactions and update the user's profile.
            Current profile: {json.dumps(current_profile, indent=2)}
            
            Interactions:
            {json.dumps(memories, indent=2)}
            
            Return a NEW complete JSON profile tracking:
            - communication_style
            - recurring_topics
            - decision_patterns
            - likes
            - dislikes
            """
            
            response = claude.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=1000,
                messages=[{"role": "user", "content": update_prompt}]
            )
            
            try:
                # Basic JSON extraction
                content = response.content[0].text
                new_profile = json.loads(content[content.find("{"):content.rfind("}")+1])
                ProfileManager.save(new_profile)
                print("✅ Profile updated successfully.")
            except Exception as e:
                print(f"❌ Failed to update profile: {e}")

# Agent Configuration
AGENTS = {
    "CHIEF_OF_STAFF": {
        "name": "Chief of Staff",
        "token": os.environ.get("SLACK_BOT_TOKEN"),
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
        "system_prompt": (
            "You are Dev Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle all technical tasks: writing code, building automations, managing GitHub repos, and technical architecture. "
            "You use Claude Code and implement solutions directly."
        ),
    },
    "CONTENT_LEAD": {
        "name": "Content Lead",
        "token": os.environ.get("SLACK_BOT_TOKEN_CONTENT"),
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
        "system_prompt": (
            "You are Design Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle deck structure, presentation design specs, wireframes, and visual communication. "
            "You produce clear specifications that others can implement."
        ),
    },
}

ROUTER_PROMPT = """
You are a routing engine. Analyze the user message and decide which agent should respond.
Reply with ONLY the agent key:
- RESEARCH_LEAD: market analysis, startup research, deal sourcing, LP intel
- DEV_LEAD: code, automation, github, technical questions
- CONTENT_LEAD: LinkedIn, newsletter, writing, Korean content
- DESIGN_LEAD: deck, presentation, wireframe, visual
- CHIEF_OF_STAFF: strategy, coordination, or if it's unclear
"""

# Initialize the primary app (Chief of Staff) for listening
app = App(token=AGENTS["CHIEF_OF_STAFF"]["token"])

@app.event("message")
def handle_message(event, client, say):
    # Ignore bot messages to avoid loops
    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return

    text = event.get("text", "")
    if not text:
        return

    print(f"📩 Processing message: {text[:50]}...")

    # 1. Routing Decision
    route_response = claude.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=20,
        system=ROUTER_PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    agent_key = route_response.content[0].text.strip().upper()
    
    if agent_key not in AGENTS:
        agent_key = "CHIEF_OF_STAFF"

    selected_agent = AGENTS[agent_key]
    print(f"🎯 Route: {agent_key}")

    # 2. Context and Profile Injection
    memory_context = MemoryManager.get_context(agent_key)
    ethan_profile = ProfileManager.load()
    
    full_system_prompt = f"""
    {selected_agent['system_prompt']}
    
    USER PROFILE (Ethan):
    {json.dumps(ethan_profile, indent=2)}
    
    {memory_context}
    """

    # 3. Generate Response
    response = claude.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=2048,
        system=full_system_prompt,
        messages=[{"role": "user", "content": text}],
    )
    reply = response.content[0].text

    # 4. Save to Memory
    MemoryManager.save(selected_agent["name"], text, reply, event["channel"])

    # 5. Periodic Profile Update (by Chief of Staff internally)
    ProfileManager.update_profile()

    # 6. Post response
    try:
        client.chat_postMessage(
            token=selected_agent["token"],
            channel=event["channel"],
            text=reply,
            thread_ts=event.get("thread_ts")
        )
    except Exception as e:
        print(f"❌ Error posting as {agent_key}: {e}")
        say(reply)

if __name__ == "__main__":
    print("🚀 SlackOS Router with Memory & Profile is starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()



