import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # API Keys
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
    SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
    
    # App Settings
    VERSION = "0.7.0"
    MAX_AGENT_CONCURRENCY = 2
    THREAD_POOL_WORKERS = 8
    
    # Data Persistence
    DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "./data")
    
    # Channels
    CHANNELS = {
        "MAIN": "#ops",
        "RESEARCH": "#ops-research",
        "DEV": "#ops-dev",
        "CONTENT": "#ops-content",
        "DESIGN": "#ops-design",
        "LOGS": "#ops-logs",
        "REVIEW": "#review",
        "DELIBERATION": "#ops-deliberation",
    }
    
    @classmethod
    def validate(cls):
        required = ["ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]
        missing = [key for key in required if not getattr(cls, key)]
        if missing:
            raise ValueError(f"Missing required environment variables: {missing}")
        return True