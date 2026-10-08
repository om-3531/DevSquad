# DevSquad - Project Plan

## 1. Project Overview

DevSquad is an AI-powered multi-agent software development system.

The main goal of DevSquad is to automate important parts of the software development process. It will start with a GitHub issue and use different AI agents to understand the problem, plan the work, write code, test the code, review the changes, and finally create a Pull Request.

### Basic Workflow

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
GitHub Pull Request

---

## 2. Problem Statement

Software developers spend a lot of time performing repetitive development tasks such as:

- Understanding GitHub issues
- Planning implementation steps
- Finding the correct files in a project
- Writing code
- Creating tests
- Running tests
- Fixing errors
- Reviewing code
- Creating Pull Requests

DevSquad aims to automate these repetitive tasks using multiple specialized AI agents while keeping humans involved in the final decision and approval.

---

## 3. Main Objectives

The main objectives of DevSquad are:

1. Understand GitHub issues using AI.
2. Convert an issue into a structured development plan.
3. Analyze an existing code repository.
4. Generate or modify source code.
5. Generate and run automated tests.
6. Detect test failures.
7. Allow the coding agent to fix detected problems.
8. Review the generated code.
9. Create a GitHub Pull Request.
10. Keep humans involved before the final code is merged.

---

## 4. AI Agents

DevSquad will contain multiple specialized agents.

### 4.1 Planner Agent

The Planner Agent will:

- Read the GitHub issue.
- Understand the requested feature or bug fix.
- Analyze the project requirements.
- Break the problem into smaller tasks.
- Create a structured implementation plan.

Example:

Issue:

"Add a login API with email and password validation."

Planner output:

1. Create login endpoint.
2. Add request validation.
3. Check user credentials.
4. Generate authentication token.
5. Add unit tests.
6. Test invalid login cases.

---

### 4.2 Coder Agent

The Coder Agent will:

- Read the project files.
- Understand the existing code.
- Identify files that need modification.
- Write or modify code.
- Follow the plan created by the Planner Agent.
- Provide a summary of changes.

---

### 4.3 Tester Agent

The Tester Agent will:

- Identify available tests.
- Generate tests when required.
- Run tests in a safe environment.
- Capture test output.
- Identify failed tests.
- Send failure information back to the Coder Agent.

If tests fail, the workflow can return to the Coder Agent for correction.

---

### 4.4 Reviewer Agent

The Reviewer Agent will:

- Review the generated code.
- Check code quality.
- Check possible security problems.
- Check coding standards.
- Check whether tests are sufficient.
- Provide a review report.

---

## 5. Main Technology Stack

### Backend

- Python
- FastAPI

### AI / Agent System

- Large Language Model (LLM)
- LangGraph
- Prompt Engineering
- Tool Calling

### GitHub Integration

- GitHub REST API
- Git
- GitHub Issues
- GitHub Pull Requests

### Testing

- pytest

### Code Execution

- Docker

### Frontend

- React
- JavaScript
- HTML
- CSS

---

## 6. Development Phases

### Phase 0 - Project Setup

- Create GitHub repository.
- Create project documentation.
- Create project structure.
- Create README.
- Create development roadmap.

### Phase 1 - Environment Setup

- Install Python.
- Create virtual environment.
- Install FastAPI.
- Install LangGraph.
- Configure LLM API.
- Configure GitHub API.
- Install Docker.
- Install pytest.

### Phase 2 - GitHub Integration

- Connect DevSquad with GitHub.
- Read repository information.
- Read GitHub issues.
- Read repository files.
- Create branches.
- Create commits.
- Create Pull Requests.

### Phase 3 - Planner Agent

- Create Planner Agent.
- Create planner prompt.
- Read issue information.
- Generate structured development plans.
- Validate planner output.

### Phase 4 - Coder Agent

- Read repository files.
- Identify relevant files.
- Generate code changes.
- Apply changes.
- Generate change summary.

### Phase 5 - Tester Agent

- Detect project type.
- Run automated tests.
- Capture test results.
- Detect failures.
- Send failures to the Coder Agent.
- Implement a controlled retry/fix loop.

### Phase 6 - Reviewer Agent

- Review changed files.
- Check code quality.
- Check security.
- Check tests.
- Generate review report.

### Phase 7 - Pull Request Automation

- Create a development branch.
- Apply final changes.
- Commit changes.
- Push the branch.
- Create a Pull Request.
- Add development summary.
- Add test results.
- Add review information.

### Phase 8 - Web Dashboard

- Create React frontend.
- Add GitHub repository selection.
- Add issue selection.
- Show agent status.
- Show execution logs.
- Show test results.
- Show Pull Request information.

### Phase 9 - Security and Production Improvements

- Isolate generated code using Docker.
- Limit code execution time.
- Restrict filesystem access.
- Protect API keys and GitHub tokens.
- Add authentication.
- Add structured logging.
- Add error handling.
- Add human approval before merging.

---

## 7. Final Expected Workflow

The final system should work approximately like this:

User
  ↓
Select GitHub Repository
  ↓
Select GitHub Issue
  ↓
DevSquad starts
  ↓
Planner Agent
  ↓
Coder Agent
  ↓
Tester Agent
  ↓
Tests Pass?
  ├── No → Coder Agent → Tester Agent
  │
  └── Yes
       ↓
Reviewer Agent
       ↓
Create Pull Request
       ↓
Human Review
       ↓
Merge

---

## 8. Safety Principle

DevSquad should not directly execute AI-generated code on the developer's main computer.

Generated code should be executed inside a controlled Docker environment.

The system should also:

- Never expose API keys.
- Never commit secrets.
- Limit execution time.
- Limit resource usage.
- Require human approval before merging important changes.

---

## 9. Project Goal

The final goal is to build a practical Agentic AI software development assistant that can demonstrate:

- AI Agents
- LLM integration
- GitHub API integration
- Software engineering automation
- Automated testing
- Code review
- Docker-based code execution
- Human-in-the-loop development

---

## 10. Project Status

Current Status:

**Phase 0 - Project Setup**

Next Target:

**Phase 1 - Environment Setup**

The project will be developed step-by-step and daily progress will be recorded in:

`docs/DAILY_PROGRESS.md`
