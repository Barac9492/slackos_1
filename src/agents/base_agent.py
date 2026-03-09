from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import anthropic
from ..config import Config

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
    
    async def generate_response(self, message: str, context: Dict[str, Any]) -> Optional[str]:
        try:
            if not self.should_respond(message, context):
                return None
                
            response = self.claude.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=2000,
                system=self.get_system_prompt(),
                messages=[{"role": "user", "content": message}]
            )
            
            return self._extract_text(response.content)
        except Exception as e:
            return f"Error generating response: {str(e)}"
    
    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        return "".join(block.text for block in content if hasattr(block, "text"))