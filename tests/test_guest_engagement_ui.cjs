const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')

const file = readFileSync(join(__dirname, '../static/scripts.js'), 'utf8')
const start = file.indexOf('function initializeGuestEngagement()')
const source = file.slice(start, file.indexOf('async function authActionRequest(', start))
const sessionKey = 'guest-engagement-v1'
const dismissalKey = 'guest-signup-dismissed-at'
const state = (overrides = {}) => ({views: [], copies: [], activeMs: 0, shown: false, ...overrides})

function setup({saved, dismissed = 0, signedIn = false, enabled = true, brokenStorage = false, session = new Map()} = {}) {
  if (saved) session.set(sessionKey, JSON.stringify(saved))
  const local = new Map([[dismissalKey, String(dismissed)]])
  const listeners = {}, windowListeners = {}, popupListeners = {}, timers = new Set(), modals = []
  let now = 1000000000, focus = true, busy = false
  const popup = {addEventListener: (name, handler) => {popupListeners[name] = handler}}
  const document = {visibilityState: 'visible', activeElement: {closest: () => null}, hasFocus: () => focus,
    getElementById: () => popup,
    querySelector: selector => selector.includes('[data-article-id]') ? {dataset: {articleId: 'one'}} : busy ? {} : null,
    addEventListener: (name, handler) => {listeners[name] = handler}}
  const context = {
    document, Date: {now: () => now},
    sessionStorage: {getItem: key => session.get(key), setItem: (key, value) => {
      if (brokenStorage) throw new Error('Storage disabled')
      session.set(key, value)
    }},
    localStorage: {getItem: key => local.get(key), setItem: (key, value) => {
      if (brokenStorage) throw new Error('Storage disabled')
      local.set(key, value)
    }},
    window: {CONFIG: {current_user: signedIn ? {id: 'reader'} : null, guest_engagement_enabled: enabled},
      addEventListener: (name, handler) => {windowListeners[name] = handler},
      bootstrap: {Modal: {getOrCreateInstance: () => ({show: () => modals.push('shown')})}}},
    setInterval: handler => {timers.add(handler); return handler}, clearInterval: handler => timers.delete(handler),
  }
  runInNewContext(source, context)
  context.initializeGuestEngagement()
  return {document, session, local, listeners, windowListeners, popupListeners, modals, timers,
    tick(seconds = 1) {for (let i = 0; i < seconds; i++) {now += 1000; [...timers].forEach(handler => handler())}},
    copy(id) {listeners['prompt-template-copied']?.({detail: {articleId: id}})},
    focus(value) {focus = value}, busy(value) {busy = value},
    stored() {return JSON.parse(session.get(sessionKey) || 'null')}}
}

test('time alone never shows a guest signup popup', () => {
  const page = setup()
  page.tick(90)
  assert.equal(page.modals.length, 0)
  assert.equal(page.stored().views.length, 1)
})

test('three distinct views and sixty active seconds show it once', () => {
  const page = setup({saved: state({views: ['two', 'three'], activeMs: 58000})})
  page.tick(2)
  assert.equal(page.modals.length, 0) // Wait for a quiet moment after navigation.
  page.tick()
  assert.equal(page.modals.length, 1)
  page.tick(90)
  assert.equal(page.modals.length, 1)
  assert.equal(page.stored().shown, true)
  const next = setup({session: page.session})
  next.tick(90)
  assert.equal(next.modals.length, 0)
})

test('typing, other modals, and ongoing interaction delay the popup', () => {
  const page = setup({saved: state({views: ['two', 'three'], activeMs: 60000})})
  page.busy(true)
  page.tick(5)
  assert.equal(page.modals.length, 0)
  page.busy(false)
  page.document.activeElement.closest = () => ({})
  page.tick(5)
  assert.equal(page.modals.length, 0)
  page.document.activeElement.closest = () => null
  page.listeners.scroll()
  page.tick(2)
  assert.equal(page.modals.length, 0)
  page.tick()
  assert.equal(page.modals.length, 1)
})

test('hidden and unfocused time does not count as active browsing', () => {
  const page = setup({saved: state({views: ['two', 'three']})})
  page.document.visibilityState = 'hidden'
  page.tick(90)
  assert.equal(page.stored().activeMs, 0)
  page.document.visibilityState = 'visible'
  page.focus(false)
  page.tick(90)
  assert.equal(page.stored().activeMs, 0)
  page.focus(true)
  page.tick(59)
  assert.equal(page.modals.length, 0)
  page.tick()
  assert.equal(page.modals.length, 1)
})

test('dismissal pauses encouragement for seven days', () => {
  const page = setup({saved: state({views: ['two', 'three'], activeMs: 60000})})
  page.tick(3)
  page.popupListeners['hidden.bs.modal']()
  const dismissed = Number(page.local.get(dismissalKey))
  const recent = setup({dismissed, saved: state({views: ['two', 'three'], activeMs: 60000})})
  recent.tick(90)
  assert.equal(recent.modals.length, 0)
  const expired = setup({dismissed: 1000000000 - 8 * 24 * 60 * 60 * 1000, saved: state({views: ['two', 'three'], activeMs: 60000})})
  expired.tick(3)
  assert.equal(expired.modals.length, 1)
})

test('signed-in users, excluded pages, and unavailable storage never show it', () => {
  for (const options of [{signedIn: true}, {enabled: false}, {brokenStorage: true}]) {
    const page = setup({...options, saved: state({views: ['two', 'three'], activeMs: 60000})})
    page.tick(90)
    assert.equal(page.modals.length, 0)
  }
})

test('back navigation respects a popup already shown on the next page', () => {
  const page = setup()
  page.windowListeners.pagehide()
  page.session.set(sessionKey, JSON.stringify(state({views: ['two', 'three'], activeMs: 60000, shown: true})))
  page.windowListeners.pageshow({persisted: true})
  page.tick(90)
  assert.equal(page.modals.length, 0)
  assert.equal(page.stored().shown, true)
})
