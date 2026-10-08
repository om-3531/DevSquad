# DevSquad - Learning Notes

This file contains the important concepts I learn while building DevSquad.

---

## 1. Agentic AI

Agentic AI refers to AI systems that can understand a goal, decide the next steps, use tools, and perform multiple actions to complete a task.

In DevSquad, different AI agents will perform different software development tasks.

Example:

```text
User Goal
   ↓
Planner
   ↓
Coder
   ↓
Tester
   ↓
Reviewer

2. AI Agent
An AI agent is a system that can:
- Understand a task.
- Reason about the task.
- Decide what action to take.
- Use available tools.
- Observe the result.
- Continue the workflow.
DevSquad will use specialized agents instead of one agent doing everything.
3. Planner Agent
The Planner Agent converts a high-level software issue into smaller development tasks.
Example:
Issue:
Add login functionality.

Planner:

1. Create login endpoint.
2. Validate user input.
3. Check credentials.
4. Generate authentication token.
5. Add tests.
6. Run tests.

4. Coder Agent
The Coder Agent uses the development plan and repository context to generate or modify source code.
It should:
- Understand existing code.
- Identify relevant files.
- Make controlled changes.
- Explain the changes.
5. Tester Agent
The Tester Agent checks whether the generated code works correctly.
It can:
- Run existing tests.
- Generate new tests.
- Detect failures.
- Analyze error messages.
- Send failures back to the Coder Agent.
6. Reviewer Agent
The Reviewer Agent checks the final code.
It looks for:
- Bugs.
- Security problems.
- Poor code quality.
- Missing tests.
- Bad error handling.
- Possible improvements.
7. Multi-Agent System
A multi-agent system uses multiple specialized agents that work together.
In DevSquad:
Planner Agent
      ↓
Coder Agent
      ↓
Tester Agent
      ↓
Reviewer Agent

Each agent has a specific responsibility.
8. LLM
LLM stands for Large Language Model.
An LLM will provide the reasoning and language/code generation capabilities required by DevSquad agents.
Possible LLM providers include:
- OpenAI
- Google Gemini
- Groq
- Local open-source models
9. Prompt Engineering
Prompt engineering means designing clear instructions for an AI model.
A good prompt should provide:
- Role.
- Task.
- Context.
- Rules.
- Expected output format.
Example:
You are a software development planning agent.

Read the GitHub issue and create a structured
development plan.

Return:
1. Required changes
2. Files to modify
3. Tests required
4. Possible risks

10. Tool Calling
Tool calling allows an AI agent to interact with external systems.
For DevSquad, tools may allow agents to:
- Read GitHub issues.
- Read repository files.
- Create branches.
- Modify files.
- Run tests.
- Create commits.
- Create Pull Requests.
The LLM decides when a tool is required, while the application controls what the tool is allowed to do.
11. GitHub API
The GitHub API allows applications to interact with GitHub programmatically.
DevSquad will use it to:
- Read repositories.
- Read issues.
- Read files.
- Create branches.
- Create commits.
- Create Pull Requests.
12. LangGraph
LangGraph is used to build stateful workflows involving AI agents.
DevSquad can use LangGraph to control:
Planner
   ↓
Coder
   ↓
Tester
   ↓
Tests Passed?
   ├── No → Coder
   └── Yes → Reviewer

It helps manage the state and flow between agents.
13. FastAPI
FastAPI is a Python framework for building APIs.
In DevSquad, FastAPI will act as the backend.
The frontend will communicate with FastAPI using HTTP requests.
Example:
React Frontend
      ↓
HTTP Request
      ↓
FastAPI
      ↓
Agent Workflow

14. Docker
Docker provides isolated environments called containers.
DevSquad will use Docker to safely run AI-generated code and tests.
Basic idea:
AI Generated Code
       ↓
Docker Container
       ↓
Run Tests
       ↓
Test Result

Generated code should not be directly executed on the host machine.
15. pytest
pytest is a Python testing framework.
DevSquad can use pytest to:
- Run tests.
- Detect failures.
- Capture errors.
- Verify code changes.
Example:
pytest

16. Git
Git is a version control system.
DevSquad will use Git for:
- Branches.
- Commits.
- Code history.
- Tracking changes.
Example:
main
  |
  └── devsquad/issue-25

AI-generated changes should be made on a separate branch instead of directly modifying the main branch.
17. Pull Request
A Pull Request allows developers to review proposed code changes before merging them.
DevSquad will eventually:
Issue
  ↓
AI Development
  ↓
Tests
  ↓
Code Review
  ↓
Pull Request
  ↓
Human Review
  ↓
Merge

The final merge should remain under human control.
18. Human-in-the-Loop
Human-in-the-loop means that humans remain involved in important decisions.
DevSquad should not automatically merge every AI-generated change.
The developer should review the Pull Request before merging.
This makes the system safer and more controllable.
19. RAG vs DevSquad
RAG is mainly used to retrieve relevant information from a knowledge source before generating an answer.
DevSquad is primarily an Agentic AI workflow that uses tools and multiple agents to perform software development tasks.
RAG may be added in the future if DevSquad needs advanced codebase retrieval.
20. Important Security Concepts
While building DevSquad, I need to understand:
- API key protection.
- GitHub token protection.
- Environment variables.
- Input validation.
- Docker isolation.
- Permission management.
- Safe code execution.
- Rate limiting.
- Logging without exposing secrets.
21. Important Learning Rule
I should understand each technology before using it in the project.
The goal is not only to make the project work.
The goal is to understand:
What am I using?
Why am I using it?
How does it work?
What problem does it solve?
What are its limitations?

22. Future Learning Topics
During development, I will add notes about:
- LangGraph State Management
- Agent Memory
- Tool Calling
- Structured LLM Output
- GitHub API Authentication
- Codebase Analysis
- Automated Test Generation
- Docker Security
- Agent Evaluation
- LLM Cost Optimization
- Error Handling
- Human-in-the-Loop
- Production Deployment
