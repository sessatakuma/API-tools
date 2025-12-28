from dotenv import load_dotenv
import os

load_dotenv()

YAHOO_API_KEY: str = os.getenv("YAHOO_API_key", "")
assert YAHOO_API_KEY, "YAHOO_API_key environment variable is not set"
