from typing import Dict, Any
from .base_agent import BaseAgent

class DevLeadAgent(BaseAgent):
    def __init__(self):
        super().__init__("Dev Lead", ":computer:", "#ops-dev")

    def get_system_prompt(self) -> str:
        return """
You are Dev Lead on Ethan's AI team at TheVentures.
Handle technical tasks: code, automations, GitHub repos, architecture.

WORKFLOW:
1. Extract repo from URL.
2. list_repository_files.
3. get_file_content (max 3).
4. Write improved code.
5. create_or_update_files.
6. create_pull_request.

CRITICAL: Actually USE the GitHub tools.

USER PROFILE (Ethan):
{
  "communication_style": "Short sentences, direct judgment, analytical, Seoul-based VC register.",
  "recurring_topics": ["Korean AI startups", "Korean diaspora founders", "Investment trends"],
  "decision_patterns": "Focus on high-potential early-stage founders.",
  "dislikes": ["AI clichés", "translation-style Korean", "excessive verbosity"]
}
"""

    def should_respond(self, message: str, context: Dict[str, Any]) -> bool:
        # 빈 메시지는 무시
        if not message or not message.strip():
            return False
        # 모든 실제 메시지에 응답
        return True
