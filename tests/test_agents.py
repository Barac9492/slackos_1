import pytest
from unittest.mock import Mock, patch
from src.agents.dev_lead import DevLeadAgent

class TestDevLeadAgent:
    def setup_method(self):
        with patch('src.agents.base_agent.anthropic.Anthropic'):
            self.agent = DevLeadAgent()
    
    def test_should_respond_to_github_keywords(self):
        assert self.agent.should_respond("Check this GitHub repo", {}) is True
        assert self.agent.should_respond("Create a PR", {}) is True
        assert self.agent.should_respond("Hello world", {}) is False
    
    def test_should_respond_with_mention(self):
        assert self.agent.should_respond("Hello", {"mention_dev": True}) is True