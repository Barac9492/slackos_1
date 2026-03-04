import os
import anthropic
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

app = App(token=os.environ["SLACK_BOT_TOKEN"])
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = (
    "You are Chief of Staff, an AI agent working for Ethan "
    "(CIO at TheVentures, a Seoul-based VC firm). "
    "Your job is to coordinate tasks, break them down, and report results back. "
    "Be concise and action-oriented."
)


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
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    )

    reply = response.content[0].text
    say(reply)


if __name__ == "__main__":
    print("⚡ Chief of Staff bot is running!")
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
