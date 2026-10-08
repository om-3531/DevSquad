# DevSquad - API Plan

## 1. Overview

DevSquad will use different APIs and services to connect the AI agents with GitHub, Large Language Models, testing environments, and the application backend.

The main integrations planned for the project are:

1. GitHub API
2. LLM API
3. LangGraph
4. Docker
5. pytest
6. FastAPI
7. React Frontend

---

## 2. GitHub API

### Purpose

The GitHub API will connect DevSquad with GitHub repositories.

It will allow DevSquad to read repository information, understand issues, access source code, create branches, commit changes, and create Pull Requests.

### Planned Operations

DevSquad will eventually support:

- Get repository information
- List repositories
- List GitHub issues
- Get issue details
- Read repository files
- Create branches
- Create or update files
- Create commits
- Get commit information
- Create Pull Requests
- Get Pull Request information
- Add Pull Request comments
- Check Pull Request status

### Authentication

GitHub authentication will use a secure GitHub token.

The token will be stored in an environment variable.

Example:

```env
GITHUB_TOKEN=your_github_token_here

The token must never be committed to GitHub.
3. GitHub Permissions
DevSquad should use the minimum permissions required for its operations.
Depending on the final implementation, required permissions may include:
- Repository read access
- Issues read/write access
- Pull Request read/write access
- Contents read/write access
Permissions will be configured carefully to reduce security risks.
4. LLM API
Purpose
The LLM will provide the intelligence behind the DevSquad agents.
The LLM will be used for:
- Understanding GitHub issues
- Creating development plans
- Understanding source code
- Generating code
- Generating tests
- Analyzing test failures
- Reviewing code
- Producing development summaries
Possible Providers
The project may support one or more of the following:
- OpenAI
- Google Gemini
- Groq
- Local open-source models
The final provider will be selected during the implementation phase.
5. LLM API Key
The LLM API key will be stored securely in an environment variable.
Example:
LLM_API_KEY=your_llm_api_key_here

The real API key must never be written directly inside source code.
The .env file will also be excluded from Git using .gitignore.
6. Planner Agent API Flow
The Planner Agent will receive information such as:
Repository
Issue Title
Issue Description
Relevant Repository Context

The LLM will process this information and return a structured development plan.
Example:
Input:

Issue:
Add login API.

Output:

1. Create login endpoint.
2. Add request validation.
3. Validate credentials.
4. Generate authentication token.
5. Add tests.

7. Coder Agent API Flow
The Coder Agent will receive:
Development Plan
Issue Description
Relevant Source Files
Existing Tests

The LLM will generate the required code changes.
Expected output:
Changed Files
Code Changes
Change Explanation
Potential Risks

The system will then apply the changes to a development branch.
8. Tester Agent API Flow
The Tester Agent will receive:
Repository
Changed Files
Existing Tests

The system will execute tests in a controlled environment.
Expected output:
Test Status
Passed Tests
Failed Tests
Error Messages
Execution Time

Example:
Status: FAILED

Passed: 8
Failed: 1

Failed Test:
test_invalid_email

Error:
Invalid email format was accepted.

9. Reviewer Agent API Flow
The Reviewer Agent will receive:
Issue
Development Plan
Changed Files
Test Results

The LLM will review the changes.
Expected output:
Code Quality
Security
Correctness
Test Coverage
Suggestions
Final Review

10. LangGraph
Purpose
LangGraph will manage communication and workflow between the AI agents.
It will be responsible for:
- Agent state
- Agent transitions
- Conditional workflows
- Retry loops
- Error handling
- Workflow completion
Example:
Planner
   ↓
Coder
   ↓
Tester
   ↓
Tests Pass?
   ├── No → Coder
   |
   └── Yes → Reviewer

11. FastAPI
Purpose
FastAPI will act as the backend API layer.
The frontend will communicate with FastAPI.
Possible API endpoints:
GET    /health
GET    /repositories
GET    /repositories/{owner}/{repo}/issues
GET    /repositories/{owner}/{repo}/issues/{issue_number}
POST   /workflow/start
GET    /workflow/{workflow_id}
GET    /workflow/{workflow_id}/status
GET    /workflow/{workflow_id}/logs
GET    /workflow/{workflow_id}/result

These endpoints are planned and may change during development.
12. Example Workflow API
Start Workflow
POST /workflow/start

Example request:
{
  "owner": "example-user",
  "repository": "example-project",
  "issue_number": 25
}

Example response:
{
  "workflow_id": "devsquad-001",
  "status": "started"
}

13. Workflow Status API
GET /workflow/{workflow_id}/status

Example response:
{
  "workflow_id": "devsquad-001",
  "current_agent": "tester",
  "status": "running"
}

Possible statuses:
pending
running
completed
failed
waiting_for_human

14. Workflow Result API
GET /workflow/{workflow_id}/result

Example response:
{
  "status": "completed",
  "branch": "devsquad/issue-25",
  "tests_passed": true,
  "review_status": "approved",
  "pull_request_url": "https://github.com/example/project/pull/42"
}

15. Docker
Purpose
Docker will provide an isolated environment for executing generated code and running tests.
Expected flow:
Generated Code
      ↓
Docker Container
      ↓
Run Tests
      ↓
Capture Output
      ↓
Return Result

The container should have restrictions on:
- CPU
- Memory
- Execution time
- Filesystem access
- Network access
16. pytest
Purpose
pytest will be used to run automated tests for Python projects.
The Tester Agent will use pytest where applicable.
Example command:
pytest

The system will capture:
- Exit code
- Standard output
- Error output
- Passed tests
- Failed tests
- Execution time
17. API Error Handling
The backend should handle API failures safely.
Possible errors:
GitHub authentication failure
Repository not found
Issue not found
Permission denied
LLM API failure
LLM rate limit
Invalid LLM response
Docker execution failure
Test execution failure
Timeout
Network failure

The system should return meaningful error messages without exposing sensitive information.
18. Rate Limits
External APIs may have rate limits.
DevSquad should:
- Detect rate-limit errors.
- Avoid unnecessary API calls.
- Retry temporary failures carefully.
- Use exponential backoff where appropriate.
- Log failures.
- Show useful status messages to the user.
19. Security Rules
The following rules are mandatory:
Never commit:
API keys
GitHub tokens
Passwords
Private keys
.env files
Sensitive user data

Use:
Environment variables
Secure secret storage
Minimum required permissions
Input validation
Safe logging

20. Planned API Architecture
The overall API communication will look like:
React Frontend
      |
      | HTTP
      v
FastAPI Backend
      |
      +-------------------+
      |                   |
      v                   v
 GitHub API            LLM API
      |                   |
      +---------+---------+
                |
                v
          LangGraph
                |
       +--------+--------+
       |        |        |
       v        v        v
    Planner   Coder    Tester
                         |
                         v
                       Docker
                         |
                         v
                     Test Result
                         |
                         v
                      Reviewer
                         |
                         v
                    GitHub PR

21. Future API Integrations
Future versions may include:
- GitHub Actions API
- Code security scanning APIs
- Observability APIs
- Authentication services
- Database services
- Additional LLM providers
- Deployment APIs
These integrations will only be added when required.
22. Current API Development Status
GitHub API
Status: Planned
LLM API
Status: Planned
LangGraph
Status: Planned
FastAPI
Status: Planned
Docker
Status: Planned
pytest
Status: Planned
React API Integration
Status: Planned
23. Development Principle
DevSquad will not depend on APIs blindly.
Each external service will be integrated step-by-step and tested independently before being connected to the complete agent workflow.
The development order will be:
GitHub API
    ↓
LLM API
    ↓
FastAPI
    ↓
Planner Agent
    ↓
Coder Agent
    ↓
Tester Agent
    ↓
Reviewer Agent
    ↓
Docker
    ↓
Pull Request Automation
