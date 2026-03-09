# src/config.py - Centralized config with validation
class Config:
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
    # ... other configs
    
    @classmethod
    def validate(cls):
        required = ["ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN"]
        missing = [k for k in required if not getattr(cls, k)]
        if missing:
            raise ValueError(f"Missing: {missing}")