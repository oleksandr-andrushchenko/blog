const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')

function element() {
  const classes = new Set(['btn-outline-dark'])
  return {
    disabled: true,
    value: "",
    append() {},
    replaceChildren() {},
    handlers: {},
    classList: {
      add(name) {classes.add(name)},
      remove(name) {classes.delete(name)},
      contains(name) {return classes.has(name)},
      toggle(name, enabled) {enabled ? classes.add(name) : classes.delete(name)}
    },
    addEventListener(name, handler) {this.handlers[name] = handler},
    removeAttribute(name) {delete this[name]}
  }
}

for (const provider of ['telegram', 'threads', 'linkedin', 'pinterest']) {
  for (const connected of [false, true]) {
    test(`${provider} ${connected ? "connected" : "disconnected"} style survives errors and follows disconnects`, async () => {
      const template = readFileSync(join(__dirname, `../web-lambda/templates/fragments/${provider}-integration.html`), 'utf8')
      const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
        .replace(/{{[\s\S]*?}}/g, value => value.includes('pinterest-boards') ? '"https://api.example/boards"' : '"https://api.example/connection"')
      const elements = new Map()
      const getElementById = id => {
        if (!elements.has(id)) elements.set(id, element())
        return elements.get(id)
      }
      let failDisconnect = true
      let currentConnected = connected
      let failStatus = false
      let onFocus
      runInNewContext(script, {
        document: {getElementById, createElement: () => element(), addEventListener(event, handler) {handler()}},
        window: {location: {href: 'https://example/profile'}, addEventListener(event, handler) {if (event === 'focus') onFocus = handler}},
        URL, setTimeout, clearTimeout,
        fetch: async (url, options) => ({
          ok: options.method === 'DELETE' ? !failDisconnect : !failStatus,
          json: async () => url.endsWith('/boards') ? {boards: [{id: '123', name: 'Example board'}], board_id: '123'}
            : options.method === 'PATCH' ? {saved: true, board_id: JSON.parse(options.body).board_id}
            : options.method === 'DELETE'
            ? {details: 'Disconnect failed'}
            : {configured: true, connected: currentConnected, pending: !currentConnected, username: 'owner', channel_title: 'Channel'}
        })
      })
      await new Promise(resolve => setImmediate(resolve))
      const visibility = [getElementById(`${provider}_show_on_user_page`), getElementById(`${provider}_show_on_articles_pages`)]
      if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, !connected))
      // Keep unsaved checkbox drafts while connection requests fail.
      visibility[0].checked = true
      const connect = getElementById(`${provider}-connect`)
      assert.equal(connect.classList.contains('btn-dark'), connected)
      assert.equal(connect.classList.contains('btn-outline-dark'), !connected)
      assert.equal(connect.disabled, false)
      if (connected) {
        const disconnect = getElementById(`${provider}-disconnect`)
        await disconnect.handlers.click()
        assert.equal(connect.classList.contains('btn-dark'), true)
        if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, false))
        assert.equal(visibility[0].checked, true)
        assert.equal(connect.classList.contains('btn-outline-dark'), false)
        failDisconnect = false
        await disconnect.handlers.click()
        assert.equal(connect.classList.contains('btn-dark'), false)
        assert.equal(connect.classList.contains('btn-outline-dark'), true)
        if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, true))
        assert.equal(visibility[0].checked, true)
      }
      if (provider === 'telegram') {
        failStatus = true
        onFocus()
        await new Promise(resolve => setImmediate(resolve))
        if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, true))
        assert.equal(visibility[0].checked, true)
        failStatus = false
        currentConnected = true
        onFocus()
        await new Promise(resolve => setImmediate(resolve))
        if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, false))
        currentConnected = false
        onFocus()
        await new Promise(resolve => setImmediate(resolve))
        if (provider !== "linkedin") visibility.forEach(input => assert.equal(input.disabled, true))
      }
    })
  }
}
