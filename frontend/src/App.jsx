import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { classifyDiffLine } from './diffPreview.js'
import {
  approvalStatusLabel,
  canInitiateWrite,
  canRecordApproval,
  formatApprovalError,
  formatWriteError,
} from './proposalApproval.js'
import {
  WORKFLOW_STATUSES,
  getStatusBadgeClass,
  getAgentBadgeClass,
  getConfidenceBadgeClass,
  isRetryableWorkflow,
  formatDuration,
  formatTimestamp,
  buildWorkflowQueryParams,
  filterWorkflowsBySearch,
} from './workflowMonitoring.js'

const API = 'http://127.0.0.1:8000'

function App() {
  const [repo, setRepo] = useState('om-3531/DevSquad')
  const [issue, setIssue] = useState('1')
  const [targetPath, setTargetPath] = useState('')
  const [authToken, setAuthToken] = useState(() => {
    try {
      return localStorage.getItem('devsquad_auth_token') || ''
    } catch {
      return ''
    }
  })
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

  function updateAuthToken(val) {
    setAuthToken(val)
    try {
      localStorage.setItem('devsquad_auth_token', val)
    } catch {
      // Ignore localStorage write errors
    }
  }

  // Workflow execution history & monitoring state
  const [workflows, setWorkflows] = useState([])
  const [workflowTotal, setWorkflowTotal] = useState(0)
  const [workflowStats, setWorkflowStats] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [repoFilter, setRepoFilter] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [historyLimit] = useState(10)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [selectedWorkflow, setSelectedWorkflow] = useState(null)
  const [selectedEvents, setSelectedEvents] = useState([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const [eventsError, setEventsError] = useState('')
  const [diagnosis, setDiagnosis] = useState(null)
  const [diagnoseLoading, setDiagnoseLoading] = useState(false)
  const [diagnoseError, setDiagnoseError] = useState('')
  const [retryLoading, setRetryLoading] = useState(false)
  const [retryError, setRetryError] = useState('')
  const [retrySuccessMsg, setRetrySuccessMsg] = useState('')
  const [fixProposalLoading, setFixProposalLoading] = useState(false)
  const [fixProposalError, setFixProposalError] = useState('')
  const [fixProposalSuccessMsg, setFixProposalSuccessMsg] = useState('')

  const fetchWorkflowStats = useCallback(async () => {
    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/stats`, { headers })
      if (!response.ok) {
        const body = await response.json().catch(() => ({}))
        if (response.status === 401) {
          setHistoryError('Authentication required: Enter an authorized Bearer token.')
        } else if (response.status === 503) {
          setHistoryError(body.detail || 'Workflow authentication is not configured on the backend.')
        }
        return
      }
      const data = await response.json()
      if (data.status === 'success') {
        setWorkflowStats(data.stats)
      }
    } catch {
      // Ignore background network fetch errors
    }
  }, [authToken])

  const fetchWorkflows = useCallback(async (customOffset = historyOffset) => {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const query = buildWorkflowQueryParams({
        limit: historyLimit,
        offset: customOffset,
        status: statusFilter,
        repository: repoFilter,
      })
      const response = await fetch(`${API}/workflows?${query}`, { headers })
      const data = await response.json()
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Authentication required: Enter an authorized Bearer token.')
        }
        if (response.status === 503) {
          throw new Error(data.detail || 'Workflow authentication is not configured on the backend.')
        }
        throw new Error(data.detail || 'Failed to retrieve workflow history.')
      }
      setWorkflows(data.workflows || [])
      setWorkflowTotal(data.total || 0)
    } catch (err) {
      setHistoryError(err.message)
    } finally {
      setHistoryLoading(false)
    }
  }, [authToken, historyLimit, historyOffset, statusFilter, repoFilter])

  useEffect(() => {
    let ignore = false
    async function loadInitialMonitoring() {
      try {
        const headers = {}
        if (authToken.trim()) {
          headers['Authorization'] = `Bearer ${authToken.trim()}`
        }
        const [statsRes, wfRes] = await Promise.allSettled([
          fetch(`${API}/workflows/stats`, { headers }),
          fetch(
            `${API}/workflows?${buildWorkflowQueryParams({
              limit: historyLimit,
              offset: historyOffset,
              status: statusFilter,
              repository: repoFilter,
            })}`,
            { headers }
          ),
        ])
        if (ignore) return
        if (statsRes.status === 'fulfilled' && statsRes.value.ok) {
          const statsData = await statsRes.value.json()
          if (statsData.status === 'success') {
            setWorkflowStats(statsData.stats)
          }
        }
        if (wfRes.status === 'fulfilled') {
          const wfData = await wfRes.value.json()
          if (wfRes.value.ok) {
            setWorkflows(wfData.workflows || [])
            setWorkflowTotal(wfData.total || 0)
            setHistoryError('')
          } else if (wfRes.value.status === 401) {
            setHistoryError('Authentication required: Enter an authorized Bearer token.')
          } else if (wfRes.value.status === 503) {
            setHistoryError(wfData.detail || 'Workflow authentication is not configured on the backend.')
          } else {
            setHistoryError(wfData.detail || 'Failed to retrieve workflow history.')
          }
        }
      } catch (err) {
        if (!ignore) setHistoryError(err.message)
      }
    }

    loadInitialMonitoring()
    return () => {
      ignore = true
    }
  }, [authToken, historyLimit, historyOffset, statusFilter, repoFilter])

  async function inspectWorkflow(wf) {
    setSelectedWorkflow(wf)
    setEventsLoading(true)
    setEventsError('')
    setSelectedEvents([])
    let existingDiagnosis = null
    if (wf.diagnosis) {
      existingDiagnosis = wf.diagnosis
    } else if (wf.diagnosis_json) {
      try {
        existingDiagnosis = typeof wf.diagnosis_json === 'string' ? JSON.parse(wf.diagnosis_json) : wf.diagnosis_json
      } catch {
        existingDiagnosis = null
      }
    }
    setDiagnosis(existingDiagnosis)
    setDiagnoseError('')
    setRetryError('')
    setRetrySuccessMsg('')
    setFixProposalError('')
    setFixProposalSuccessMsg('')

    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/${wf.workflow_id}/events`, { headers })
      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'Failed to retrieve workflow events.')
      }
      setSelectedEvents(data.events || [])
    } catch (err) {
      setEventsError(err.message)
    } finally {
      setEventsLoading(false)
    }
  }

  async function inspectWorkflowById(workflowId) {
    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/${workflowId}`, { headers })
      if (response.ok) {
        const data = await response.json()
        if (data.workflow) {
          inspectWorkflow(data.workflow)
        }
      }
    } catch {
      // Ignore background fetch error
    }
  }

  async function handleAnalyzeFailure(forceRefresh = false) {
    if (!selectedWorkflow) return
    setDiagnoseLoading(true)
    setDiagnoseError('')
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/${selectedWorkflow.workflow_id}/diagnose`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ force_refresh: forceRefresh }),
      })
      const data = await response.json()
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Authentication required: Enter an authorized Bearer token.')
        }
        throw new Error(data.detail || 'Diagnosis request failed.')
      }
      setDiagnosis(data.diagnosis)
      setSelectedWorkflow((prev) => (prev ? { ...prev, diagnosis: data.diagnosis } : null))
      setWorkflows((prev) =>
        prev.map((w) => (w.workflow_id === selectedWorkflow.workflow_id ? { ...w, diagnosis: data.diagnosis } : w))
      )
    } catch (err) {
      setDiagnoseError(err.message)
    } finally {
      setDiagnoseLoading(false)
    }
  }

  async function handleRetryWorkflow() {
    if (!selectedWorkflow || retryLoading) return
    setRetryLoading(true)
    setRetryError('')
    setRetrySuccessMsg('')
    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/${selectedWorkflow.workflow_id}/retry`, {
        method: 'POST',
        headers,
      })
      const data = await response.json()
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Authentication required: Enter an authorized Bearer token.')
        }
        throw new Error(data.detail || 'Workflow retry request failed.')
      }
      setRetrySuccessMsg(`Safe retry initiated! New execution ID: ${data.workflow_id}`)
      fetchWorkflowStats()
      fetchWorkflows(0)
    } catch (err) {
      setRetryError(err.message)
    } finally {
      setRetryLoading(false)
    }
  }

  async function handleCreateFixProposal() {
    if (!selectedWorkflow || fixProposalLoading) return
    setFixProposalLoading(true)
    setFixProposalError('')
    setFixProposalSuccessMsg('')
    try {
      const headers = {}
      if (authToken.trim()) {
        headers['Authorization'] = `Bearer ${authToken.trim()}`
      }
      const response = await fetch(`${API}/workflows/${selectedWorkflow.workflow_id}/fix-proposal`, {
        method: 'POST',
        headers,
      })
      const data = await response.json()
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error('Authentication required: Enter an authorized Bearer token.')
        }
        throw new Error(data.detail || 'Failed to create candidate fix proposal.')
      }
      setFixProposalSuccessMsg(
        `Proposal candidate ${data.proposal_id} created with status ${data.proposal?.status || 'PENDING_REVIEW'}. Explicit human review and approval is required before applying.`
      )
      if (data.proposal && data.diff_preview) {
        setResult({
          issue_number: selectedWorkflow.issue_number,
          issue_title: `Diagnostic fix for workflow ${selectedWorkflow.workflow_id.substring(0, 8)}`,
          proposed_code: data.proposal.proposed_content,
          diff_preview: data.diff_preview,
          proposal: data.proposal,
          proposal_id: data.proposal.proposal_id,
          workflow_id: selectedWorkflow.workflow_id,
          test_execution: { status: 'DIAGNOSTIC_FIX_CANDIDATE' },
          correction_attempts: 0,
          max_correction_attempts: 0,
          files_changed: [{ path: data.proposal.target_path, operation: data.proposal.operation || 'MODIFY' }],
          review: `Candidate fix generated from AI failure diagnosis:\n${diagnosis?.summary || ''}\nRoot cause: ${diagnosis?.probable_root_cause || ''}`,
        })
      }
    } catch (err) {
      setFixProposalError(err.message)
    } finally {
      setFixProposalLoading(false)
    }
  }

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
      fetchWorkflowStats()
      fetchWorkflows(0)
    } catch (err) {
      setError(
        err.message.includes('Failed to fetch')
          ? 'Cannot connect to backend. Start FastAPI on port 8000 and check CORS settings.'
          : err.message
      )
      fetchWorkflowStats()
      fetchWorkflows(0)
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
        <a className="nav-item" href="#history">⏱ <span>Workflow History</span></a>

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
                {result.workflow_id && (
                  <div>
                    <small>WORKFLOW RUN ID</small>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
                      <code style={{ fontSize: '11px', background: '#f1f5f9', padding: '2px 6px', borderRadius: '4px' }}>
                        {result.workflow_id.substring(0, 8)}…
                      </code>
                      <button
                        type="button"
                        className="view-events-btn"
                        style={{ padding: '3px 8px', fontSize: '11px' }}
                        onClick={() => {
                          const target = workflows.find((w) => w.workflow_id === result.workflow_id) || {
                            workflow_id: result.workflow_id,
                            repository: repo,
                            issue_number: Number(issue),
                            status: result.quality_gate?.approved ? 'SUCCESS' : 'FAILED',
                            stage: 'COMPLETED',
                            planner_status: 'COMPLETED',
                            coder_status: 'COMPLETED',
                            tester_status: result.quality_gate?.approved ? 'COMPLETED' : 'FAILED',
                            reviewer_status: 'COMPLETED',
                            duration_seconds: null,
                            started_at: new Date().toISOString(),
                          }
                          inspectWorkflow(target)
                          document.getElementById('history')?.scrollIntoView({ behavior: 'smooth' })
                        }}
                      >
                        Inspect ↗
                      </button>
                    </div>
                  </div>
                )}
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

        <section className="panel history-panel" id="history">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">OBSERVABILITY & AUDIT</p>
              <h2>Workflow Execution History</h2>
              <p className="subtitle">Track executions, agent stages, durations, and diagnostic event streams.</p>
            </div>
            <div className="history-header-actions">
              <button
                type="button"
                className="refresh-btn"
                onClick={() => {
                  fetchWorkflowStats()
                  fetchWorkflows(historyOffset)
                }}
                disabled={historyLoading}
                title="Refresh workflow history"
              >
                {historyLoading ? <span className="spinner" /> : '↻ Refresh'}
              </button>
            </div>
          </div>

          <div className="history-stats-grid">
            <article className="stat-card mini">
              <p>Total Executions</p>
              <h3>{workflowStats ? workflowStats.total_executions : '—'}</h3>
              <small>All logged runs</small>
            </article>
            <article className="stat-card mini">
              <p>Success Rate</p>
              <h3>{workflowStats ? `${workflowStats.success_rate}%` : '—'}</h3>
              <small>{workflowStats ? `${workflowStats.successful_executions} passed` : 'Calculating'}</small>
            </article>
            <article className="stat-card mini">
              <p>Average Duration</p>
              <h3>{workflowStats ? formatDuration(workflowStats.average_duration_seconds) : '—'}</h3>
              <small>End-to-end time</small>
            </article>
            <article className="stat-card mini">
              <p>Active Runs</p>
              <h3>{workflowStats ? workflowStats.running_executions : '—'}</h3>
              <small>{workflowStats?.failed_executions || 0} failed</small>
            </article>
          </div>

          <div className="history-filters-bar">
            <div className="filter-group">
              <label htmlFor="status-filter">Status</label>
              <select
                id="status-filter"
                value={statusFilter}
                onChange={(e) => {
                  setStatusFilter(e.target.value)
                  setHistoryOffset(0)
                }}
              >
                {WORKFLOW_STATUSES.map((st) => (
                  <option key={st} value={st}>{st}</option>
                ))}
              </select>
            </div>

            <div className="filter-group">
              <label htmlFor="repo-filter">Repository Filter</label>
              <input
                id="repo-filter"
                placeholder="Filter by repo e.g. om-3531/DevSquad"
                value={repoFilter}
                onChange={(e) => {
                  setRepoFilter(e.target.value)
                  setHistoryOffset(0)
                }}
              />
            </div>

            <div className="filter-group search-group">
              <label htmlFor="history-search">Search In View</label>
              <input
                id="history-search"
                placeholder="Search ID, issue, stage..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            <div className="filter-group token-group">
              <label htmlFor="history-auth-token">Bearer Token</label>
              <div className="token-input-row">
                <input
                  id="history-auth-token"
                  type="password"
                  placeholder="Bearer token"
                  value={authToken}
                  onChange={(e) => updateAuthToken(e.target.value)}
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={() => {
                    fetchWorkflowStats()
                    fetchWorkflows(historyOffset)
                  }}
                  title="Apply Bearer token & refresh"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>

          {historyError && (
            <div className="error-box">
              <p>{historyError}</p>
              <div className="token-assist">
                <label htmlFor="assist-auth-token">Bearer Token:</label>
                <input
                  id="assist-auth-token"
                  type="password"
                  placeholder="Paste workflow_reader or approver token"
                  value={authToken}
                  onChange={(e) => updateAuthToken(e.target.value)}
                />
                <button
                  type="button"
                  className="approval-button inline-btn"
                  onClick={() => {
                    fetchWorkflowStats()
                    fetchWorkflows(historyOffset)
                  }}
                >
                  Authenticate
                </button>
              </div>
            </div>
          )}

          <div className="history-table-container">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Workflow ID</th>
                  <th>Target</th>
                  <th>Stage</th>
                  <th>Agents</th>
                  <th>Duration</th>
                  <th>Started</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {historyError ? (
                  <tr>
                    <td colSpan="8" className="empty-history-cell error-state-cell">
                      <div className="error-history-state">
                        <span className="error-icon">⚠️</span>
                        <div>
                          <strong>Failed to load execution history</strong>
                          <p>{historyError}</p>
                          <button
                            type="button"
                            className="refresh-btn inline-btn"
                            onClick={() => {
                              fetchWorkflowStats()
                              fetchWorkflows(historyOffset)
                            }}
                          >
                            ↻ Retry
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : historyLoading ? (
                  <tr>
                    <td colSpan="8" className="empty-history-cell">
                      <div className="loading-state"><span className="spinner" /> Loading execution history...</div>
                    </td>
                  </tr>
                ) : filterWorkflowsBySearch(workflows, searchQuery).length === 0 ? (
                  <tr>
                    <td colSpan="8" className="empty-history-cell">
                      <div className="no-history-state">
                        <p>No workflow executions found matching your active filters.</p>
                        <small>
                          Status: <strong>{statusFilter}</strong>
                          {repoFilter ? <> · Repository: <strong>{repoFilter}</strong></> : null}
                          {searchQuery ? <> · Search: <strong>{searchQuery}</strong></> : null}
                        </small>
                        {(statusFilter !== 'ALL' || repoFilter || searchQuery) && (
                          <div style={{ marginTop: '10px' }}>
                            <button
                              type="button"
                              className="clear-filters-btn"
                              onClick={() => {
                                setStatusFilter('ALL')
                                setRepoFilter('')
                                setSearchQuery('')
                                setHistoryOffset(0)
                              }}
                            >
                              Reset filters
                            </button>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                ) : (
                  filterWorkflowsBySearch(workflows, searchQuery).map((wf) => {
                    const isSelected = selectedWorkflow?.workflow_id === wf.workflow_id
                    return (
                      <tr
                        key={wf.workflow_id}
                        className={isSelected ? 'selected-row' : ''}
                      >
                        <td>
                          <span className={`status-badge ${getStatusBadgeClass(wf.status)}`}>
                            {wf.status}
                          </span>
                        </td>
                        <td>
                          <code className="wf-id" title={wf.workflow_id}>
                            {wf.workflow_id.length > 12 ? `${wf.workflow_id.substring(0, 10)}…` : wf.workflow_id}
                          </code>
                          <div className="wf-badges-row">
                            {wf.retry_of_workflow_id && (
                              <span className="tag-retry" title={`Retried from ${wf.retry_of_workflow_id}`}>
                                🔁 Retry
                              </span>
                            )}
                            {(wf.diagnosis || wf.diagnosis_json) && (
                              <span className="tag-diagnosed" title="AI Diagnosis available">
                                🧠 Diagnosed
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div className="wf-target">
                            <span>{wf.repository}</span>
                            {wf.issue_number && <small>Issue #{wf.issue_number}</small>}
                          </div>
                        </td>
                        <td>
                          <span className="stage-pill">{wf.stage}</span>
                        </td>
                        <td>
                          <div className="agent-indicators" title={`Planner: ${wf.planner_status}, Coder: ${wf.coder_status}, Tester: ${wf.tester_status}, Reviewer: ${wf.reviewer_status}`}>
                            <span className={`agent-chip ${getAgentBadgeClass(wf.planner_status)}`}>P</span>
                            <span className={`agent-chip ${getAgentBadgeClass(wf.coder_status)}`}>C</span>
                            <span className={`agent-chip ${getAgentBadgeClass(wf.tester_status)}`}>T</span>
                            <span className={`agent-chip ${getAgentBadgeClass(wf.reviewer_status)}`}>R</span>
                          </div>
                        </td>
                        <td>{formatDuration(wf.duration_seconds)}</td>
                        <td><small>{formatTimestamp(wf.started_at)}</small></td>
                        <td>
                          <button
                            type="button"
                            className="view-events-btn"
                            onClick={() => inspectWorkflow(wf)}
                          >
                            {isSelected ? 'Viewing' : 'Inspect'}
                          </button>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="history-pagination">
            <span>
              Showing {filterWorkflowsBySearch(workflows, searchQuery).length} of {workflowTotal} runs (Page {Math.floor(historyOffset / historyLimit) + 1} of {Math.max(1, Math.ceil(workflowTotal / historyLimit))})
            </span>
            <div className="pagination-buttons">
              <button
                type="button"
                disabled={historyOffset <= 0 || historyLoading}
                onClick={() => {
                  const nextOffset = Math.max(0, historyOffset - historyLimit)
                  setHistoryOffset(nextOffset)
                  fetchWorkflows(nextOffset)
                }}
              >
                ← Previous
              </button>
              <button
                type="button"
                disabled={historyOffset + historyLimit >= workflowTotal || historyLoading}
                onClick={() => {
                  const nextOffset = historyOffset + historyLimit
                  setHistoryOffset(nextOffset)
                  fetchWorkflows(nextOffset)
                }}
              >
                Next →
              </button>
            </div>
          </div>

          {selectedWorkflow && (
            <div className="workflow-inspector-panel">
              <div className="inspector-header">
                <div>
                  <p className="eyebrow">EXECUTION DETAILS</p>
                  <h3>Workflow <code>{selectedWorkflow.workflow_id}</code></h3>
                  {selectedWorkflow.retry_of_workflow_id && (
                    <div className="inspector-linkage-note">
                      <span>🔁 Retried from: </span>
                      <button
                        type="button"
                        className="link-btn"
                        onClick={() => inspectWorkflowById(selectedWorkflow.retry_of_workflow_id)}
                      >
                        <code>{selectedWorkflow.retry_of_workflow_id}</code> ↗
                      </button>
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className="close-inspector-btn"
                  onClick={() => setSelectedWorkflow(null)}
                  title="Close inspector"
                >
                  ✕ Close
                </button>
              </div>

              <div className="inspector-summary-bar">
                <div>
                  <small>STATUS</small>
                  <span className={`status-badge ${getStatusBadgeClass(selectedWorkflow.status)}`}>
                    {selectedWorkflow.status}
                  </span>
                </div>
                <div>
                  <small>STAGE</small>
                  <strong>{selectedWorkflow.stage}</strong>
                </div>
                <div>
                  <small>DURATION</small>
                  <strong>{formatDuration(selectedWorkflow.duration_seconds)}</strong>
                </div>
                <div>
                  <small>STARTED</small>
                  <span>{formatTimestamp(selectedWorkflow.started_at)}</span>
                </div>
                {selectedWorkflow.completed_at && (
                  <div>
                    <small>COMPLETED</small>
                    <span>{formatTimestamp(selectedWorkflow.completed_at)}</span>
                  </div>
                )}
                {selectedWorkflow.proposal_id && (
                  <div>
                    <small>PROPOSAL</small>
                    <code>{selectedWorkflow.proposal_id}</code>
                  </div>
                )}
              </div>

              <div className="inspector-agents-bar">
                <span className="agents-bar-label">Agent Statuses:</span>
                <div className="agent-detail-badge">
                  <span>Planner:</span>
                  <span className={`agent-chip ${getAgentBadgeClass(selectedWorkflow.planner_status)}`}>
                    {selectedWorkflow.planner_status}
                  </span>
                </div>
                <div className="agent-detail-badge">
                  <span>Coder:</span>
                  <span className={`agent-chip ${getAgentBadgeClass(selectedWorkflow.coder_status)}`}>
                    {selectedWorkflow.coder_status}
                  </span>
                </div>
                <div className="agent-detail-badge">
                  <span>Tester:</span>
                  <span className={`agent-chip ${getAgentBadgeClass(selectedWorkflow.tester_status)}`}>
                    {selectedWorkflow.tester_status}
                  </span>
                </div>
                <div className="agent-detail-badge">
                  <span>Reviewer:</span>
                  <span className={`agent-chip ${getAgentBadgeClass(selectedWorkflow.reviewer_status)}`}>
                    {selectedWorkflow.reviewer_status}
                  </span>
                </div>
              </div>

              {/* ACTION CONTROLS FOR FAILED WORKFLOWS */}
              {selectedWorkflow.status === 'FAILED' && (
                <div className="inspector-actions-bar">
                  <div className="inspector-actions-left">
                    <button
                      type="button"
                      className="btn-diagnose"
                      disabled={diagnoseLoading}
                      onClick={() => handleAnalyzeFailure(Boolean(diagnosis))}
                      title="Analyze failure events, logs, and stage errors with AI"
                    >
                      {diagnoseLoading ? (
                        <>
                          <span className="spinner mini" /> Analyzing failure…
                        </>
                      ) : diagnosis ? (
                        '🔄 Re-analyze Failure'
                      ) : (
                        '🧠 Analyze Failure'
                      )}
                    </button>

                    <button
                      type="button"
                      className="btn-retry"
                      disabled={!isRetryableWorkflow(selectedWorkflow) || retryLoading}
                      onClick={handleRetryWorkflow}
                      title={
                        !isRetryableWorkflow(selectedWorkflow)
                          ? 'This failure is non-retryable (e.g. authentication or credential error)'
                          : 'Safely retry this workflow execution'
                      }
                    >
                      {retryLoading ? (
                        <>
                          <span className="spinner mini" /> Retrying…
                        </>
                      ) : (
                        '🔁 Safe Retry Workflow'
                      )}
                    </button>
                  </div>

                  {!isRetryableWorkflow(selectedWorkflow) && (
                    <span className="non-retryable-note">
                      ⚠️ Non-retryable: Authentication / credential failures cannot be resolved by retry.
                    </span>
                  )}
                </div>
              )}

              {/* RETRY NOTICES */}
              {retrySuccessMsg && (
                <div className="success-banner retry-banner">
                  <span>✓ {retrySuccessMsg}</span>
                </div>
              )}
              {retryError && (
                <div className="error-box retry-error">
                  <strong>Retry Failed:</strong> {retryError}
                </div>
              )}

              {/* DIAGNOSIS ERROR */}
              {diagnoseError && (
                <div className="error-box diagnose-error">
                  <strong>AI Diagnosis Error:</strong> {diagnoseError}
                </div>
              )}

              {/* AI DIAGNOSIS CARD */}
              {diagnosis && (
                <div className="diagnosis-card">
                  <div className="diagnosis-header">
                    <div className="diagnosis-title-group">
                      <h4>🧠 AI Failure Diagnosis</h4>
                      <span className={`confidence-badge ${getConfidenceBadgeClass(diagnosis.confidence)}`}>
                        {diagnosis.confidence || 'UNKNOWN'} CONFIDENCE
                      </span>
                    </div>
                    <button
                      type="button"
                      className="refresh-diagnosis-btn"
                      disabled={diagnoseLoading}
                      onClick={() => handleAnalyzeFailure(true)}
                      title="Force refresh diagnosis"
                    >
                      ↻ Refresh
                    </button>
                  </div>

                  {diagnosis.confidence === 'LOW' && (
                    <div className="diagnosis-warning-box">
                      ⚠️ <strong>Low Confidence:</strong> Insufficient telemetry or error evidence was found in the
                      available audit logs. Additional diagnostics, logs, or manual review are recommended.
                    </div>
                  )}

                  <div className="diagnosis-summary-box">
                    <strong>Summary</strong>
                    <p>{diagnosis.summary}</p>
                  </div>

                  <div className="diagnosis-grid">
                    <div className="diagnosis-grid-item">
                      <small>FAILED WORKFLOW STAGE</small>
                      <strong className="failed-stage-pill">{diagnosis.failed_stage || 'UNKNOWN'}</strong>
                    </div>
                    <div className="diagnosis-grid-item">
                      <small>PROBABLE ROOT CAUSE</small>
                      <p className="root-cause-text">{diagnosis.probable_root_cause || 'Not identified'}</p>
                    </div>
                  </div>

                  {diagnosis.supporting_evidence?.length > 0 && (
                    <div className="diagnosis-evidence-box">
                      <small>SUPPORTING EVIDENCE</small>
                      <ul className="evidence-list">
                        {diagnosis.supporting_evidence.map((item, idx) => (
                          <li key={idx}>
                            <code>{item}</code>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {diagnosis.recommended_steps?.length > 0 && (
                    <div className="diagnosis-steps-box">
                      <small>RECOMMENDED TROUBLESHOOTING STEPS</small>
                      <ol className="steps-list">
                        {diagnosis.recommended_steps.map((step, idx) => (
                          <li key={idx}>{step}</li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {diagnosis.suggested_fix && (
                    <div className="diagnosis-fix-section">
                      <div className="fix-header">
                        <div>
                          <small>SUGGESTED CODE FIX (CANDIDATE)</small>
                          <p className="fix-subtitle">
                            Creates a dry-run candidate proposal requiring explicit human review and approval.
                          </p>
                        </div>
                        <button
                          type="button"
                          className="btn-create-fix-proposal"
                          disabled={fixProposalLoading}
                          onClick={handleCreateFixProposal}
                        >
                          {fixProposalLoading ? (
                            <>
                              <span className="spinner mini" /> Generating Proposal…
                            </>
                          ) : (
                            'Generate Proposal Candidate ↗'
                          )}
                        </button>
                      </div>

                      <pre className="code-block fix-code">
                        <code>{diagnosis.suggested_fix}</code>
                      </pre>

                      {fixProposalSuccessMsg && (
                        <div className="success-banner fix-success">
                          <span>✓ {fixProposalSuccessMsg}</span>
                          <button
                            type="button"
                            className="scroll-to-proposal-btn"
                            onClick={() => {
                              document.getElementById('proposal-title')?.scrollIntoView({ behavior: 'smooth' })
                            }}
                          >
                            Review & Approve Diff ↑
                          </button>
                        </div>
                      )}

                      {fixProposalError && (
                        <div className="error-box">
                          <strong>Fix Proposal Error:</strong> {fixProposalError}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {selectedWorkflow.error_summary && (
                <div className="error-box inspector-error">
                  <strong>Workflow Failure Diagnostic:</strong>
                  <p>{selectedWorkflow.error_summary}</p>
                </div>
              )}

              <h4>Audit Event Stream</h4>
              {eventsLoading ? (
                <div className="loading-state"><span className="spinner" /> Loading event stream...</div>
              ) : eventsError ? (
                <div className="error-box">{eventsError}</div>
              ) : selectedEvents.length === 0 ? (
                <p className="no-events-note">No discrete audit events recorded for this execution.</p>
              ) : (
                <div className="timeline-stream">
                  {selectedEvents.map((ev) => (
                    <div className="timeline-item" key={ev.event_id}>
                      <div className="timeline-marker" />
                      <div className="timeline-content">
                        <div className="timeline-meta">
                          <span className="timeline-type">{ev.event_type}</span>
                          <span className="stage-pill mini">{ev.stage}</span>
                          <span className="timeline-time">{formatTimestamp(ev.timestamp)}</span>
                        </div>
                        <p className="timeline-message">{ev.message}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>

        <footer>DevSquad · AI-assisted development with human review</footer>
      </main>
    </div>
  )
}

export default App
