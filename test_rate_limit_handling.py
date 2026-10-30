import os
from unittest.mock import MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient
from groq import RateLimitError

from backend.config import Settings
from backend.main import app, handle_ai_rate_limit, select_relevant_files
from backend.agents.llm_text import (
    extract_retry_after,
    format_rate_limit_detail,
    is_daily_limit_error,
    is_rate_limit_error,
)
from backend.agents.model_manager import (
    check_model_availability,
    get_available_models,
    get_model_limits,
    recommend_fallback_model,
    sanitize_secret,
)


def _make_rate_limit_error(message="Rate limit reached", headers=None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(429, headers=headers or {}, request=req)
    return RateLimitError(message=message, response=resp, body=None)


# 1. Test Retry-After Extraction from Headers and Text
def test_extract_retry_after_from_header():
    err = _make_rate_limit_error(headers={"retry-after": "14.5"})
    assert extract_retry_after(err) == 14.5

    err_capital = _make_rate_limit_error(headers={"Retry-After": "30"})
    assert extract_retry_after(err_capital) == 30.0

    err_tokens = _make_rate_limit_error(headers={"x-ratelimit-reset-tokens": "2.2"})
    assert extract_retry_after(err_tokens) == 2.2


def test_extract_retry_after_from_message_text():
    err1 = _make_rate_limit_error("Rate limit reached on TPM. Please try again in 5.4s.")
    assert extract_retry_after(err1) == 5.4

    err2 = _make_rate_limit_error("Tokens per minute limit reached. Retry after 12s.")
    assert extract_retry_after(err2) == 12.0

    err3 = _make_rate_limit_error("Please wait 8.5 seconds before retrying.")
    assert extract_retry_after(err3) == 8.5


# 2. Test Daily Quota (TPD) vs TPM Detection
def test_is_daily_limit_error():
    tpd_err = _make_rate_limit_error("Rate limit reached for model openai/gpt-oss-120b on tokens per day (TPD: 200,000)")
    assert is_daily_limit_error(tpd_err) is True

    tpm_err = _make_rate_limit_error("Rate limit reached on tokens per minute (TPM: 8,000). Try again in 6s.")
    assert is_daily_limit_error(tpm_err) is False


# 3. Test Secret Sanitization in Logs and Errors
def test_sanitize_secret_redacts_groq_and_github_tokens():
    text_with_groq = "Request failed with key gsk_1234567890abcdefghijklmnop and Bearer secret_token_xyz"
    sanitized = sanitize_secret(text_with_groq)
    assert "gsk_1234567890abcdefghijklmnop" not in sanitized
    assert "[REDACTED_GROQ_KEY]" in sanitized
    assert "secret_token_xyz" not in sanitized

    text_with_github = "GitHub PAT: ghp_123456789012345678901234567890"
    sanitized_gh = sanitize_secret(text_with_github)
    assert "ghp_123456789012345678901234567890" not in sanitized_gh
    assert "[REDACTED_GITHUB_TOKEN]" in sanitized_gh


# 4. Test Model Availability Check
def test_check_model_availability_with_mocked_groq_client():
    mock_client = MagicMock()
    m1 = MagicMock()
    m1.id = "openai/gpt-oss-120b"
    m1.active = True
    m2 = MagicMock()
    m2.id = "openai/gpt-oss-20b"
    m2.active = True

    mock_client.models.list.return_value = MagicMock(data=[m1, m2])

    assert check_model_availability(mock_client, "openai/gpt-oss-120b") is True
    assert check_model_availability(mock_client, "openai/gpt-oss-20b") is True
    assert check_model_availability(mock_client, "non-existent-model") is False


# 5. Test Fallback Model Recommendation Based on Availability and Limits
def test_recommend_fallback_model_checks_availability():
    mock_client = MagicMock()
    m1 = MagicMock()
    m1.id = "openai/gpt-oss-120b"
    m2 = MagicMock()
    m2.id = "openai/gpt-oss-20b"

    mock_client.models.list.return_value = MagicMock(data=[m1, m2])

    # When 120b is rate limited, recommend available 20b
    fallback = recommend_fallback_model(mock_client, current_model="openai/gpt-oss-120b", configured_fallback="openai/gpt-oss-20b")
    assert fallback == "openai/gpt-oss-20b"

    # Does not recommend the model that is currently rate limited
    fallback_same = recommend_fallback_model(mock_client, current_model="openai/gpt-oss-20b", configured_fallback="openai/gpt-oss-20b")
    assert fallback_same == "openai/gpt-oss-120b"


def test_get_model_limits():
    limits_120b = get_model_limits("openai/gpt-oss-120b")
    assert limits_120b["tpm"] == 8000
    assert limits_120b["tpd"] == 200000

    limits_llama = get_model_limits("llama-3.3-70b-versatile")
    assert limits_llama["tpm"] == 12000


# 6. Test Configurable Model Selection Through Environment Variables
def test_settings_configurable_models(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "custom/primary-model")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "custom/fallback-model")
    monkeypatch.setenv("LLM_MODEL_CODER", "custom/coder-model")

    cfg = Settings()
    assert cfg.get_model() == "custom/primary-model"
    assert cfg.get_model("coder") == "custom/coder-model"
    assert cfg.get_model("tester") == "custom/primary-model"
    assert cfg.LLM_FALLBACK_MODEL == "custom/fallback-model"


# 7. Test Irrelevant Repository File Filtering
def test_select_relevant_files_filters_docs_and_unrelated_files():
    tree_paths = [
        "README.md",
        "docs/setup.md",
        "package-lock.json",
        "frontend/dist/bundle.js",
        "backend/main.py",
        "backend/github/client.py",
        "backend/github/issues.py",
        "backend/agents/coder.py",
        "tests/test_random.py",
    ]

    relevant = select_relevant_files(
        tree_paths=tree_paths,
        issue_title="Add get_issues to backend/github/client.py",
        issue_body="Please implement get_issues in client.py",
        target_path="backend/github/client.py",
    )

    # Must prioritize backend/github/client.py
    assert relevant[0] == "backend/github/client.py"
    # Non-code files (README.md, package-lock.json) must not crowd out relevant python code
    assert "README.md" not in relevant
    assert "package-lock.json" not in relevant
    assert len(relevant) <= 12


# 8. Test Graceful HTTP 429 Response Format and Retry-After Header
def test_handle_ai_rate_limit_helper():
    err = _make_rate_limit_error(
        message="Rate limit reached for model openai/gpt-oss-120b. Please try again in 14.5s.",
        headers={"retry-after": "14.5"},
    )
    http_exc = handle_ai_rate_limit(err, model="openai/gpt-oss-120b")
    assert http_exc.status_code == 429
    assert "14.5s" in http_exc.detail or "14" in http_exc.detail
    assert http_exc.headers.get("Retry-After") == "14"


def test_endpoint_agents_code_returns_graceful_429(monkeypatch):
    client = TestClient(app)

    async def mock_get_issue(*args, **kwargs):
        return {"title": "Test Issue", "body": "Fix something"}

    monkeypatch.setattr("backend.main.issue_service.get_issue", mock_get_issue)

    def mock_create_plan(*args, **kwargs):
        raise _make_rate_limit_error(headers={"retry-after": "8.0"})

    monkeypatch.setattr("backend.main.planner_agent.create_plan", mock_create_plan)

    response = client.post("/agents/code", json={"repo": "om-3531/DevSquad", "issue_number": 1})
    assert response.status_code == 429
    data = response.json()
    assert "rate limit exceeded" in data["detail"].lower()
    assert response.headers.get("Retry-After") == "8"


def test_endpoint_models_status():
    client = TestClient(app)
    response = client.get("/models/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "current_model" in data
    assert "configured_fallback" in data


# 9. Test Default Model is openai/gpt-oss-120b and Overridable by Env
def test_default_model_is_gpt_oss_120b(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_PLANNER", raising=False)
    monkeypatch.delenv("LLM_MODEL_CODER", raising=False)
    monkeypatch.delenv("LLM_MODEL_TESTER", raising=False)
    monkeypatch.delenv("LLM_MODEL_REVIEWER", raising=False)

    cfg = Settings()
    cfg.LLM_MODEL = "openai/gpt-oss-120b"
    assert cfg.get_model() == "openai/gpt-oss-120b"
    assert cfg.get_model("planner") == "openai/gpt-oss-120b"
    assert cfg.get_model("coder") == "openai/gpt-oss-120b"


# 10. Test Dynamic Agent Model Resolution
def test_agent_dynamic_model_resolution(monkeypatch):
    from backend.agents.coder import CoderAgent
    from backend.agents.planner import PlannerAgent
    from backend.agents.tester import TesterAgent
    from backend.agents.reviewer import ReviewerAgent

    coder = CoderAgent()
    planner = PlannerAgent()
    tester = TesterAgent()
    reviewer = ReviewerAgent()

    monkeypatch.setenv("LLM_MODEL", "openai/gpt-oss-120b")
    assert coder.model == "openai/gpt-oss-120b"
    assert planner.model == "openai/gpt-oss-120b"
    assert tester.model == "openai/gpt-oss-120b"
    assert reviewer.model == "openai/gpt-oss-120b"

    # Now override LLM_MODEL dynamically without recreating agent objects
    monkeypatch.setenv("LLM_MODEL", "custom/env-override-model")
    assert coder.model == "custom/env-override-model"
    assert planner.model == "custom/env-override-model"
    assert tester.model == "custom/env-override-model"
    assert reviewer.model == "custom/env-override-model"

    # Now test role-specific override
    monkeypatch.setenv("LLM_MODEL_CODER", "custom/specialized-coder")
    assert coder.model == "custom/specialized-coder"
    assert planner.model == "custom/env-override-model"


# 11. Test Startup Log Contains Model Name and No Secrets
def test_startup_logging_clean(caplog):
    import logging
    from backend.main import log_startup_configuration

    with caplog.at_level(logging.INFO):
        log_startup_configuration()

    log_text = caplog.text
    assert "Primary LLM model:" in log_text
    assert "Fallback LLM model:" in log_text
    # Ensure no tokens or secret values are present in log
    assert "gsk_" not in log_text
    assert "ghp_" not in log_text
    assert "APPROVAL_AUTH_TOKENS" not in log_text


# 12. Regression Test: Primary Model Retries with Backoff on Temporary 429
def test_rate_limit_primary_recovers_after_retry():
    from backend.agents.coder import CoderAgent

    coder = CoderAgent.__new__(CoderAgent)
    coder._model = "openai/gpt-oss-120b"
    coder._fallback_model = "openai/gpt-oss-20b"
    coder.on_fallback = None
    coder.client = MagicMock()

    choice = MagicMock()
    choice.message.content = "def add(a, b):\n    return a + b\n"
    choice.finish_reason = "stop"
    valid_resp = MagicMock(choices=[choice])

    rate_limit = _make_rate_limit_error(
        message="TPM rate limit. Please try again in 2.0s.",
        headers={"retry-after": "2.0"},
    )

    coder.client.chat.completions.create.side_effect = [
        rate_limit,
        valid_resp,
    ]

    with patch("backend.agents.coder.time.sleep") as mock_sleep:
        code = coder._generate("Implement add function")

    assert "def add" in code
    assert coder.client.chat.completions.create.call_count == 2
    mock_sleep.assert_called_once_with(2.5)  # 2.0 + 0.5 buffer


# 13. Regression Test: Rate Limit Automatically Switches to Fallback Model and Succeeds
def test_rate_limit_switches_to_fallback_model_and_succeeds():
    from backend.agents.coder import CoderAgent

    coder = CoderAgent.__new__(CoderAgent)
    coder._model = "openai/gpt-oss-120b"
    coder._fallback_model = "openai/gpt-oss-20b"
    coder.client = MagicMock()

    fallback_events = []
    coder.on_fallback = lambda from_m, to_m, reason: fallback_events.append((from_m, to_m, reason))

    daily_err = _make_rate_limit_error(
        message="Rate limit reached for model openai/gpt-oss-120b on tokens per day (TPD: 200,000)"
    )

    choice = MagicMock()
    choice.message.content = "def multiply(a, b):\n    return a * b\n"
    choice.finish_reason = "stop"
    valid_resp = MagicMock(choices=[choice])

    def mock_create(model, **kwargs):
        if model == "openai/gpt-oss-120b":
            raise daily_err
        return valid_resp

    coder.client.chat.completions.create.side_effect = mock_create

    with patch("backend.agents.coder.time.sleep"):
        code = coder._generate("Implement multiply function")

    assert "def multiply" in code
    assert len(fallback_events) == 1
    assert fallback_events[0][0] == "openai/gpt-oss-120b"
    assert fallback_events[0][1] == "openai/gpt-oss-20b"
    assert "daily_quota" in fallback_events[0][2]
    assert coder.model == "openai/gpt-oss-20b"


# 14. Regression Test: Rate Limit Fallback Failure (Both Primary and Fallback Models 429)
def test_rate_limit_fallback_also_fails():
    from backend.agents.coder import CoderAgent

    coder = CoderAgent.__new__(CoderAgent)
    coder._model = "openai/gpt-oss-120b"
    coder._fallback_model = "openai/gpt-oss-20b"
    coder.on_fallback = None
    coder.client = MagicMock()

    primary_err = _make_rate_limit_error("Daily quota exceeded on 120b")
    fallback_err = _make_rate_limit_error("Daily quota exceeded on 20b")

    def mock_create(model, **kwargs):
        if model == "openai/gpt-oss-120b":
            raise primary_err
        raise fallback_err

    coder.client.chat.completions.create.side_effect = mock_create

    with patch("backend.agents.coder.time.sleep"):
        with pytest.raises(RateLimitError):
            coder._generate("Implement divide function")


# 15. Regression Test: Rate Limit Exhausted Without Fallback Model
def test_rate_limit_exhausted_retries_without_fallback():
    from backend.agents.coder import CoderAgent

    coder = CoderAgent.__new__(CoderAgent)
    coder._model = "openai/gpt-oss-120b"
    coder._fallback_model = None
    coder.on_fallback = None
    coder.client = MagicMock()

    rl_err = _make_rate_limit_error("TPM limit reached without fallback")
    coder.client.chat.completions.create.side_effect = rl_err

    with patch("backend.agents.coder.time.sleep") as mock_sleep:
        with pytest.raises(RateLimitError):
            coder._generate("Implement power function")

    assert coder.client.chat.completions.create.call_count == 3
    assert mock_sleep.call_count == 2


# 16. Regression Test: Planner Switches to Fallback Model on Rate Limit
def test_planner_switches_to_fallback_on_rate_limit():
    from backend.agents.planner import PlannerAgent

    planner = PlannerAgent.__new__(PlannerAgent)
    planner._model = "openai/gpt-oss-120b"
    planner._fallback_model = "openai/gpt-oss-20b"
    planner.client = MagicMock()

    fallback_events = []
    planner.on_fallback = lambda from_m, to_m, reason: fallback_events.append((from_m, to_m, reason))

    daily_err = _make_rate_limit_error("Tokens per day quota exhausted for 120b")
    plan_json = '{"summary": "Fallback plan", "architecture_guidance": "Clean architecture", "target_files": [], "acceptance_criteria": ["Works"]}'

    choice = MagicMock()
    choice.message.content = plan_json
    choice.finish_reason = "stop"
    valid_resp = MagicMock(choices=[choice])

    def mock_create(model, **kwargs):
        if model == "openai/gpt-oss-120b":
            raise daily_err
        return valid_resp

    planner.client.chat.completions.create.side_effect = mock_create

    with patch("backend.agents.planner.time.sleep"):
        plan = planner.create_plan(title="Test Feature", description="Implement test feature")

    assert plan["summary"] == "Fallback plan"
    assert len(fallback_events) == 1
    assert planner.model == "openai/gpt-oss-20b"


# 17. Regression Test: Full Workflow Continues Successfully with Fallback Model
@pytest.mark.asyncio
async def test_workflow_continues_using_fallback_model(monkeypatch):
    from backend.main import execute_agent_workflow_internal, planner_agent, coder_agent, reviewer_agent, tester_agent
    from backend.schemas import IssueRequest

    # Mock issue and repo services
    async def mock_get_issue(repo, issue_number):
        return {"title": "Implement helper", "body": "Write a pure python helper."}

    async def mock_get_repo(repo):
        return {"default_branch": "main"}

    async def mock_get_branch_sha(repo, branch):
        return "1111222233334444555566667777888899990000"

    async def mock_get_tree(repo, branch):
        return ["backend/helper.py"]

    monkeypatch.setattr("backend.main.issue_service.get_issue", mock_get_issue)
    monkeypatch.setattr("backend.main.repository_service.get_repository", mock_get_repo)
    monkeypatch.setattr("backend.main.repository_service.get_branch_sha", mock_get_branch_sha)
    monkeypatch.setattr("backend.main.repository_service.get_tree", mock_get_tree)

    # In planner, trigger fallback callback to simulate rate limit on primary model
    def mock_create_plan(*args, **kwargs):
        if planner_agent.on_fallback:
            planner_agent.on_fallback("openai/gpt-oss-120b", "openai/gpt-oss-20b", "daily_quota_exhausted")
        return {
            "summary": "Plan generated with fallback model",
            "architecture_guidance": "Standard design",
            "target_files": [{"path": "backend/helper.py", "operation": "create", "rationale": "new helper"}],
            "acceptance_criteria": ["Returns valid result"],
        }

    monkeypatch.setattr(planner_agent, "create_plan", mock_create_plan)
    monkeypatch.setattr(coder_agent, "generate_code", lambda *a, **kw: "def helper_fn():\n    return 'success'\n")
    monkeypatch.setattr(reviewer_agent, "review_code", lambda *a, **kw: "VERDICT: APPROVE\nLooks great.")
    monkeypatch.setattr(tester_agent, "generate_tests", lambda *a, **kw: "from candidate import helper_fn\ndef test_fn(): assert helper_fn() == 'success'")

    monkeypatch.setattr(
        "backend.main.generated_test_runner.run_generated_tests",
        lambda *a, **kw: {
            "passed": True,
            "status": "PASSED",
            "summary": "1 passed in 0.05s",
            "return_code": 0,
            "stdout": "1 passed",
            "stderr": "",
            "failed_tests": [],
        },
    )

    request = IssueRequest(repo="om-3531/DevSquad", issue_number=1, target_path="backend/helper.py")
    result = await execute_agent_workflow_internal(request)

    assert result["status"] == "success"
    assert result["fallback_triggered"] is True
    assert result["model_used"] == "openai/gpt-oss-20b"
    assert "daily_quota" in result["fallback_reason"]
    assert result["quality_gate"]["approved"] is True

