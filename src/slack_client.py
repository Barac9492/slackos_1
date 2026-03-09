import threading
from concurrent.futures import ThreadPoolExecutor
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from .config import Config
from .agents.dev_lead import DevLeadAgent

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
            self._route_message(message, say, context)
    
    def _route_message(self, message, say, context):
        """Route messages to appropriate agents"""
        text = message.get('text', '')
        
        for agent_name, agent in self.agents.items():
            if agent.should_respond(text, context):
                self.executor.submit(self._handle_agent_response, agent, text, say, context)
    
    def _handle_agent_response(self, agent, message, say, context):
        """Handle agent response with concurrency control"""
        with self._agent_inflight_lock:
            if self._agent_inflight.get(agent.name, 0) >= Config.MAX_AGENT_CONCURRENCY:
                return
            self._agent_inflight[agent.name] = self._agent_inflight.get(agent.name, 0) + 1
        
        try:
            import asyncio
            response = asyncio.run(agent.generate_response(message, context))
            if response:
                say(f"{agent.emoji} **{agent.name}**: {response}")
        finally:
            with self._agent_inflight_lock:
                self._agent_inflight[agent.name] -= 1
    
    def start(self):
        """Start the Slack bot"""
        handler = SocketModeHandler(self.app, Config.SLACK_APP_TOKEN)
        handler.start()