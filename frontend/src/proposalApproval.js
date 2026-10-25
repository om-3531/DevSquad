export function canRecordApproval(status, confirmed) {
  return status === 'PENDING_REVIEW' && confirmed
}

export function approvalStatusLabel(status) {
  return (status || 'UNKNOWN').replaceAll('_', ' ')
}

export function formatApprovalError(status, detail) {
  if (status === 401) {
    return 'Authentication required (401): Please provide a valid Bearer token provisioned with the "proposal_approver" role.'
  }
  if (status === 403) {
    return 'Permission denied (403): Your authenticated token does not have the "proposal_approver" role.'
  }
  if (status === 429) {
    return 'Rate limit exceeded (429): Too many approval attempts. Please wait before retrying.'
  }
  if (status === 503) {
    return 'Service unavailable (503): Proposal approval authentication is not configured on the backend.'
  }
  return detail || 'Approval could not be recorded.'
}
export function canInitiateWrite(status) {
  return status === 'APPROVED' || status === 'WRITE_FAILED'
}

export function formatWriteError(status, detail) {
  if (status === 503) {
    return 'GitHub writes are disabled on this backend (GITHUB_WRITES_ENABLED=false). Real repository modifications remain safely locked.'
  }
  if (status === 401) {
    return 'Authentication required (401): Please enter a valid Bearer token with approver permission.'
  }
  if (status === 403) {
    return 'Permission denied (403): Your token does not have the "proposal_approver" role.'
  }
  if (status === 409) {
    return detail || 'Proposal is stale or not eligible for writing. Run a new dry-run workflow.'
  }
  if (status === 429) {
    return 'Rate limit exceeded (429): Too many write attempts. Please wait before retrying.'
  }
  if (status === 502) {
    return 'GitHub write workflow failed safely. Check server logs.'
  }
  return detail || 'GitHub write operation could not be completed.'
}
