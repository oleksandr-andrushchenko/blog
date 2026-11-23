const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')

const file = readFileSync(join(__dirname, '../static/scripts.js'), 'utf8')
const start = file.indexOf('async function authActionRequest(')
const source = file.slice(start, file.indexOf('document.addEventListener("DOMContentLoaded"', start))
const settle = () => new Promise(resolve => setImmediate(resolve))

function element(dataset = {}) {
  const classes = new Set(['d-none'])
  const handlers = {}
  return {dataset, handlers, disabled: false, textContent: '',
    classList: {add: value => classes.add(value), remove: value => classes.delete(value)}, classes,
    addEventListener: (name, callback) => {handlers[name] = callback}}
}

function setup({completion = false, signedIn = true, welcomePending = false, reply} = {}) {
  const listeners = {}, calls = [], locations = [], modals = []
  const elements = completion ? {
    'auth-completion': element({returnUrl: '/articles'}),
    'auth-action-message': element(), 'auth-action-retry': element(),
    'auth-action-skip': element(), 'registration-welcome': element()
  } : {'registration-welcome': element()}
  const context = {
    document: {addEventListener: (name, callback) => {listeners[name] = callback},
      getElementById: id => elements[id],
      createElement: () => ({dataset: {}, setAttribute() {}})},
    window: {CONFIG: {current_user: signedIn ? {id: 'reader'} : null,
      begin_auth_action_url: '/auth/actions', complete_auth_action_url: '/auth/actions/complete',
      welcome_seen_url: '/auth/welcome/seen', welcome_pending: welcomePending},
      location: {href: 'https://example.com/articles', pathname: '/articles', search: '', hash: '',
        assign: url => locations.push(url), replace: url => locations.push(url)},
      bootstrap: {Modal: {getOrCreateInstance: modal => ({show: () => modals.push(modal)})}}},
    URL, AbortSignal,
    fetch: async (url, options) => {
      calls.push({url, options, body: JSON.parse(options.body)})
      return reply(url, options)
    }
  }
  runInNewContext(source, context)
  context.initializeAuthActions()
  return {listeners, calls, elements, locations, modals}
}

const response = value => ({ok: true, json: async () => value})

test('guest click saves the exact action before navigating to login', async () => {
  const page = setup({signedIn: false, reply: async () => response({login_url: 'https://login.example.com/'})})
  assert.equal(page.calls.length, 0)
  const link = {dataset: {authAction: 'like', authTarget: 'article-one'},
    href: 'https://example.com/login?redirect_url=%2Farticles%2Farticle-one',
    parentElement: {querySelector: () => null}, setAttribute() {}, removeAttribute() {}}
  let prevented = false
  await page.listeners.click({button: 0, target: {closest: () => link}, preventDefault() {prevented = true}})
  assert.ok(prevented)
  assert.deepEqual(page.calls[0].body, {action: 'like', target: 'article-one', return_url: '/articles/article-one'})
  assert.equal(page.calls[0].options.credentials, 'include')
  assert.deepEqual(page.locations, ['https://login.example.com/'])
})

test('welcome remains hidden until the pending mutation has finished', async () => {
  let finish
  const page = setup({completion: true, reply: () => new Promise(resolve => {finish = resolve})})
  assert.equal(page.calls.length, 1)
  assert.equal(page.modals.length, 0)
  assert.equal(page.locations.length, 0)
  finish(response({redirect_url: '/articles/one', welcome: true}))
  await settle()
  assert.equal(page.modals.length, 1)
  assert.equal(page.calls.length, 1) // Calling show() alone is not an acknowledgement.
  await page.elements['registration-welcome'].handlers['shown.bs.modal']()
  assert.equal(page.calls[1].url, '/auth/welcome/seen')
  assert.equal(page.calls[1].options.keepalive, true)
  assert.equal(page.locations.length, 0)
  page.elements['registration-welcome'].handlers['hidden.bs.modal']()
  assert.deepEqual(page.locations, ['/articles/one'])
})

test('returning sign-in continues directly without a welcome popup', async () => {
  const page = setup({completion: true, reply: () => response({redirect_url: '/tags', welcome: false})})
  await settle()
  assert.equal(page.modals.length, 0)
  assert.deepEqual(page.locations, ['/tags'])
})

test('failed action stays retryable and welcome follows an explicit skip', async () => {
  let failed = true
  const page = setup({completion: true, reply: () => failed
    ? {ok: false, json: async () => ({detail: 'Article is no longer public'})}
    : response({redirect_url: '/articles', welcome: true})})
  await settle()
  assert.equal(page.modals.length, 0)
  assert.equal(page.elements['auth-action-message'].textContent, 'Article is no longer public')
  assert.equal(page.elements['auth-action-retry'].disabled, false)
  assert.ok(!page.elements['auth-action-skip'].classes.has('d-none'))
  failed = false
  await page.elements['auth-action-skip'].handlers.click()
  assert.deepEqual(page.calls[1].body, {skip: true})
  assert.equal(page.modals.length, 1)
})

test('an unacknowledged welcome is retried on ordinary authenticated pages', async () => {
  const page = setup({welcomePending: true, reply: () => response({seen: true})})
  assert.equal(page.modals.length, 1)
  assert.equal(page.calls.length, 0)
  await page.elements['registration-welcome'].handlers['shown.bs.modal']()
  assert.equal(page.calls[0].url, '/auth/welcome/seen')
  assert.equal(page.locations.length, 0)
})

test('a failed acknowledgement leaves navigation working so a later visit can retry', async () => {
  const page = setup({completion: true, reply: url => url.endsWith('/seen')
    ? {ok: false, json: async () => ({detail: 'Temporarily unavailable'})}
    : response({redirect_url: '/articles', welcome: true})})
  await settle()
  await page.elements['registration-welcome'].handlers['shown.bs.modal']()
  await settle()
  page.elements['registration-welcome'].handlers['hidden.bs.modal']()
  assert.deepEqual(page.locations, ['/articles'])
})
