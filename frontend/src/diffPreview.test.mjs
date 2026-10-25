import assert from 'node:assert/strict'
import test from 'node:test'
import { classifyDiffLine } from './diffPreview.js'

test('classifies additions, removals, metadata, and unchanged lines', () => {
  assert.equal(classifyDiffLine('+added'), 'addition')
  assert.equal(classifyDiffLine('-removed'), 'removal')
  assert.equal(classifyDiffLine('@@ -1 +1 @@'), 'meta')
  assert.equal(classifyDiffLine(' unchanged'), 'context')
})
