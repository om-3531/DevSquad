import assert from 'node:assert/strict'
import test from 'node:test'
import {
  calculateSuccessRate,
  formatDuration,
  formatTimestamp,
  getAgentBadgeClass,
  getStatusBadgeClass,
  buildWorkflowQueryParams,
  filterWorkflowsBySearch,
  getConfidenceBadgeClass,
  isRetryableWorkflow,
} from './workflowMonitoring.js'

test('getStatusBadgeClass maps statuses to styling classes', () => {
  assert.equal(getStatusBadgeClass('SUCCESS'), 'badge-success')
  assert.equal(getStatusBadgeClass('FAILED'), 'badge-failed')
  assert.equal(getStatusBadgeClass('RUNNING'), 'badge-running')
  assert.equal(getStatusBadgeClass('PENDING'), 'badge-pending')
  assert.equal(getStatusBadgeClass('CANCELLED'), 'badge-cancelled')
  assert.equal(getStatusBadgeClass('OTHER'), 'badge-neutral')
})

test('getAgentBadgeClass maps agent statuses', () => {
  assert.equal(getAgentBadgeClass('COMPLETED'), 'agent-completed')
  assert.equal(getAgentBadgeClass('RUNNING'), 'agent-running')
  assert.equal(getAgentBadgeClass('FAILED'), 'agent-failed')
  assert.equal(getAgentBadgeClass('SKIPPED'), 'agent-skipped')
  assert.equal(getAgentBadgeClass('PENDING'), 'agent-pending')
})

test('formatDuration formats milliseconds, seconds, and minutes correctly', () => {
  assert.equal(formatDuration(null), '—')
  assert.equal(formatDuration(undefined), '—')
  assert.equal(formatDuration(0.4), '400ms')
  assert.equal(formatDuration(12.34), '12.3s')
  assert.equal(formatDuration(65), '1m 5s')
  assert.equal(formatDuration(120), '2m 0s')
})

test('formatTimestamp formats valid ISO strings and handles invalid or empty inputs', () => {
  assert.equal(formatTimestamp(null), '—')
  assert.equal(formatTimestamp(''), '—')
  assert.equal(formatTimestamp('invalid-date'), 'invalid-date')
  const formatted = formatTimestamp('2026-03-15T14:30:00Z')
  assert.ok(typeof formatted === 'string' && formatted.length > 0)
  assert.ok(formatted.includes('15') || formatted.includes('Mar'))
})

test('calculateSuccessRate computes percentages cleanly', () => {
  assert.equal(calculateSuccessRate(0, 0), 0)
  assert.equal(calculateSuccessRate(3, 4), 75.0)
  assert.equal(calculateSuccessRate(1, 3), 33.3)
})

test('buildWorkflowQueryParams builds URL params correctly', () => {
  const query = buildWorkflowQueryParams({
    limit: 10,
    offset: 20,
    status: 'SUCCESS',
    repository: 'om-3531/DevSquad',
  })
  assert.match(query, /limit=10/)
  assert.match(query, /offset=20/)
  assert.match(query, /status=SUCCESS/)
  assert.match(query, /repository=om-3531%2FDevSquad/)

  const defaultQuery = buildWorkflowQueryParams({ status: 'ALL' })
  assert.ok(!defaultQuery.includes('status='))
})

test('filterWorkflowsBySearch matches on workflow ID, repo, issue, or stage', () => {
  const list = [
    { workflow_id: 'wf-111', repository: 'owner/repo1', issue_number: 42, stage: 'COMPLETED' },
    { workflow_id: 'wf-222', repository: 'owner/repo2', issue_number: 99, stage: 'FAILED' },
  ]
  assert.equal(filterWorkflowsBySearch(list, '111').length, 1)
  assert.equal(filterWorkflowsBySearch(list, 'repo2').length, 1)
  assert.equal(filterWorkflowsBySearch(list, '42').length, 1)
  assert.equal(filterWorkflowsBySearch(list, 'failed').length, 1)
  assert.equal(filterWorkflowsBySearch(list, 'nonexistent').length, 0)
  assert.equal(filterWorkflowsBySearch(list, '').length, 2)
})

test('getConfidenceBadgeClass maps confidence levels to styling classes', () => {
  assert.equal(getConfidenceBadgeClass('HIGH'), 'confidence-high')
  assert.equal(getConfidenceBadgeClass('MEDIUM'), 'confidence-medium')
  assert.equal(getConfidenceBadgeClass('LOW'), 'confidence-low')
  assert.equal(getConfidenceBadgeClass(''), 'confidence-neutral')
})

test('isRetryableWorkflow identifies eligible failed workflows', () => {
  assert.equal(isRetryableWorkflow({ status: 'SUCCESS' }), false)
  assert.equal(isRetryableWorkflow({ status: 'RUNNING' }), false)
  assert.equal(isRetryableWorkflow({ status: 'FAILED', error_summary: 'Docker timeout' }), true)
  assert.equal(isRetryableWorkflow({ status: 'FAILED', error_summary: '401: GitHub authentication failed' }), false)
  assert.equal(isRetryableWorkflow(null), false)
})

