const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')

const file = readFileSync(join(__dirname, '../static/scripts.js'), 'utf8')
const start = file.indexOf('function initializeBookmarks()')
const source = file.slice(start, file.indexOf('document.addEventListener("DOMContentLoaded"', start))
const settle = () => new Promise(resolve => setImmediate(resolve))

function button(id, saved = false) {
  const classes = new Set([saved ? 'btn-success' : 'btn-light'])
  const icon = {}, label = {textContent: saved ? 'Bookmarked' : 'Bookmark'}, attrs = {}
  attrs['aria-pressed'] = String(saved)
  return {dataset: {articleId: id, bookmarkSaved: String(saved)}, disabled: false, attrs, icon, label,
    classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name)}}, classes,
    setAttribute(name, value) {attrs[name] = value}, removeAttribute(name) {delete attrs[name]},
    querySelector(selector) {return this.loading ? null : selector === 'i' ? icon : label},
    closest(selector) {return selector === '.btn-article-bookmark' ? this : null}}
}

function setup(buttons, handler, signedIn = true, counters = []) {
  const listeners = {}, calls = [], alerts = [], events = []
  const timers = new Set()
  const context = {document: {body: {}, querySelectorAll: selector => selector === "[data-bookmarks-count]" ? counters : buttons, getElementById: () => null,
    addEventListener: (name, callback) => {listeners[name] = callback},
    dispatchEvent: event => events.push(event)},
    window: {CONFIG: {current_user: signedIn ? {id: 'reader'} : null,
      update_bookmark_url: '/bookmarks/{article_id}'},
      alert: text => alerts.push(text)},
    $: control => ({
      setButtonLoading() {
        control.loading = true
        control.loadingText = 'Loading...'
        control.disabled = true
        control.setAttribute('aria-busy', 'true')
      },
      clearButtonLoading() {
        control.loading = false
        delete control.loadingText
        control.disabled = false
        control.removeAttribute('aria-busy')
      }
    }),
    CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options.detail}},
    AbortController,
    setTimeout: callback => {timers.add(callback); return callback},
    clearTimeout: callback => timers.delete(callback),
    fetch: async (url, options) => {
      const body = JSON.parse(options.body)
      calls.push({url, body, options})
      return handler(url, body, options)
    }}
  runInNewContext(source, context)
  context.initializeBookmarks()
  return {calls, alerts, events, timeout: () => [...timers].forEach(callback => callback()),
    click: target => listeners.click({target, preventDefault() {}})}
}

const response = value => ({ok: true, json: async () => ({bookmarks_count: value.saved ? 1 : 0, ...value})})

test('server-rendered controls make no initial request and synchronize copies after a save', async () => {
  const buttons = [button('one'), button('one'), button('two', true)]
  const page = setup(buttons, (url, body) => response(body))
  await settle()
  assert.equal(page.calls.length, 0)
  assert.equal(buttons[2].attrs['aria-pressed'], 'true')
  await page.click(buttons[0])
  assert.deepEqual(page.calls[0].body, {saved: true})
  assert.equal(page.calls[0].options.credentials, 'include')
  assert.equal(buttons[0].label.textContent, 'Bookmarked')
  assert.ok(buttons[0].classes.has('btn-success'))
  assert.ok(!buttons[0].classes.has('btn-light'))
  assert.equal(buttons[1].attrs['aria-pressed'], 'true')
})

test('failed mutation preserves the server-rendered state and enables retry', async () => {
  const saved = button('one', true)
  const page = setup([saved], () => ({ok: false}))
  await page.click(saved)
  assert.equal(saved.attrs['aria-pressed'], 'true')
  assert.equal(saved.label.textContent, 'Bookmarked')
  assert.equal(saved.disabled, false)
  assert.equal(page.alerts.length, 1)
})

test('dynamically inserted server-rendered controls work without state requests', async () => {
  const buttons = []
  const page = setup(buttons, (url, body) => response(body))
  buttons.push(button('one', true))
  await page.click(buttons[0])
  assert.equal(page.calls.length, 1)
  assert.deepEqual(page.calls[0].body, {saved: false})
  assert.equal(buttons[0].label.textContent, 'Bookmark')
  await page.click(buttons[0])
  assert.deepEqual(page.calls[1].body, {saved: true})
})

test('guests make no private requests', async () => {
  const guest = setup([], () => {throw new Error('No guest requests')}, false)
  await settle()
  assert.equal(guest.calls.length, 0)
})

test('a stalled save request times out and preserves the previous state for retry', async () => {
  const control = button('one', true)
  const page = setup([control], (url, body, options) =>
    new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('Timed out')))))
  const click = page.click(control)
  assert.equal(control.disabled, true)
  page.timeout()
  await click
  assert.equal(control.disabled, false)
  assert.equal(control.attrs['aria-busy'], undefined)
  assert.equal(control.attrs['aria-pressed'], 'true')
  assert.equal(control.label.textContent, 'Bookmarked')
})

test('bookmark totals use the response count, update all displays, and hide zero', async () => {
  const classes = new Set(['d-none'])
  const statClasses = new Set(['d-none'])
  const counter = {dataset: {bookmarksCount: 'one', count: '0'}, textContent: '0',
    classList: {toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name)}},
    closest: () => ({classList: {toggle(name, enabled) {enabled ? statClasses.add(name) : statClasses.delete(name)}}})}
  const control = button('one')
  const page = setup([control], (url, body) => response({...body, bookmarks_count: body.saved ? 1234 : 0}), true, [counter])
  await page.click(control)
  assert.equal(counter.textContent, '1,234')
  assert.equal(counter.dataset.count, '1234')
  assert.ok(!classes.has('d-none') && statClasses.has('d-inline-flex'))
  await page.click(control)
  assert.equal(counter.textContent, '0')
  assert.ok(classes.has('d-none') && statClasses.has('d-none'))
})


test('all copies show loading feedback, block repeated clicks, and restore updated content', async () => {
  const buttons = [button('one'), button('one'), button('two')]
  let finish
  const page = setup(buttons, () => new Promise(resolve => {finish = resolve}))
  const click = page.click(buttons[0])
  for (const control of buttons.slice(0, 2)) {
    assert.equal(control.disabled, true)
    assert.equal(control.loadingText, 'Loading...')
    assert.equal(control.attrs['aria-busy'], 'true')
  }
  assert.equal(buttons[2].disabled, false)
  await page.click(buttons[1])
  assert.equal(page.calls.length, 1)
  finish(response({saved: true}))
  await click
  for (const control of buttons.slice(0, 2)) {
    assert.equal(control.loading, false)
    assert.equal(control.disabled, false)
    assert.equal(control.attrs['aria-busy'], undefined)
    assert.equal(control.label.textContent, 'Bookmarked')
  }
})

test('invalid responses restore the original button content for retry', async () => {
  const control = button('one')
  const page = setup([control], () => response({saved: 'invalid'}))
  await page.click(control)
  assert.equal(control.loading, false)
  assert.equal(control.disabled, false)
  assert.equal(control.attrs['aria-pressed'], 'false')
  assert.equal(control.label.textContent, 'Bookmark')
  assert.equal(page.alerts.length, 1)
})

test('only a successful bookmark save announces confirmation', async () => {
  const control = button('one')
  const page = setup([control], (url, body) => response(body))
  await page.click(control)
  assert.equal(page.events.length, 1)
  assert.equal(page.events[0].type, 'article-bookmarked')
  assert.equal(page.events[0].detail.articleId, 'one')
  await page.click(control)
  assert.equal(page.events.length, 1)
  const failed = setup([control], () => ({ok: false}))
  await failed.click(control)
  assert.equal(failed.events.length, 0)
})
