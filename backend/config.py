import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


class Settings:
    def __init__(self):
        self.reload(override=False)

    def reload(self, override: bool = False):
        if ENV_PATH.exists():
            load_dotenv(dotenv_path=ENV_PATH, override=override)
        default_proposal_db = Path(__file__).resolve().parent / "data" / "proposals.sqlite3"
        self.PROPOSAL_DB_PATH: str = os.getenv("PROPOSAL_DB_PATH", str(default_proposal_db))
        self.WORKFLOW_DB_PATH: str = os.getenv("WORKFLOW_DB_PATH", self.PROPOSAL_DB_PATH)
        self.APPROVAL_AUTH_TOKENS: str | None = os.getenv("APPROVAL_AUTH_TOKENS")
        self.GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")
        self.GITHUB_REPO: str | None = os.getenv("GITHUB_REPO")
        # Writes remain disabled unless a future deployment explicitly enables
        # this feature after adding its separate authorization workflow.
        self.GITHUB_WRITES_ENABLED: bool = os.getenv("GITHUB_WRITES_ENABLED", "false").strip().lower() == "true"
        self.LLM_API_KEY: str | None = os.getenv("LLM_API_KEY")
        self.LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
        self.LLM_FALLBACK_MODEL: str = os.getenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b")
        self.LLM_MODEL_PLANNER: str | None = os.getenv("LLM_MODEL_PLANNER")
        self.LLM_MODEL_CODER: str | None = os.getenv("LLM_MODEL_CODER")
        self.LLM_MODEL_TESTER: str | None = os.getenv("LLM_MODEL_TESTER")
        self.LLM_MODEL_REVIEWER: str | None = os.getenv("LLM_MODEL_REVIEWER")

    @property
    def approval_auth_tokens(self) -> str | None:
        return os.getenv("APPROVAL_AUTH_TOKENS", self.APPROVAL_AUTH_TOKENS)

    def get_model(self, agent_role: str | None = None) -> str:
        """Return the configured model for a specific agent role or the default model."""
        if agent_role:
            role_key = f"LLM_MODEL_{agent_role.upper()}"
            specific_model = os.getenv(role_key) or getattr(self, role_key, None)
            if specific_model and str(specific_model).strip():
                return str(specific_model).strip()
        env_val = os.getenv("LLM_MODEL")
        if env_val and env_val.strip():
            return env_val.strip()
        configured = getattr(self, "LLM_MODEL", None)
        if configured and str(configured).strip():
            return str(configured).strip()
        return "openai/gpt-oss-120b"


settings = Settings()
