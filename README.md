# DevSquad — AI-Powered Software Development Team

DevSquad is an Agentic AI-based software development system that uses multiple AI agents to assist developers in solving GitHub issues.

Instead of asking one AI to perform the complete software development task, DevSquad divides the work among specialized AI agents such as Planner, Coder, Tester, and Reviewer.

The main goal is to make software development more structured, automated, and reliable.

---

## 🚀 Project Workflow

```text
GitHub Issue
     ↓
Planner Agent
     ↓
Coder Agent
     ↓
Tester Agent
     ↓
Reviewer Agent
     ↓
Human Approval
     ↓
Pull Request

If the tests fail, the workflow can return to the Coder Agent for improvement.
Coder
  ↓
Tester
  ↓
Tests Failed
  ↓
Coder
  ↓
Tester

🤖 AI Agents
Agent	Responsibility
Planner Agent	Understands the GitHub issue and creates an implementation plan
Coder Agent	Generates or modifies code based on the plan
Tester Agent	Runs tests and checks whether the implementation works
Reviewer Agent	Reviews code quality, security, and correctness
Human	Approves the final changes before creating a Pull Request


🧠 Why Multi-Agent AI?
A single AI agent may try to solve planning, coding, testing, and reviewing at the same time.
DevSquad separates these responsibilities into different agents.
This provides:
- Better task organization
- Separation of responsibilities
- Automated testing
- Code review
- Error feedback loops
- Human approval before final changes
🏗️ Architecture
The current architecture is:
                    GitHub
                       │
                       ▼
                GitHub Issue
                       │
                       ▼
                FastAPI Backend
                       │
                       ▼
                LangGraph Workflow
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     Planner         Coder          Tester
        │              │              │
        └──────────────┴──────────────┘
                       │
                       ▼
                    Reviewer
                       │
                       ▼
                Human Approval
                       │
                       ▼
                 Pull Request

🛠️ Tech Stack
Backend
- Python
- FastAPI
- Pydantic
- HTTPX
AI / Agent Framework
- LangGraph
- Large Language Models
- Prompt Engineering
Development Tools
- Git
- GitHub
- pytest
- Docker
Frontend
Planned:
- React
- JavaScript
- HTML
- CSS
📂 Project Structure
DevSquad/
│
├── backend/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py
│   ├── schemas.py
│   │
│   └── github/
│       ├── __init__.py
│       ├── client.py
│       └── issues.py
│
├── docs/
│   ├── API_PLAN.md
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── DAILY_PROGRESS.md
│   ├── LEARNING_NOTES.md
│   └── PROJECT_PLAN.md
│
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md

⚙️ Current Backend
The project currently contains a FastAPI backend.
Available endpoints:
GET  /
GET  /health
POST /github/issue

Health Check
GET /health

Example response:
{
  "status": "healthy"
}

GitHub Issue
POST /github/issue

Example request:
{
  "repo": "om-3531/DevSquad",
  "issue_number": 1
}

This endpoint retrieves a GitHub issue through the GitHub API.
▶️ How to Run
1. Clone the repository
git clone https://github.com/om-3531/DevSquad.git
cd DevSquad

2. Install dependencies
python -m pip install -r requirements.txt

3. Start the backend
python -m uvicorn backend.main:app --reload

4. Open the API
http://127.0.0.1:8000

5. Open Swagger Documentation
http://127.0.0.1:8000/docs

🔐 Security
DevSquad will use environment variables for sensitive information such as:
- GitHub access tokens
- LLM API keys
- Other secrets
Sensitive .env files must never be committed to GitHub.
Only .env.example should be included in the repository.
📚 Documentation
Detailed project documentation is available in the docs directory.
- [Project Plan](docs/PROJECT_PLAN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API Plan](docs/API_PLAN.md)
- [Daily Progress](docs/DAILY_PROGRESS.md)
- [Learning Notes](docs/LEARNING_NOTES.md)
- [Changelog](docs/CHANGELOG.md)
📈 Development Status
Completed
- GitHub repository setup
- Project documentation
- FastAPI backend setup
- Backend configuration
- Pydantic schemas
- GitHub API client
- GitHub issue service
- GitHub issue API endpoint
- Health check endpoint
- Local backend testing
In Progress
- Agent architecture
- Planner Agent
- Coder Agent
- Tester Agent
- Reviewer Agent
- LangGraph workflow
Planned
- Docker sandbox
- Automated testing
- Pull Request creation
- Human approval workflow
- React dashboard
- Complete end-to-end agentic workflow
🎯 Future Goals
The final goal of DevSquad is to provide an AI-assisted software development workflow where a developer can submit a GitHub issue and receive a tested and reviewed implementation through a controlled multi-agent process.
Future versions may include:
- Automatic branch creation
- Automatic code generation
- Docker-based code execution
- Automated test generation
- Code quality analysis
- Pull Request generation
- Human-in-the-loop approval
- Web dashboard
- Workflow monitoring
- Multiple LLM provider support
👨‍💻 Author
Om Nagare
B.Tech Computer Science and Engineering
⚠️ Project Safety
DevSquad is designed as a development assistance system.
Generated code should be reviewed and tested before being merged into production systems.
Human approval is intentionally included in the workflow.
