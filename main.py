#!/usr/bin/env python3
"""SlackOS - Multi-Agent Slack Bot System"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.slack_client import SlackClient

def main():
    """Main entry point"""
    try:
        client = SlackClient()
        print("🚀 SlackOS starting...")
        client.start()
    except KeyboardInterrupt:
        print("\n👋 SlackOS shutting down...")
    except Exception as e:
        print(f"❌ Failed to start SlackOS: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()