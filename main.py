# src/agents/base_agent.py - Abstract agent class
class BaseAgent(ABC):
    @abstractmethod
    def should_respond(self, message: str) -> bool:
        pass
    
    async def generate_response(self, message: str) -> Optional[str]:
        try:
            # Safe API call with error handling
        except Exception as e:
            return f"Error: {str(e)}"