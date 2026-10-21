import { useState } from 'react'
import './App.css'

const API = 'http://127.0.0.1:8000'

function App() {
  const [repo, setRepo] = useState('om-3531/DevSquad')
  const [issue, setIssue] = useState('1')
  const [targetPath, setTargetPath] = useState('src/solution.py')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function runWorkflow(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    setResult(null)

    try {
      const response = await fetch(`${API}/agents/workflow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo: repo.trim(),
          issue_number: Number(issue),
          target_path: targetPath.trim() || null,
        }),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || 'Workflow request failed.')
      }

      setResult(data)
    } catch (err) {
      setError(
        err.message.includes('Failed to fetch')
          ? 'Cannot connect to backend. Start FastAPI on port 8000 and check CORS settings.'
          : err.message
      )
    } finally {
      setLoading(false)
    }
  }

  const quality = result?.quality_gate?.status || 'WAITING'
  const passed = quality === 'REVIEW_APPROVED_GENERATED_TESTS_PASSED'

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">DS</div>
          <div>
            <h2>DevSquad</h2>
            <p>AI Engineering Team</p>
          </div>
        </div>

        <p className="nav-label">WORKSPACE</p>
        <a className="nav-item active" href="#dashboard">▦ <span>Dashboard</span></a>
        <a className="nav-item" href="#workflow">⌘ <span>AI Workflow</span></a>
        <a className="nav-item" href="#results">◷ <span>Run Results</span></a>

        <div className="sidebar-bottom">
          <div className="status-dot" />
          <div>
            <strong>DevSquad API</strong>
            <p>Configure at localhost:8000</p>
          </div>
        </div>
      </aside>

      <main className="main-content" id="dashboard">
        <header className="topbar">
          <div>
            <p className="eyebrow">ENGINEERING WORKSPACE</p>
            <h1>AI Development Dashboard</h1>
            <p className="subtitle">From GitHub issue to reviewed code and a draft pull request.</p>
          </div>
          <span className="local-badge"><span /> Local workspace</span>
        </header>

        <section className="stats-grid">
          <article className="stat-card">
            <span className="stat-icon purple">⌘</span>
            <p>Workflow</p>
            <h3>{result ? 'Completed' : 'Ready to run'}</h3>
            <small>AI planning and code generation</small>
          </article>
          <article className="stat-card">
            <span className="stat-icon green">✓</span>
            <p>Quality gate</p>
            <h3>{passed ? 'Passed' : result ? 'Needs review' : 'Waiting'}</h3>
            <small>Reviewer and generated tests</small>
          </article>
          <article className="stat-card">
            <span className="stat-icon blue">⑂</span>
            <p>Pull request</p>
            <h3>{result?.pull_request ? `#${result.pull_request.number}` : 'Not created'}</h3>
            <small>Draft PR, never auto-merged</small>
          </article>
        </section>

        <section className="workspace-grid">
          <article className="panel workflow-panel" id="workflow">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">AUTOMATION</p>
                <h2>Run an AI workflow</h2>
              </div>
              <span className="step-pill">01 / INPUT</span>
            </div>

            <form onSubmit={runWorkflow}>
              <label htmlFor="repo">GitHub repository</label>
              <input
                id="repo"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="owner/repository"
                required
                pattern="[^/]+/[^/]+"
                title="Enter repository as owner/repository"
              />

              <div className="form-row">
                <div>
                  <label htmlFor="issue">Issue number</label>
                  <input
                    id="issue"
                    type="number"
                    min="1"
                    value={issue}
                    onChange={(e) => setIssue(e.target.value)}
                    required
                  />
                </div>
                <div>
                  <label htmlFor="target">New Python file path</label>
                  <input
                    id="target"
                    value={targetPath}
                    onChange={(e) => setTargetPath(e.target.value)}
                    placeholder="src/solution.py"
                  />
                </div>
              </div>

              <p className="field-help">
                Use a new .py file path. Leave it blank to run without creating a PR.
              </p>

              <button className="run-button" type="submit" disabled={loading}>
                {loading ? <><span className="spinner" /> Running workflow...</> : '▶  Run AI workflow'}
              </button>
            </form>

            {error && <div className="error-box">{error}</div>}
          </article>

          <article className="panel pipeline-panel">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">PROCESS</p>
                <h2>Workflow pipeline</h2>
              </div>
              <span className="step-pill">5 STAGES</span>
            </div>

            <div className="pipeline">
              {[
                ['01', 'Fetch issue', 'Read issue and repository context'],
                ['02', 'Plan solution', 'Create an implementation plan'],
                ['03', 'Generate code', 'AI coder proposes a solution'],
                ['04', 'Review & test', 'Reviewer and isolated tests'],
                ['05', 'Draft pull request', 'Branch, commit and draft PR'],
              ].map(([number, title, description], index) => (
                <div className="pipeline-step" key={number}>
                  <div className={`pipeline-number ${result && index < 4 ? 'complete' : ''}`}>
                    {result && index < 4 ? '✓' : number}
                  </div>
                  <div>
                    <strong>{title}</strong>
                    <p>{description}</p>
                  </div>
                </div>
              ))}
            </div>
            <p className="pipeline-note">A successful workflow does not merge code automatically.</p>
          </article>
        </section>

        <section className="panel results-panel" id="results">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">EXECUTION OUTPUT</p>
              <h2>Workflow results</h2>
            </div>
            {result && <span className={`result-badge ${passed ? 'success' : 'warning'}`}>{quality.replaceAll('_', ' ')}</span>}
          </div>

          {!result && !error && (
            <div className="empty-state">
              <div className="empty-icon">⌘</div>
              <h3>Ready when you are</h3>
              <p>Enter a GitHub issue and run the workflow to see generated code, review feedback and test results here.</p>
            </div>
          )}

          {result && (
            <div className="result-content">
              <div className="result-summary">
                <div>
                  <small>ISSUE</small>
                  <strong>#{result.issue_number} · {result.issue_title}</strong>
                </div>
                <div>
                  <small>TEST STATUS</small>
                  <strong>{result.test_execution?.status || result.testing_status}</strong>
                </div>
                <div>
                  <small>REVIEW RETRIES</small>
                  <strong>{result.correction_attempts} / {result.max_correction_attempts}</strong>
                </div>
              </div>

              <h3>Reviewer feedback</h3>
              <pre className="output-block">{result.review || 'No review feedback returned.'}</pre>

              <h3>Generated code</h3>
              <pre className="code-block"><code>{result.proposed_code || 'No code returned.'}</code></pre>

              <h3>Test summary</h3>
              <pre className="output-block">{result.test_execution?.summary || 'No test summary returned.'}</pre>

              {result.pull_request?.html_url && (
                <a className="pr-link" href={result.pull_request.html_url} target="_blank" rel="noreferrer">
                  Open draft pull request ↗
                </a>
              )}

              {result.branch && <p className="branch-info">Branch: <code>{result.branch}</code></p>}
              {result.quality_gate?.next_action && <p className="next-action">{result.quality_gate.next_action}</p>}
            </div>
          )}
        </section>

        <footer>DevSquad · AI-assisted development with human review</footer>
      </main>
    </div>
  )
}

export default App