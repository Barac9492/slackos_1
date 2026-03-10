import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from .config import Config
from .agents.dev_lead import DevLeadAgent

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class SlackClient:
    def __init__(self):
        Config.validate()

        self.app = App(token=Config.SLACK_BOT_TOKEN)
        self.executor = ThreadPoolExecutor(max_workers=Config.THREAD_POOL_WORKERS)
        self._agent_inflight = {}
        self._agent_inflight_lock = threading.Lock()

        # Initialize agents
        self.agents = {
            'dev_lead': DevLeadAgent(),
        }

        self._setup_handlers()

    def _setup_handlers(self):
        @self.app.message("")
        def handle_message(message, say, context):
            text = message.get('text', '')
            user = message.get('user', 'unknown')
            subtype = message.get('subtype', None)
            bot_id = message.get('bot_id', None)

            logger.info(f"[MSG] user={user} subtype={subtype} bot_id={bot_id} text={text[:80]!r}")

            # bot 자신의 메시지는 무시
            if bot_id or subtype == 'bot_message':
                logger.info("[MSG] Skipping bot message")
                return

            self._route_message(message, say, context)

        @self.app.event("reaction_added")
        def handle_reaction_added(event, say, client):
            reaction = event.get("reaction", "")
            user = event.get("user", "")
            item = event.get("item", {})
            channel = item.get("channel", "")
            ts = item.get("ts", "")
            logger.info(f"[REACTION] user={user} reaction={reaction} channel={channel} ts={ts}")

    def _route_message(self, message, say, context):
        """Route messages to appropriate agents"""
        text = message.get('text', '')

        routed = False
        for agent_name, agent in self.agents.items():
            should = agent.should_respond(text, context)
            logger.info(f"[ROUTE] agent={agent_name} should_respond={should} text={text[:60]!r}")
            if should:
                routed = True
                self.executor.submit(self._handle_agent_response, agent, message, say)

        if not routed:
            logger.warning(f"[ROUTE] No agent matched for text={text[:80]!r}")

    def _handle_agent_response(self, agent, message, say):
        """Handle agent response with concurrency control"""
        text = message.get('text', '')
        channel = message.get('channel', '')
        user = message.get('user', '')

        with self._agent_inflight_lock:
            if self._agent_inflight.get(agent.name, 0) >= Config.MAX_AGENT_CONCURRENCY:
                logger.warning(f"[AGENT] {agent.name} at max concurrency, dropping message")
                return
            self._agent_inflight[agent.name] = self._agent_inflight.get(agent.name, 0) + 1

        try:
            logger.info(f"[AGENT] {agent.name} generating response for user={user} text={text[:60]!r}")
            response = agent.generate_response_sync(text)
            if response:
                logger.info(f"[AGENT] {agent.name} responding: {response[:80]!r}")
                say(text=f"{agent.emoji} *{agent.name}*: {response}")
            else:
                logger.warning(f"[AGENT] {agent.name} returned empty response")
        except Exception as e:
            logger.error(f"[AGENT] {agent.name} error: {e}", exc_info=True)
            say(text=f"{agent.emoji} *{agent.name}*: 오류가 발생했습니다: {e}")
        finally:
            with self._agent_inflight_lock:
                self._agent_inflight[agent.name] -= 1

    def start(self):
        """Start the Slack bot"""
        logger.info("Starting SocketModeHandler...")
        handler = SocketModeHandler(self.app, Config.SLACK_APP_TOKEN)
        handler.start()
