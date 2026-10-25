import { useState } from 'react'
import './App.css'
import { classifyDiffLine } from './diffPreview.js'
import {
  approvalStatusLabel,
  canInitiateWrite,
  canRecordApproval,
  formatApprovalError,
  formatWriteError,
} from './proposalApproval.js'

const API = 'http://127.0.0.1:8000'

function App() {
  const [repo, setRepo] = useState('om-3531/DevSquad')
  const [issue, setIssue] = useState('1')
  const [targetPath, setTargetPath] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [rateLimitInfo, setRateLimitInfo] = useState(null)
  const [approvalConfirmed, setApprovalConfirmed] = useState(false)
  const [approvalMessage, setApprovalMessage] = useState('')
  const [approvalError, setApprovalError] = useState('')
  const [approvalLoading, setApprovalLoading] = useState(false)
  const [writeLoading, setWriteLoading] = useState(false)
  const [writeMessage, setWriteMessage] = useState('')
  const [writeError, setWriteError] = useState('')


  async function runWorkflow(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    setRateLimitInfo(null)
    setApprovalConfirmed(false)
    setApprovalMessage('')
    setApprovalError('')
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
        if (response.status === 429) {
          const retryHeader = response.headers.get('retry-after')
          setRateLimitInfo({
            message: data.detail || 'AI rate limit exceeded.',
            retryAfter: retryHeader ? Number(retryHeader) : null,
          })
          throw new Error(data.detail || 'AI model rate limit exceeded. Please wait before retrying.')
        }
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

  async function approveProposal() {
    const proposal = result?.proposal
    if (!proposal || !canRecordApproval(proposal.status, approvalConfirmed)) return

    setApprovalLoading(true)
    setApprovalError('')
    setApprovalMessage('')
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/proposals/${proposal.proposal_id}/approve`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ content_digest: proposal.content_digest }),
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(formatApprovalError(response.status, data.detail))
      }
      setResult((current) => ({ ...current, proposal: data.proposal }))
      setApprovalMessage(data.message)
      setApprovalConfirmed(false)
    } catch (err) {
      setApprovalError(err.message)
    } finally {
      setApprovalLoading(false)
    }
  }

  async function executeWriteWorkflow() {
    const proposal = result?.proposal
    if (!proposal || !canInitiateWrite(proposal.status)) return

    setWriteLoading(true)
    setWriteError('')
    setWriteMessage('')
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/proposals/${proposal.proposal_id}/write`, {
        method: 'POST',
        headers,
      })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(formatWriteError(response.status, data.detail))
      }
      setResult((current) => ({
        ...current,
        proposal: data.proposal,
        pull_request: data.pull_request,
        commit: data.commit,
        branch: data.commit?.branch || current?.branch,
        repository_modified: true,
      }))
      setWriteMessage(data.message || 'Approved proposal committed and draft PR created.')
    } catch (err) {
      setWriteError(err.message)
    } finally {
      setWriteLoading(false)
    }
  }


  const quality = result?.quality_gate?.status || 'WAITING'
  const passed = quality === 'REVIEW_APPROVED_GENERATED_TESTS_PASSED'
  const isFailed = quality === 'TEST_ENVIRONMENT_ERROR' || quality === 'GENERATED_TESTS_FAILED'
  const isChangesRequested = quality === 'REQUEST_CHANGES'

  function getStepState(index) {
    if (!result) return { className: '', label: null }
    if (index === 0) return { className: 'complete', label: '✓' }
    if (index === 1) return { className: 'complete', label: '✓' }
    if (index === 2) return { className: result.proposed_code ? 'complete' : 'failed', label: result.proposed_code ? '✓' : '✕' }
    if (index === 3) {
      if (passed) return { className: 'complete', label: '✓' }
      if (isFailed) return { className: 'failed', label: '✕' }
      if (isChangesRequested) return { className: 'failed', label: '✕' }
      return { className: 'warning', label: '!' }
    }
    if (index === 4) {
      if (result.repository_modified && result.pull_request) return { className: 'complete', label: '✓' }
      return { className: '', label: null }
    }
    return { className: '', label: null }
  }

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
            <p className="subtitle">From GitHub issue to reviewed code and a verified dry-run candidate.</p>
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
            <h3>
              {passed
                ? 'Passed'
                : isFailed
                  ? (quality === 'TEST_ENVIRONMENT_ERROR' ? 'Env Error' : 'Tests Failed')
                  : isChangesRequested
                    ? 'Changes Requested'
                    : result
                      ? 'Needs Review'
                      : 'Waiting'}
            </h3>
            <small>Reviewer and generated tests</small>
          </article>
          <article className="stat-card">
            <span className="stat-icon blue">⑂</span>
            <p>Pull request</p>
            <h3>{result?.pull_request ? `#${result.pull_request.number}` : 'Not created'}</h3>
            <small>Writes require separate authorization</small>
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
                  <label htmlFor="target">Target Python file (optional)</label>
                  <input
                    id="target"
                    value={targetPath}
                    onChange={(e) => setTargetPath(e.target.value)}
                    placeholder="Auto-detect or e.g. src/solution.py"
                  />
                </div>
              </div>

              <p className="field-help">
                Specify an existing file to modify or a new file to create. Leave blank to let AI auto-detect from repository files.
              </p>

              <button className="run-button" type="submit" disabled={loading}>
                {loading ? <><span className="spinner" /> Running workflow...</> : '▶  Run AI workflow'}
              </button>
            </form>

            {rateLimitInfo ? (
              <div className="rate-limit-box">
                <div className="rate-limit-header">
                  <span className="rate-limit-icon">⏳</span>
                  <strong>AI Rate Limit Reached</strong>
                </div>
                <p className="rate-limit-message">{rateLimitInfo.message}</p>
                {rateLimitInfo.retryAfter && (
                  <div className="retry-countdown-badge">
                    <span>⏱ Recommended retry in ~{rateLimitInfo.retryAfter}s</span>
                  </div>
                )}
              </div>
            ) : error ? (
              <div className="error-box">{error}</div>
            ) : null}
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
                ['05', 'Write preparation', 'Dry-run only; GitHub writes stay disabled'],
              ].map(([number, title, description], index) => {
                const step = getStepState(index)
                return (
                  <div className="pipeline-step" key={number}>
                    <div className={`pipeline-number ${step.className}`}>
                      {step.label || number}
                    </div>
                    <div>
                      <strong>{title}</strong>
                      <p>{description}</p>
                    </div>
                  </div>
                )
              })}
            </div>
            <p className="pipeline-note">A successful workflow does not create GitHub changes automatically.</p>
          </article>
        </section>

        <section className="panel results-panel" id="results">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">EXECUTION OUTPUT</p>
              <h2>Workflow results</h2>
            </div>
            {result && (
              <span
                className={`result-badge ${
                  passed
                    ? 'success'
                    : isFailed || isChangesRequested
                      ? 'danger'
                      : 'warning'
                }`}
              >
                {quality.replaceAll('_', ' ')}
              </span>
            )}
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

              {result.files_changed?.length > 0 && (
                <>
                  <h3>Proposed Changes</h3>
                  <div className="files-list">
                    {result.files_changed.map((file, idx) => (
                      <div className="file-item" key={idx}>
                        <span>{file.path}</span>
                        <span className={`op-badge ${file.operation?.toLowerCase()}`}>
                          {file.operation?.toUpperCase()}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {result.acceptance_criteria?.length > 0 && (
                <>
                  <h3>Acceptance Criteria</h3>
                  <ul className="criteria-list">
                    {result.acceptance_criteria.map((item, idx) => (
                      <li className="criteria-item" key={idx}>
                        <span className="criteria-icon">✓</span>
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <h3>Reviewer feedback</h3>
              <pre className="output-block">{result.review || 'No review feedback returned.'}</pre>

              <h3>Generated code</h3>
              <pre className="code-block"><code>{result.proposed_code || 'No code returned.'}</code></pre>

              <h3>Diff preview</h3>
              {result.diff_preview ? (
                <section className="diff-preview" aria-label="Proposed file diff">
                  <div className="diff-preview-header">
                    <strong>{result.diff_preview.path}</strong>
                    <span className={`op-badge ${result.diff_preview.operation?.toLowerCase()}`}>
                      {result.diff_preview.operation?.toUpperCase()}
                    </span>
                  </div>
                  {!result.diff_preview.reliable ? (
                    <p className="diff-unavailable">
                      A reliable diff is unavailable: {result.diff_preview.original_content_unavailable_reason}
                    </p>
                  ) : result.diff_preview.changed ? (
                    <pre className="diff-code" aria-label={`Unified diff for ${result.diff_preview.path}`}>
                      {result.diff_preview.diff.split('\n').map((line, index) => (
                        <span className={`diff-line ${classifyDiffLine(line)}`} key={`${index}-${line}`}>
                          {line}{'\n'}
                        </span>
                      ))}
                    </pre>
                  ) : (
                    <p className="diff-unchanged">The proposed content matches the original file; no change is proposed.</p>
                  )}
                </section>
              ) : (
                <p className="diff-unchanged">No target file was selected, so a file diff is unavailable.</p>
              )}

              {result.proposal && (
                <section className="approval-panel" aria-labelledby="approval-title">
                  <div className="approval-heading">
                    <div>
                      <h3 id="approval-title">Human approval</h3>
                      <p>Proposal <code>{result.proposal.proposal_id}</code></p>
                    </div>
                    <span className={`approval-status ${result.proposal.status.toLowerCase()}`}>
                      {approvalStatusLabel(result.proposal.status)}
                    </span>
                  </div>
                  <dl className="proposal-details">
                    <div><dt>Repository</dt><dd>{result.proposal.repository}</dd></div>
                    <div><dt>Target branch</dt><dd>{result.proposal.target_branch}</dd></div>
                    <div><dt>Target file</dt><dd>{result.proposal.target_path || 'No file resolved'}</dd></div>
                  </dl>
                  {result.proposal.status === 'PENDING_REVIEW' ? (
                    <>
                      <div className="auth-token-field">
                        <label htmlFor="auth-token">Approver Bearer Token</label>
                        <input
                          id="auth-token"
                          type="password"
                          value={authToken}
                          onChange={(e) => setAuthToken(e.target.value)}
                          placeholder="Paste your assigned approver token"
                          autoComplete="off"
                        />
                        <small className="field-hint">
                          Requires a token provisioned with the <code>proposal_approver</code> role in <code>APPROVAL_AUTH_TOKENS</code>.
                        </small>
                      </div>
                      <label className="approval-confirmation">
                        <input
                          type="checkbox"
                          checked={approvalConfirmed}
                          onChange={(event) => setApprovalConfirmed(event.target.checked)}
                        />
                        <span>I reviewed this exact diff and approve this dry-run proposal.</span>
                      </label>
                      <button className="approval-button" type="button" disabled={!canRecordApproval(result.proposal.status, approvalConfirmed) || approvalLoading} onClick={approveProposal}>
                        {approvalLoading ? 'Recording approval…' : 'Record approval'}
                      </button>
                    </>
                  ) : result.proposal.status === 'APPROVED' ? (
                    <div className="write-section">
                      <p className="approval-message">✓ Approval recorded. Candidate is eligible for GitHub commit and draft PR.</p>
                      <div className="write-disabled-notice">
                        <span>🛡️ GitHub Writes Disabled</span>
                        <small><code>GITHUB_WRITES_ENABLED=false</code>: Write operations fail closed safely.</small>
                      </div>
                      <button
                        className="write-button"
                        type="button"
                        disabled={writeLoading}
                        onClick={executeWriteWorkflow}
                      >
                        {writeLoading ? 'Executing write pipeline…' : 'Create GitHub Branch, Commit & Draft PR'}
                      </button>
                    </div>
                  ) : result.proposal.status === 'PR_CREATED' ? (
                    <div className="write-success-box">
                      <p className="approval-message">✓ Pull request created on GitHub!</p>
                      {result.proposal.pr_url && (
                        <p>
                          <a className="pr-link" href={result.proposal.pr_url} target="_blank" rel="noreferrer">
                            Open Draft Pull Request #{result.proposal.pr_number} ↗
                          </a>
                        </p>
                      )}
                      {result.proposal.commit_sha && (
                        <p className="branch-info">Commit: <code>{result.proposal.commit_sha.substring(0, 7)}</code></p>
                      )}
                    </div>
                  ) : result.proposal.status === 'WRITE_FAILED' ? (
                    <div className="write-section">
                      <p className="approval-error">Write workflow halted.</p>
                      {result.proposal.commit_sha && (
                        <p className="field-hint">
                          Commit <code>{result.proposal.commit_sha.substring(0, 7)}</code> was recorded. Retrying will create the draft PR without duplicating the commit.
                        </p>
                      )}
                      <button
                        className="write-button retry"
                        type="button"
                        disabled={writeLoading}
                        onClick={executeWriteWorkflow}
                      >
                        {writeLoading ? 'Retrying…' : 'Retry Draft PR Creation'}
                      </button>
                    </div>
                  ) : result.proposal.status === 'STALE' ? (
                    <p className="approval-error">Proposal is stale: remote repository revisions changed after review. Please run a new workflow.</p>
                  ) : (
                    <p className="approval-error">This proposal is not eligible for approval. Run a new verified dry-run workflow if changes are needed.</p>
                  )}
                  {approvalMessage && <p className="approval-message">{approvalMessage}</p>}
                  {approvalError && <p className="approval-error" role="alert">{approvalError}</p>}
                  {writeMessage && <p className="approval-message">{writeMessage}</p>}
                  {writeError && <p className="approval-error" role="alert">{writeError}</p>}
                </section>
              )}


              <h3>Test summary</h3>
              <pre className="output-block">{result.test_execution?.summary || 'No test summary returned.'}</pre>

              {result.write_simulation && (
                <div className="rate-limit-box">
                  <strong>GitHub write simulation</strong>
                  <p>{result.write_simulation.reason}</p>
                  <p>Would create: {result.write_simulation.would_create.join(', ')}.</p>
                </div>
              )}

              {result.pull_request?.html_url && (
                <a className="pr-link" href={result.pull_request.html_url} target="_blank" rel="noreferrer">
                  Open draft pull request ↗
                </a>
              )}

              {result.branch && <p className="branch-info">Branch: <code>{result.branch}</code></p>}
              {result.commit?.commit_sha && (
                <p className="branch-info">Commit: <code>{result.commit.commit_sha.substring(0, 7)}</code></p>
              )}
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
