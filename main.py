import os
import threading
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

AGENTS_CONFIG = [
    {
        "name": "Chief of Staff",
        "token_env": "SLACK_BOT_TOKEN",
        "system_prompt": (
            "You are Chief of Staff, an AI agent working for Ethan "
            "(CIO at TheVentures, a Seoul-based VC firm). "
            "Your job is to coordinate tasks, break them down, and report results back. "
            "Be concise and action-oriented."
        ),
    },
    {
        "name": "Research Lead",
        "token_env": "SLACK_BOT_TOKEN_RESEARCH",
        "system_prompt": (
            "You are Research Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You specialize in deal sourcing, market analysis, startup evaluation, and LP intelligence. "
            "You track Korean AI startups, Korean diaspora founders, and investment trends. "
            "Be analytical and precise."
        ),
    },
    {
        "name": "Dev Lead",
        "token_env": "SLACK_BOT_TOKEN_DEV",
        "system_prompt": (
            "You are Dev Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle all technical tasks: writing code, building automations, managing GitHub repos, and technical architecture. "
            "You use Claude Code and implement solutions directly."
        ),
    },
    {
        "name": "Content Lead",
        "token_env": "SLACK_BOT_TOKEN_CONTENT",
        "system_prompt": (
            "You are Content Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You write in Ethan's voice: short sentences, direct judgment, no AI clichés, "
            "newspaper editorial register in Korean (~다, ~이다), no translation-style Korean. "
            "You handle LinkedIn posts, newsletters (애당초의 미디움 레어), and LP materials."
        ),
    },
    {
        "name": "Design Lead",
        "token_env": "SLACK_BOT_TOKEN_DESIGN",
        "system_prompt": (
            "You are Design Lead, an AI agent working for Ethan (CIO at TheVentures). "
            "You handle deck structure, presentation design specs, wireframes, and visual communication. "
            "You produce clear specifications that others can implement."
        ),
    },
]

def create_slack_app(config):
    token = os.environ.get(config["token_env"])
    if not token:
        print(f"⚠️ Warning: {config['token_env']} not found. Skipping {config['name']}.")
        return None

    app = App(token=token)
    system_prompt = config["system_prompt"]

    @app.event("message")
    def handle_message(event, say):
        # Ignore bot messages to avoid loops
        if event.get("subtype") == "bot_message" or event.get("bot_id"):
            return

        text = event.get("text", "")
        if not text:
            return

        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": text}],
        )

        reply = response.content[0].text
        say(reply)

    return app

def run_agent(app, name):
    print(f"⚡ {name} is starting...")
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.start()

if __name__ == "__main__":
    threads = []
    for config in AGENTS_CONFIG:
        app = create_slack_app(config)
        if app:
            thread = threading.Thread(target=run_agent, args=(app, config["name"]), daemon=True)
            threads.append(thread)
            thread.start()

    print(f"🚀 Started {len(threads)} agents. Keep the process running...")
    # Keep the main thread alive
    for thread in threads:
        thread.join()

