import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    GITHUB_REPO = os.getenv("GITHUB_REPO")
    LLM_API_KEY = os.getenv("LLM_API_KEY")


settings = Settings()
