const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const {runInNewContext} = require('node:vm')
const test = require('node:test')

function element() {
  const classes = new Set(['d-none'])
  return {
    disabled: false, value: '', textContent: '', dataset: {}, handlers: {}, children: [],
    classList: {
      add: name => classes.add(name), remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, on) => on ? classes.add(name) : classes.delete(name)
    },
    addEventListener(name, handler) {this.handlers[name] = handler},
    setAttribute(name, value) {this[name] = value},
    removeAttribute(name) {delete this[name]},
    append(child) {this.children.push(child)},
    add(child) {this.children.push(child)},
    replaceChildren(...children) {this.children = children},
    querySelector() {return this.label ||= element()}
  }
}
function load(fragment, fetch, button) {
  const elements = new Map()
  const get = id => {
    if (!elements.has(id)) elements.set(id, element())
    return elements.get(id)
  }
  const template = readFileSync(join(__dirname, '../web-lambda/templates/fragments/' + fragment + '.html'), 'utf8')
  const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace(/{{[\s\S]*?}}/g, value => value.includes('facebook-page-pages') ? '"https://api.example/pages"' : '"https://api.example/connection"')
  const assigned = []
  runInNewContext(script, {
    document: {getElementById: get, createElement: element, addEventListener(name, handler) {handler()},
      querySelectorAll: () => button ? [button] : []},
    window: {location: {href: 'https://catalog.example/utils', assign(url) {assigned.push(url)}}},
    history: {replaceState() {}}, URL, AbortSignal, crypto: {randomUUID: () => 'explicit-event'}, fetch,
    Option: function(text, value) {this.textContent = text; this.value = value}
  })
  return {get, assigned}
}
const settle = () => new Promise(resolve => setImmediate(resolve))

for (const configured of [true, false]) {
  test(`Utils connection configuration=${configured} and Page picker`, async () => {
    const requests = []
    const ui = load('facebook-page-integration', async (url, options) => {
      requests.push([url, options])
      return {ok: true, json: async () => url.endsWith('/pages') ?
        {pages: [{id: '123', name: '<Example Page>'}]} :
        {configured, connected: false, can_select: configured}}
    })
    await settle()
    assert.equal(ui.get('facebook-page-connect').disabled, !configured)
    assert.equal(ui.get('facebook-page-connect-label').textContent, 'Connect')
    assert.equal(ui.get('facebook-page-select-form').classList.contains('d-none'), !configured)
    if (configured) {
      assert.equal(ui.get('facebook-page-select').children[1].textContent, '<Example Page>')
      ui.get('facebook-page-select').value = '123'
      await ui.get('facebook-page-select-form').handlers.submit({preventDefault() {}})
      await settle()
      const patch = requests.find(([, options]) => options.method === 'PATCH')
      assert.deepEqual(JSON.parse(patch[1].body), {page_id: '123'})
      assert.equal(patch[1].credentials, 'include')
      assert.ok(patch[1].signal)
    }
  })
}

test('failed disconnect preserves connected styling and permits recovery', async () => {
  const ui = load('facebook-page-integration', async (url, options) => ({
    ok: options.method !== 'DELETE',
    json: async () => options.method === 'DELETE' ? {details: 'Disconnect failed'} :
      {configured: true, connected: true, page_name: 'Example Page', can_select: false}
  }))
  await settle()
  await ui.get('facebook-page-disconnect').handlers.click()
  await settle()
  assert.equal(ui.get('facebook-page-connect').classList.contains('btn-dark'), true)
  assert.equal(ui.get('facebook-page-connect-label').textContent, 'Reconnect')
  assert.equal(ui.get('facebook-page-disconnect').disabled, false)
  assert.equal(ui.get('facebook-page-status').textContent, 'Disconnect failed')
})

function shareButton() {
  const button = element()
  button.disabled = true
  button.dataset = {platform: 'facebook', platformName: 'Facebook Page', canShare: 'true',
    shareUrl: 'https://api.example/articles/article/share/facebook-page',
    connectionStatusUrl: 'https://api.example/connection'}
  button.parentElement = element()
  return button
}

test('a disconnected Page displays the connection link', async () => {
  const button = shareButton()
  load('article-sharing', async () => ({ok: true, json: async () => ({configured: true, connected: false})}), button)
  await settle()
  assert.equal(button.disabled, true)
  assert.equal(button.parentElement.querySelector().classList.contains('d-none'), false)
})

for (const outcome of ['shared', 'unconfirmed', 'lost', 'processing']) {
  test(`Facebook Page share ${outcome} sends one explicit request`, async () => {
    const button = shareButton()
    const requests = []
    const ui = load('article-sharing', async (url, options) => {
      if (url.endsWith('/connection')) {
        return {ok: true, json: async () => ({configured: true, connected: true, page_name: 'Example Page'})}
      }
      requests.push(options)
      if (outcome === 'lost') throw new Error('Connection lost')
      return {ok: true, json: async () => outcome === 'shared' ?
        {shared: true, page_name: 'Example Page', post_url: 'https://www.facebook.com/123_456'} : {status: outcome}}
    }, button)
    await settle()
    assert.equal(button.disabled, false)
    await button.handlers.click()
    assert.equal(requests.length, 1)
    assert.equal(requests[0].credentials, 'include')
    assert.deepEqual(JSON.parse(requests[0].body), {event_id: 'explicit-event'})
    assert.ok(requests[0].signal)
    if (outcome === 'shared') {
      assert.equal(button.dataset.state, 'shared')
      const status = ui.get('article-sharing-status')
      assert.match(status.textContent, /Example Page/)
      assert.equal(status.children[0].href, 'https://www.facebook.com/123_456')
      assert.match(status.children[0].rel, /nofollow/)
    } else {
      assert.equal(button.dataset.state, 'unconfirmed')
      assert.equal(button.disabled, true)
      await button.handlers.click()
      assert.equal(requests.length, 1)
    }
  })
}
