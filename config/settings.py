import os

from dotenv import load_dotenv

load_dotenv()

YAHOO_API_KEY: str = os.getenv("YAHOO_API_KEY", "")
assert YAHOO_API_KEY, "YAHOO_API_KEY environment variable is not set"
