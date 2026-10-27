/**
 * Helper utilities for Workflow Monitoring and history formatting.
 */

export const WORKFLOW_STATUSES = [
  'ALL',
  'SUCCESS',
  'FAILED',
  'RUNNING',
  'PENDING',
  'CANCELLED',
]

export function getStatusBadgeClass(status) {
  switch ((status || '').toUpperCase()) {
    case 'SUCCESS':
      return 'badge-success'
    case 'FAILED':
      return 'badge-failed'
    case 'RUNNING':
      return 'badge-running'
    case 'PENDING':
      return 'badge-pending'
    case 'CANCELLED':
      return 'badge-cancelled'
    default:
      return 'badge-neutral'
  }
}

export function getAgentBadgeClass(status) {
  switch ((status || '').toUpperCase()) {
    case 'COMPLETED':
      return 'agent-completed'
    case 'RUNNING':
      return 'agent-running'
    case 'FAILED':
      return 'agent-failed'
    case 'SKIPPED':
      return 'agent-skipped'
    default:
      return 'agent-pending'
  }
}

export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || isNaN(seconds)) {
    return '—'
  }
  const s = Number(seconds)
  if (s < 1) {
    return `${Math.round(s * 1000)}ms`
  }
  if (s < 60) {
    return `${s.toFixed(1)}s`
  }
  const mins = Math.floor(s / 60)
  const remainingSecs = Math.round(s % 60)
  return `${mins}m ${remainingSecs}s`
}

export function formatTimestamp(isoString) {
  if (!isoString) return '—'
  try {
    const date = new Date(isoString)
    if (isNaN(date.getTime())) return isoString
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })
  } catch {
    return isoString
  }
}

export function calculateSuccessRate(successful, total) {
  if (!total || total <= 0) return 0
  return Number(((successful / total) * 100).toFixed(1))
}

export function buildWorkflowQueryParams({ limit = 20, offset = 0, status, repository }) {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  if (status && status !== 'ALL') {
    params.set('status', status.toUpperCase())
  }
  if (repository && repository.trim()) {
    params.set('repository', repository.trim())
  }
  return params.toString()
}

export function filterWorkflowsBySearch(workflows, query) {
  if (!query || !query.trim()) return workflows
  const q = query.trim().toLowerCase()
  return (workflows || []).filter((wf) => {
    const id = (wf.workflow_id || '').toLowerCase()
    const repo = (wf.repository || '').toLowerCase()
    const issue = wf.issue_number ? String(wf.issue_number) : ''
    const stage = (wf.stage || '').toLowerCase()
    return id.includes(q) || repo.includes(q) || issue.includes(q) || stage.includes(q)
  })
}

export function getConfidenceBadgeClass(confidence) {
  switch ((confidence || '').toUpperCase()) {
    case 'HIGH':
      return 'confidence-high'
    case 'MEDIUM':
      return 'confidence-medium'
    case 'LOW':
      return 'confidence-low'
    default:
      return 'confidence-neutral'
  }
}

export function isRetryableWorkflow(workflow) {
  if (!workflow || workflow.status !== 'FAILED') return false
  const err = (workflow.error_summary || '').toLowerCase()
  if (err.includes('401') || err.includes('authentication failed') || err.includes('bad credentials')) {
    return false
  }
  return true
}

