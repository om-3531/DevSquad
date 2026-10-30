# DevSquad — AI-Powered Software Development Team

DevSquad is an AI-powered, multi-agent software development system that assists developers in solving GitHub issues through an organized, multi-stage workflow.

Specialized AI agents plan solutions, generate Python code, synthesize unit tests, review implementations for security and adherence, run candidate tests in an isolated Docker environment, and prepare a dry-run GitHub write plan for human review.

---

## 🚀 Key Features

- **Planner Agent:** Analyzes GitHub issues and generates a structured implementation plan.
- **Coder Agent:** Implements Python code with syntax validation, token-limit detection, and context-preserving refinement.
- **Tester Agent:** Synthesizes isolated, deterministic pytest suites that mock external network calls and avoid restricted modules.
- **Reviewer Agent:** Critically evaluates proposed code for task satisfaction, security, logic errors, and edge cases.
- **Isolated Docker Test Runner:** Executes candidate code and tests in a hardened, non-root Docker container with `--network none`, memory/CPU caps, and approved dependencies (`requests`).
- **Bounded Retry Loop:** Automatically feeds review feedback and test failures back into the coder agent for up to 2 correction attempts.
- **Safe GitHub Integration:** Fetches issue and repository context and simulates the branch, commit, and draft-PR operations required after explicit human authorization. The workflow endpoint never performs GitHub writes.
- **Interactive UI Dashboard:** React/Vite dashboard displaying real-time pipeline status, reviewer notes, test output, and draft pull-request links.

---

## 🛠 Tech Stack

### Backend
- **Python 3.12+**
- **FastAPI**: Async REST API with CORS support
- **Uvicorn**: ASGI server
- **Pydantic**: Request and schema validation
- **HTTPX**: Async HTTP client for GitHub API interactions

### AI Agents & Orchestration
- **Groq SDK**: LLM inference (e.g. `openai/gpt-oss-20b` or `llama-3.3-70b-versatile`)
- **LangGraph**: Workflow planning and state structure
- **Custom Agent Loop**: Structured prompt engineering, syntax verification, and retry handling

### Isolated Testing & Quality Gates
- **Docker**: Containerized execution with resource limits and network isolation
- **pytest**: Test execution runner

### Frontend
- **React 19**
- **Vite**
- **Vanilla CSS**: Clean, responsive dashboard layout

---

## 🏗 Architecture & Workflow

```text
                    GitHub
                       |
                       v
                 GitHub Issue
                       |
                       v
                 FastAPI Backend
                       |
                       v
                 Agent Workflow
                       |
          +------------+------------+
          |            |            |
          v            v            v
       Planner       Coder        Tester
          |            |            |
          +------------+------------+
                       |
                       v
                    Reviewer
                       |
                       v
                Isolated Tests
                 (Docker test runner)
                       |
             +---------+---------+
             |                   |
             v                   v
         Tests Pass          Tests Fail
             |                   |
             v                   v
        Quality Gate       Retry / Review (Up to 2x)
             |
             v
     Persistent Proposal
       (human approval)
             |
             v
       No GitHub writes
```

The workflow only marks a candidate ready for a future draft pull request when:
1. Reviewer issues `VERDICT: APPROVE`.
2. Generated tests pass in the isolated Docker container.
3. A valid, non-existing `.py` target path is supplied.

### Proposal persistence and approval

Successful dry runs create a proposal stored in local SQLite (`PROPOSAL_DB_PATH`,
default `backend/data/proposals.sqlite3`). The database stores the candidate text
only for server-side digest verification; proposal endpoints return metadata and
the reviewed diff, not a `proposed_content` field. Approval is atomic, expires
after the configured TTL, and only records approval—it does not create a branch,
commit, or pull request. SQLite is suitable for this single-machine deployment;
a multi-instance deployment needs shared storage plus real user authentication
and authorization before approvals can be attributed to people.

---

## 📁 Project Structure

```text
DevSquad/
├── backend/
│   ├── agents/
│   │   ├── coder.py                # Coder agent with syntax verification & retry
│   │   ├── planner.py              # Implementation planner agent
│   │   ├── reviewer.py             # Code reviewer agent
│   │   ├── tester.py               # Pytest synthesis agent
│   │   ├── test_runner.py          # Local test runner utility
│   │   ├── generated_test_runner.py# Isolated Docker container runner
│   │   └── llm_text.py             # Markdown fence parsing
│   ├── github/
│   │   ├── client.py               # Resilient GitHub API client (retry, reconciliation)
│   │   ├── issues.py               # Issue fetching & creation service
│   │   ├── repository.py           # Repository metadata & contents service
│   │   ├── validation.py           # Git ref and path safety validation
│   │   └── write_orchestrator.py   # Guarded write orchestrator
│   ├── auth.py                     # Role-based access control & Bearer authenticator
│   ├── config.py                   # Configuration and settings loader
│   ├── patch_validator.py          # Multi-file patch validation & traversal protection
│   ├── proposals.py                # Proposal persistence, digests & human approval
│   ├── schemas.py                  # Pydantic input/output models
│   ├── workflows.py                # Workflow execution store, stages & history events
│   └── main.py                     # FastAPI application endpoints
├── frontend/
│   ├── src/
│   │   ├── App.jsx                 # Dashboard UI component
│   │   ├── App.css                 # Responsive dashboard styling
│   │   ├── proposalApproval.js     # Human approval & error formatting utilities
│   │   ├── proposalApproval.test.mjs# Frontend node:test suite
│   │   └── main.jsx                # Application root
│   ├── package.json                # Frontend dependencies
│   └── vite.config.js              # Vite configuration
├── Dockerfile.test                 # Isolated test runner container image
├── candidate-requirements.txt      # Hardened dependencies for sandbox runner
├── requirements.txt                # Python backend dependencies
├── pytest.ini                      # Pytest configuration
├── test_auth_and_proposals.py      # RBAC, approval, and proposal test suite
├── test_coder_agent.py             # Coder agent unit tests
├── test_generated_test_runner.py   # Container execution tests
├── test_github_client.py           # GitHub client & transient error retry tests
├── test_review_and_test_regression.py # Quality gate & terminal state regressions
├── test_workflow.py                # End-to-end mocked workflow tests
├── test_workflow_history.py        # History, pagination, and metrics tests
├── test_write_orchestrator.py      # Guarded branch/commit/PR orchestration tests
├── .env.example                    # Template for required environment variables
└── README.md                       # Project documentation
```

---

## ⚙️ Prerequisites

1. **Python 3.12+**: Ensure Python is in your PATH.
2. **Node.js (v18+) & npm**: For running the Vite frontend.
3. **Docker Desktop**: Required for the isolated candidate test execution environment.
4. **Groq API Key**: Obtain a key from the [Groq Console](https://console.groq.com).
5. **GitHub Personal Access Token**: Create a token with `repo` scope permissions for repository access and draft PR creation.

---

## 📥 Installation & Setup

### 1. Clone the Repository

```powershell
git clone https://github.com/om-3531/DevSquad.git
cd DevSquad
```

### 2. Configure Python Virtual Environment (Windows PowerShell)

```powershell
# If script execution is restricted, allow for current process:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install backend dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Build the Isolated Docker Test Image

Ensure Docker Desktop is running, then build the test runner container:

```powershell
docker build -t devsquad-test-runner -f Dockerfile.test .
```

### 4. Configure Environment Variables

Copy the `.env.example` template to `.env`:

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in your credentials:

```env
GITHUB_TOKEN=ghp_yourActualGitHubTokenHere
GITHUB_REPO=your_username/your_repo
LLM_API_KEY=gsk_yourActualGroqApiKeyHere
LLM_MODEL=openai/gpt-oss-120b
```

> **Security Note:** Never commit `.env` or share API tokens. The `.gitignore` file includes `.env` to prevent accidental commits.

### 5. Install Frontend Dependencies

```powershell
cd frontend
npm install
cd ..
```

---

## ▶️ Running the Application

Open two PowerShell terminal windows from the `DevSquad` project root:

### Terminal 1: Start Backend (FastAPI)

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Verify backend health:
- Health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Interactive API Docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Terminal 2: Start Frontend (React/Vite)

```powershell
cd frontend
npm run dev
```

Open your browser to the local URL (typically [http://localhost:5173](http://localhost:5173)).

---

## 🧪 Testing and Quality Assurance

### Run Backend Tests

From the project root with the virtual environment activated:

```powershell
# Run all automated tests
python -m pytest -v

# Run workflow tests with detailed output
python -m pytest test_workflow.py -v

# Run test runner and container execution tests
python -m pytest test_generated_test_runner.py -v
```

### Run Frontend Verification

From the `frontend` folder:

```powershell
npm run lint
npm run build
```

---

## 💡 How to Use the Workflow

1. Navigate to the DevSquad dashboard at [http://localhost:5173](http://localhost:5173).
2. Enter the target **GitHub repository** (`owner/repository`).
3. Enter the **Issue number** (e.g., `1`).
4. (Optional) Provide a **New Python file path** (e.g., `src/solution.py`):
   - If provided and all review & tests pass, the workflow returns a clearly labelled write simulation; it does not create a branch, commit, or pull request.
   - If omitted, the workflow will plan, generate code, test, and review safely *without making any GitHub changes*.
5. Click **Run AI workflow**.
6. Monitor the 5-stage pipeline and inspect the reviewer feedback, synthesized unit tests, and candidate code in the execution output panel.

---

## Read-only Integration Checks

Start the backend first. These checks do not create GitHub branches, commits, issues, or pull requests.

```powershell
# Read one issue through the configured GitHub token.
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/github/issue -ContentType 'application/json' -Body '{"repo":"owner/repository","issue_number":1}'

# Ask the provider for configured-model status. This calls the provider's model-list API,
# not a completion endpoint, and should be skipped if you do not want any provider request.
Invoke-RestMethod -Uri http://127.0.0.1:8000/models/status

# Verify Docker availability before attempting an isolated candidate run.
docker version
docker image inspect devsquad-test-runner
```

Do not include tokens in these commands or paste them into a terminal transcript. Configure them only in the local `.env` file.

---

## 🔧 Troubleshooting & Known Limitations

- **Docker Connectivity:**
  - *Symptom:* `status: "TEST_ENVIRONMENT_ERROR"` with message `"Docker is not available"`.
  - *Fix:* Ensure Docker Desktop is started and the `devsquad-test-runner` image has been built (`docker build -t devsquad-test-runner -f Dockerfile.test .`).
- **LLM Truncation / Empty Responses:**
  - The Coder agent detects token-limit truncation (`finish_reason="length"`) and automatically retries with a focused prompt. If a task is very large, break the GitHub issue into smaller, modular sub-tasks.
- **Candidate Dependencies in Container:**
  - Standard library modules and `requests==2.32.3` are approved and available in the container. Network access is disabled in the container (`--network none`) for safety; tests must mock external network calls.
- **GitHub Rate Limits:**
  - Ensure `GITHUB_TOKEN` is configured to prevent unauthenticated GitHub API rate limits.
