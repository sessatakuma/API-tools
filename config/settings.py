"""Application settings loaded from .env.

The accent pipeline previously needed `YAHOO_API_KEY` to call Yahoo's MA
HTTP endpoint; the local fugashi + UniDic migration in #50 dropped that
dependency entirely, so this file is intentionally near-empty. Keep it so
future settings have an obvious home.
"""

from dotenv import load_dotenv

load_dotenv()
