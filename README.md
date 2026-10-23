# DevSquad — AI-Powered Software Development Team

DevSquad is an AI-powered, multi-agent software development system that helps developers solve GitHub issues through an organized workflow.

It uses specialized AI agents to plan tasks, generate code, create tests, review implementations, and prepare draft pull requests for human review.

## ðŸš€ Key Features

- **Planner Agent:** Analyzes GitHub issues and creates an implementation plan.
- **Coder Agent:** Generates Python code based on the task and implementation plan.
- **Tester Agent:** Generates tests for the proposed implementation.
- **Reviewer Agent:** Reviews code for correctness, task relevance, security, and quality.
- **Automated Test Execution:** Runs generated tests in an isolated test environment.
- **Retry Workflow:** Allows code improvements when review or test execution fails.
- **GitHub Integration:** Retrieves issues and repository information.
- **Draft Pull Requests:** Creates a branch, commits generated code, and opens a draft PR after the workflow passes its quality checks.
- **Human Oversight:** Generated changes remain subject to human review before merging.

## ðŸ› ï¸ Tech Stack

### Backend
- Python
- FastAPI
- Pydantic
- HTTPX
- Uvicorn

### AI and Agent Workflow
- LangGraph
- Groq
- Large Language Models
- Prompt Engineering

### Frontend
- React
- JavaScript
- Vite

### Testing and Development
- pytest
- pytest-asyncio
- Git and GitHub
- Docker

## ðŸ—ï¸ Architecture

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
                Generated Tests
                       |
             +---------+---------+
             |                   |
             v                   v
         Tests Pass          Tests Fail
             |                   |
             v                   v
        Quality Gate       Retry / Review
             |
             v
       Create Git Branch
             |
             v
        Commit Changes
             |
             v
       Draft Pull Request
             |
             v
       Human Review
```

The workflow only creates a draft pull request when the configured review and generated-test checks pass and a valid target path is provided. Failed checks do not automatically authorize repository changes.

## ðŸ“ Project Structure

```text
DevSquad/
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ agents/
â”‚   â”‚   â”œâ”€â”€ coder.py
â”‚   â”‚   â”œâ”€â”€ reviewer.py
â”‚   â”‚   â””â”€â”€ ...
â”‚   â”œâ”€â”€ github/
â”‚   â”œâ”€â”€ main.py
â”‚   â”œâ”€â”€ config.py
â”‚   â””â”€â”€ schemas.py
â”œâ”€â”€ frontend/
â”‚   â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ public/
â”‚   â”œâ”€â”€ package.json
â”‚   â””â”€â”€ vite.config.js
â”œâ”€â”€ docs/
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ pytest.ini
â”œâ”€â”€ test_workflow.py
â”œâ”€â”€ .env.example
â”œâ”€â”€ .gitignore
â””â”€â”€ README.md
```

*Note: The structure above highlights the main project components. Individual files may change as development continues.*

## âš™ï¸ Prerequisites

Install the following before running the project:

- Python
- Node.js and npm
- Git
- A GitHub personal access token with the permissions required for your repository
- A Groq API key

## ðŸ“¥ Installation

### 1. Clone the Repository

```bash
git clone https://github.com/om-3531/DevSquad.git
cd DevSquad
```

### 2. Create a Python Virtual Environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install Python Dependencies

```powershell
python -m pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create a `.env` file in the project root directory.

Use `.env.example` as a reference if it contains the required variable names. Configure the GitHub token, Groq API key, and any other settings required by the application.

**Important:** Never commit your `.env` file or expose API keys in source code, logs, screenshots, or public repositories.

### 5. Install Frontend Dependencies

```powershell
cd frontend
npm install
```

## â–¶ï¸ Running the Application

Open two terminal windows from the project root.

### Terminal 1: Start the Backend

Activate the virtual environment if necessary, then run:

```powershell
python -m uvicorn backend.main:app --reload
```

The backend should be available at:

- API root: `http://127.0.0.1:8000`
- Swagger documentation: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

### Terminal 2: Start the Frontend

```powershell
cd frontend
npm run dev
```

Open the local URL printed by Vite in your terminal.

## ðŸ”„ How the Workflow Works

1. Select a GitHub repository and issue.
2. The backend retrieves the issue details and relevant repository context.
3. The Planner Agent creates an implementation plan.
4. The Coder Agent generates the proposed code.
5. The Tester Agent generates tests.
6. The Reviewer Agent evaluates the implementation.
7. Generated tests run in an isolated test environment.
8. Failed reviews or tests can trigger a limited improvement-and-retry cycle.
9. If the configured quality checks pass and a target path is supplied, the backend can create a branch, commit the generated file, and open a draft pull request.
10. A human reviews the draft pull request before any merge.

**Safety:** Passing generated tests does not guarantee that code is correct or secure. Always inspect generated code and test results before approving a pull request.

## ðŸ§ª Testing

Run the backend test suite from the project root:

```powershell
python -m pytest -q
```

Run the workflow tests separately:

```powershell
python -m pytest test_workflow.py -v
```

Check the frontend code:

```powershell
cd frontend
npm run lint
npm run build
```

## ðŸ” Security and Limitations

- Keep API keys and tokens in environment variables.
- Review AI-generated code before merging.
- Treat generated tests as useful checks, not proof of correctness.
- Draft pull requests require human review.
- Do not use production credentials or sensitive data in test environments.
- Verify repository permissions and generated changes before approving a pull request.

## ðŸ‘¨â€ðŸ’» Author

**Om Nagare**
B.Tech — Computer Science and Engineering

## ðŸ“„ License

Add a license file before describing this project as open source under a specific license.
