import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import anthropic
from ..config import Config

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    def __init__(self, name: str, emoji: str, channel: str):
        self.name = name
        self.emoji = emoji
        self.channel = channel
        self.claude = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    @abstractmethod
    def get_system_prompt(self) -> str:
        pass

    @abstractmethod
    def should_respond(self, message: str, context: Dict[str, Any]) -> bool:
        pass

    def generate_response_sync(self, message: str) -> Optional[str]:
        """Synchronous response generation — called from ThreadPoolExecutor."""
        try:
            logger.info(f"[{self.name}] Calling Anthropic API...")
            response = self.claude.messages.create(
                                        model="claude-sonnet-4-6",
                max_tokens=2000,
                system=self.get_system_prompt(),
                messages=[{"role": "user", "content": message}]
            )
            result = self._extract_text(response.content)
            logger.info(f"[{self.name}] Got response ({len(result)} chars)")
            return result
        except Exception as e:
            logger.error(f"[{self.name}] Anthropic API error: {e}", exc_info=True)
            raise

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        return "".join(block.text for block in content if hasattr(block, "text"))
