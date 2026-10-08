# DevSquad - System Architecture

## 1. Overview

DevSquad is designed as a multi-agent AI software development system.

The system connects a user, GitHub, an LLM, multiple AI agents, a testing environment, and a Pull Request workflow.

The main architecture is:

```text
User
  |
  v
React Frontend
  |
  v
FastAPI Backend
  |
  v
LangGraph Agent Workflow
  |
  +----------------+
  |                |
  v                v
Planner Agent    GitHub Tools
  |
  v
Coder Agent
  |
  v
Tester Agent
  |
  +------ Tests Pass ------+
  |                        |
  | No                     | Yes
  v                        v
Coder Agent           Reviewer Agent
  |                        |
  +----> Tester             |
                           v
                    GitHub Pull Request
                           |
                           v
                     Human Approval

2. Main Components
DevSquad will contain the following major components:
2.1 Frontend
The frontend will provide a simple dashboard where the user can:
- Connect/select a GitHub repository.
- View GitHub issues.
- Select an issue.
- Start the DevSquad workflow.
- View the status of each AI agent.
- View logs.
- View test results.
- View the generated Pull Request.
Technology:
- React
- JavaScript
- HTML
- CSS
3. FastAPI Backend
The FastAPI backend will act as the main application server.
Responsibilities:
- Receive requests from the frontend.
- Communicate with GitHub.
- Start the agent workflow.
- Manage application state.
- Send agent results to the frontend.
- Handle errors.
- Manage authentication and secrets safely.
Basic flow:
React
  |
  | HTTP Request
  v
FastAPI
  |
  v
Agent Workflow

4. Agent Orchestration
DevSquad will use LangGraph to manage the AI agent workflow.
LangGraph will be responsible for:
- Managing agent state.
- Controlling the order of agents.
- Passing information between agents.
- Handling conditional decisions.
- Managing retry loops.
- Stopping the workflow when required.
Example:
Planner
   |
   v
Coder
   |
   v
Tester
   |
   +---- Failed ----> Coder
   |
   +---- Passed ----> Reviewer
                         |
                         v
                    Pull Request

5. Planner Agent
The Planner Agent is the first development agent.
Input:
GitHub Issue
Repository Context

Output:
Structured Development Plan

Example:
Issue:
Add user login API.

Plan:

1. Create login endpoint.
2. Validate email and password.
3. Check user credentials.
4. Generate authentication token.
5. Add unit tests.
6. Run tests.

6. Coder Agent
The Coder Agent uses the development plan and repository context to generate code changes.
Input:
Development Plan
Repository Files
Issue Description

Output:
Code Changes
Changed Files
Change Summary

The Coder Agent should not blindly modify the entire repository.
It should identify the required files and make controlled changes.
7. Tester Agent
The Tester Agent validates the code produced by the Coder Agent.
Responsibilities:
- Find existing tests.
- Generate tests when required.
- Run tests.
- Capture output.
- Detect failures.
- Return structured test results.
Example:
Test Result

Passed: 8
Failed: 1

Failed Test:
test_invalid_email

Error:
Invalid email format was accepted.

If tests fail, the workflow can send the failure information back to the Coder Agent.
8. Reviewer Agent
The Reviewer Agent reviews the final changes after the tests pass.
It checks:
- Code quality.
- Readability.
- Security.
- Error handling.
- Possible bugs.
- Test coverage.
- Best practices.
Example:
Code Review

Code Quality: Good
Security: Good
Tests: Good
Error Handling: Needs Improvement

Recommendation:
Add better validation for user input.

9. GitHub Integration
GitHub will be the main source-control platform for DevSquad.
The GitHub integration will handle:
- Repository information.
- Issues.
- Repository files.
- Branches.
- Commits.
- Pull Requests.
Expected workflow:
GitHub Issue
     |
     v
DevSquad
     |
     v
Create Branch
     |
     v
Apply Changes
     |
     v
Run Tests
     |
     v
Commit Changes
     |
     v
Create Pull Request

GitHub authentication will use a secure token stored in environment variables.
10. LLM Layer
The LLM will provide reasoning and code-generation capabilities to the agents.
The LLM may be used for:
- Issue understanding.
- Task planning.
- Code generation.
- Test generation.
- Error analysis.
- Code review.
Possible providers:
- OpenAI
- Google Gemini
- Groq
- Local open-source models
The exact provider will be selected during the implementation phase.
11. Docker Sandbox
AI-generated code should not be executed directly on the host machine.
Docker will provide an isolated environment for running generated code and tests.
Expected flow:
AI Generated Code
       |
       v
Docker Container
       |
       v
Run Tests
       |
       v
Capture Output
       |
       v
Destroy / Clean Container

The sandbox should have appropriate restrictions on:
- Execution time.
- Filesystem access.
- Network access.
- CPU usage.
- Memory usage.
12. Testing Layer
The initial testing system will use pytest for Python projects.
The testing system will:
1. Detect the project.
2. Identify test commands.
3. Run tests.
4. Capture output.
5. Identify failures.
6. Return structured results.
Example:
Test Suite
--------------------
Passed: 15
Failed: 2
Skipped: 1
--------------------
Status: FAILED

13. Application State
The agent workflow needs to maintain state throughout the development process.
Example state:
repository
issue
issue_description
repository_context
development_plan
files_to_change
code_changes
test_results
review_results
branch_name
commit_id
pull_request_url
workflow_status

This state allows agents to share information with each other.
14. Final Architecture
The complete system will follow this architecture:
                         USER
                           |
                           v
                   +---------------+
                   | React Frontend|
                   +-------+-------+
                           |
                           v
                   +---------------+
                   | FastAPI       |
                   | Backend       |
                   +-------+-------+
                           |
                           v
                   +---------------+
                   | LangGraph     |
                   | Workflow      |
                   +-------+-------+
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
          Planner       Coder         Tester
           Agent        Agent          Agent
             |             |             |
             +-------------+-------------+
                           |
                           v
                      Reviewer Agent
                           |
                           v
                    +--------------+
                    | GitHub API   |
                    +------+-------+
                           |
                           v
                    Pull Request
                           |
                           v
                    Human Approval

15. Security Architecture
Security is an important part of DevSquad.
The system must:
- Never expose API keys.
- Never commit secrets to GitHub.
- Store secrets in environment variables.
- Use restricted GitHub permissions where possible.
- Execute generated code inside a controlled environment.
- Limit Docker resources.
- Require human approval before important changes are merged.
16. Design Principle
DevSquad is not intended to completely replace software developers.
The goal is to build an AI development assistant that automates repetitive development tasks while keeping the developer in control.
Final workflow:
AI Automation
      +
Developer Review
      =
Safer Software Development

17. Future Improvements
Possible future features:
- Support for multiple programming languages.
- Automatic issue creation.
- Automatic bug detection.
- Codebase documentation generation.
- Code quality scoring.
- Security vulnerability scanning.
- GitHub Actions integration.
- Agent performance analytics.
- Human approval checkpoints.
- Support for multiple LLM providers.
