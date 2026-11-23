const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')
const draftSource = readFileSync(join(__dirname, '../static/comment-draft.js'), 'utf8')
function draftBrowser(records = new Map(), storage = new Map(), clock = {now: Date.now()}) {
  const context = {
    sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key)},
    crypto: {randomUUID: require('node:crypto').randomUUID},
    Date: {now: () => clock.now}, setTimeout, clearTimeout,
    indexedDB: {open() {
      const request = {}
      setImmediate(() => {
        request.result = {close() {}, transaction() {
          const transaction = {}
          let pending = 0
          const schedule = callback => {
            pending++
            setImmediate(() => {callback(); if (--pending === 0) transaction.oncomplete?.()})
          }
          transaction.objectStore = () => ({
            put(value, key) {schedule(() => records.set(key, structuredClone(value)))},
            delete(key) {schedule(() => records.delete(key))},
            get(key) {
              const read = {}
              schedule(() => {read.result = structuredClone(records.get(key)); read.onsuccess?.()})
              return read
            },
            openCursor() {
              const cursor = {}, keys = [...records.keys()]
              const next = () => schedule(() => {
                const key = keys.shift()
                cursor.result = key === undefined ? null : {value: records.get(key),
                  delete: () => schedule(() => records.delete(key)), continue: next}
                cursor.onsuccess?.()
              })
              next()
              return cursor
            }
          })
          return transaction
        }}
        request.onsuccess()
      })
      return request
    }}
  }
  runInNewContext(draftSource, context)
  return context
}

test('draft storage round-trips file contents, isolates tabs and articles, and expires abandoned drafts', async () => {
  const records = new Map(), clock = {now: Date.now()}
  const browser = draftBrowser(records, new Map(), clock)
  const draft = browser.commentDraftStore('one')
  const file = new Blob(['image bytes'], {type: 'image/png'})
  await draft.save('  Keep whitespace  ', [{file, format: 'image'}])
  const restored = await draft.load()
  assert.equal(restored.text, '  Keep whitespace  ')
  assert.equal(await restored.items[0].file.text(), 'image bytes')
  assert.equal(await browser.commentDraftStore('two').load(), null)
  assert.equal(await draftBrowser(records).commentDraftStore('one').load(), null)
  clock.now += 15 * 60 * 1000
  assert.equal(await draft.load(), null)
  assert.equal(records.size, 0)
  await draft.save('New draft', [])
  await draft.remove()
  assert.equal(await draft.load(), null)
  assert.equal(records.size, 0)
})

test('unavailable browser storage rejects saving instead of losing the guest input through login', async () => {
  const browser = draftBrowser()
  browser.indexedDB.open = () => {throw new Error('Storage unavailable')}
  await assert.rejects(browser.commentDraftStore('one').save('Draft', []), /Storage unavailable/)
  browser.sessionStorage.getItem = () => {throw new Error('Blocked')}
  await assert.rejects(browser.commentDraftStore('one').save('Draft', []), /Blocked/)
})
