const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')

function element() {
  const classes = new Set(['btn-outline-dark'])
  return {
    disabled: true,
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

for (const provider of ['telegram', 'threads', 'linkedin']) {
  for (const connected of [false, true]) {
    test(`${provider} ${connected ? "connected" : "disconnected"} style survives errors and follows disconnects`, async () => {
      const template = readFileSync(join(__dirname, `../web-lambda/templates/fragments/${provider}-integration.html`), 'utf8')
      const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
        .replace(/{{[\s\S]*?}}/g, '"https://api.example/connection"')
      const elements = new Map()
      const getElementById = id => {
        if (!elements.has(id)) elements.set(id, element())
        return elements.get(id)
      }
      let failDisconnect = true
      runInNewContext(script, {
        document: {getElementById, addEventListener(event, handler) {handler()}},
        window: {location: {href: 'https://example/profile'}, addEventListener() {}},
        URL, setTimeout, clearTimeout,
        fetch: async (url, options) => ({
          ok: options.method !== 'DELETE' || !failDisconnect,
          json: async () => options.method === 'DELETE'
            ? {details: 'Disconnect failed'}
            : {configured: true, connected, pending: false, username: 'owner', channel_title: 'Channel'}
        })
      })
      await new Promise(resolve => setImmediate(resolve))
      const connect = getElementById(`${provider}-connect`)
      assert.equal(connect.classList.contains('btn-dark'), connected)
      assert.equal(connect.classList.contains('btn-outline-dark'), !connected)
      assert.equal(connect.disabled, false)
      if (connected) {
        const disconnect = getElementById(`${provider}-disconnect`)
        await disconnect.handlers.click()
        assert.equal(connect.classList.contains('btn-dark'), true)
        assert.equal(connect.classList.contains('btn-outline-dark'), false)
        failDisconnect = false
        await disconnect.handlers.click()
        assert.equal(connect.classList.contains('btn-dark'), false)
        assert.equal(connect.classList.contains('btn-outline-dark'), true)
      }
    })
  }
}
