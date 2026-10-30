import assert from 'node:assert/strict'
import test from 'node:test'
import {
  approvalStatusLabel,
  canInitiateWrite,
  canRecordApproval,
  canRecordRejection,
  formatApiDetail,
  formatApprovalError,
  formatWriteError,
} from './proposalApproval.js'

test('approval requires pending status and an explicit confirmation', () => {
  assert.equal(canRecordApproval('PENDING_REVIEW', false), false)
  assert.equal(canRecordApproval('PENDING_REVIEW', true), true)
  assert.equal(canRecordApproval('PENDING_REVIEW', true, ''), false)
  assert.equal(canRecordApproval('PENDING_REVIEW', true, '   '), false)
  assert.equal(canRecordApproval('PENDING_REVIEW', true, 'valid-token'), true)
  assert.equal(canRecordApproval('APPROVED', true), false)
  assert.equal(canRecordApproval('REJECTED', true), false)
  assert.equal(canRecordRejection('PENDING_REVIEW'), true)
  assert.equal(canRecordRejection('PENDING_REVIEW', ''), false)
  assert.equal(canRecordRejection('PENDING_REVIEW', 'valid-token'), true)
  assert.equal(canRecordRejection('APPROVED'), false)
  assert.equal(canRecordRejection('REJECTED'), false)
  assert.equal(approvalStatusLabel('PENDING_REVIEW'), 'PENDING REVIEW')
  assert.equal(approvalStatusLabel('NOT_ELIGIBLE'), 'NOT ELIGIBLE')
  assert.equal(approvalStatusLabel('REJECTED'), 'REJECTED')
})

test('formatApprovalError returns clear messages for HTTP auth and rate-limit statuses', () => {
  assert.match(formatApprovalError(401, ''), /Authentication required/)
  assert.match(formatApprovalError(403, ''), /Permission denied/)
  assert.match(formatApprovalError(409, 'Not eligible'), /Not eligible/)
  assert.match(formatApprovalError(410, ''), /expired/)
  assert.match(formatApprovalError(429, ''), /Rate limit exceeded/)
  assert.match(formatApprovalError(503, ''), /Service unavailable/)
  assert.equal(formatApprovalError(400, 'Custom failure message'), 'Custom failure message')
})


test('canInitiateWrite accepts APPROVED or WRITE_FAILED status', () => {
  assert.equal(canInitiateWrite('APPROVED'), true)
  assert.equal(canInitiateWrite('WRITE_FAILED'), true)
  assert.equal(canInitiateWrite('PENDING_REVIEW'), false)
  assert.equal(canInitiateWrite('PR_CREATED'), false)
})

test('formatApiDetail renders FastAPI validation arrays instead of [object Object]', () => {
  assert.equal(formatApiDetail('Plain error'), 'Plain error')
  assert.equal(
    formatApiDetail([{ loc: ['body', 'repo'], msg: 'repo must use the owner/repository format.' }]),
    'repo must use the owner/repository format.'
  )
  assert.equal(formatApiDetail([{ msg: 'first' }, { msg: 'second' }]), 'first; second')
  assert.equal(formatApiDetail(undefined), '')
})

test('formatWriteError returns clear safe messages for disabled writes and errors', () => {
  assert.match(formatWriteError(503, ''), /GitHub writes are disabled/)
  assert.match(formatWriteError(401, ''), /Authentication required/)
  assert.match(formatWriteError(403, ''), /Permission denied/)
  assert.match(formatWriteError(429, ''), /Rate limit exceeded/)
  assert.match(formatWriteError(502, ''), /GitHub write workflow failed safely/)
})


