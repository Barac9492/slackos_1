import os
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

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
    
    # Validation: fallback to CoS if invalid key returned
    if agent_key not in AGENTS:
        print(f"⚠️ Invalid agent key '{agent_key}' returned. Falling back to CHIEF_OF_STAFF.")
        agent_key = "CHIEF_OF_STAFF"

    selected_agent = AGENTS[agent_key]
    print(f"🎯 Route: {agent_key}")

    # 2. Generate Response using the selected agent's system prompt
    response = claude.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=2048,
        system=selected_agent["system_prompt"],
        messages=[{"role": "user", "content": text}],
    )
    reply = response.content[0].text

    # 3. Post response using the specific agent's token
    try:
        client.chat_postMessage(
            token=selected_agent["token"],
            channel=event["channel"],
            text=reply,
            thread_ts=event.get("thread_ts")
        )
    except Exception as e:
        print(f"❌ Error posting as {agent_key}: {e}")
        # Fallback to posting as CoS if the other token fails
        say(f"(Fallback) {reply}")

if __name__ == "__main__":
    print("🚀 SlackOS Router (Chief of Staff) is starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()


