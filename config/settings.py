import os

from dotenv import load_dotenv

load_dotenv()

YAHOO_API_KEY: str = os.getenv("YAHOO_API_KEY", "")
assert YAHOO_API_KEY, "YAHOO_API_KEY environment variable is not set"

ALLOWED_HOSTS: list[str] = os.getenv("ALLOWED_HOSTS", "").split(",")
assert ALLOWED_HOSTS, "ALLOWED_HOSTS environment variable is not set"

ALLOW_ORIGINS: list[str] = os.getenv("ALLOW_ORIGINS", "").split(",")
assert ALLOW_ORIGINS, "ALLOW_ORIGINS environment variable is not set"

BUILD_API_KEY: str = os.getenv("BUILD_API_KEY", "")
assert BUILD_API_KEY, "BUILD_API_KEY environment variable is not set"