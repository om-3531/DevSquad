import json
from unittest.mock import MagicMock, patch

from backend.agents.planner import PlannerAgent


def make_planner():
    with patch("backend.agents.planner.Groq") as mock_groq:
        planner = PlannerAgent.__new__(PlannerAgent)
        planner.model = "test-model"
        planner.client = mock_groq.return_value
    return planner


def test_create_plan_includes_issue_details():
    planner = make_planner()

    expected_plan = {
        "problem_summary": "Fix login",
        "steps": ["Add validation"],
        "testing_strategy": "Run tests",
        "risks": [],
    }

    response = MagicMock()
    response.choices[0].message.content = json.dumps(expected_plan)
    planner.client.chat.completions.create.return_value = response

    result = planner.create_plan(
        title="Fix login",
        description="Reject invalid passwords",
        repository_context="FastAPI application",
    )

    prompt = planner.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]

    assert "Fix login" in prompt
    assert "Reject invalid passwords" in prompt
    assert "FastAPI application" in prompt
    assert result == expected_plan


def test_create_plan_handles_invalid_json():
    planner = make_planner()

    response = MagicMock()
    response.choices[0].message.content = "not valid JSON"
    planner.client.chat.completions.create.return_value = response

    result = planner.create_plan(
        title="Example",
        description="Example issue",
    )

    assert result["problem_summary"] == (
        "The AI returned a response that was not valid JSON."
    )
    assert result["raw_response"] == "not valid JSON"