const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')
const fragment = name => readFileSync(join(__dirname, '../web-lambda/templates/fragments/', name), 'utf8')
const script = template => template.match(/<script>([\s\S]*?)<\/script>/)[1]

test('moderation uses inline form submission and requires a reason only for rejection', () => {
  let change
  let reloads = 0
  let submit
  const form = {action: 'https://api.example/articles/article/status',
    elements: {status: {value: 'published'}, comment: {value: ''}},
    addEventListener(event, fn) {if (event === 'change') change = fn}}
  runInNewContext(script(fragment('article-status-script.html')), {
    document: {addEventListener(event, fn) {fn()}, getElementById() {return form}},
    window: {location: {reload() {reloads++}}},
    handleFormSubmit(selector, url, options) {submit = {selector, url, options}}
  })
  assert.equal(submit.url, form.action)
  assert.equal(submit.options.resetOnSuccess, false)
  assert.equal(form.elements.comment.required, false)
  form.elements.status.value = 'rejected'
  change()
  assert.equal(form.elements.comment.required, true)
  assert.throws(() => submit.options.beforeSubmit({status: 'rejected', comment: null}), /rejection reason/)
  const prepared = submit.options.prepareData({status: 'rejected', comment: 'Needs references', ignored: 'value'})
  assert.deepEqual(JSON.parse(JSON.stringify(prepared)), {status: 'rejected', comment: 'Needs references'})
  submit.options.beforeSubmit(prepared)
  submit.options.onSuccess()
  assert.equal(reloads, 1)
  form.elements.status.value = 'published'
  change()
  assert.equal(form.elements.comment.required, false)
})

test('guest reports send only contact fields with a bounded reason, canonical URL and plain details', () => {
  const template = fragment('article-report.html')
  const reasons = runInNewContext('(' + template.match(/{% set report_reasons = ([\s\S]*?) %}/)[1] + ')')
  const url = 'https://example.com/@author/article'
  const limit = 1000 - url.length - 100
  const code = script(template)
    .replace('{{ report_reasons | tojson }}', JSON.stringify(reasons))
    .replace('{{ article_canonical_url | tojson }}', JSON.stringify(url))
    .replace("{{ url('create-contact-message', absolute=True) | tojson }}", JSON.stringify('https://api.example/contacts/message'))
    .replace('{{ report_details_limit }}', String(limit))
  let submit
  runInNewContext(code, {
    document: {addEventListener(event, fn) {fn()}},
    handleFormSubmit(selector, url, options) {submit = {selector, url, options}}
  })
  assert.equal(submit.options.authRequired, false)
  assert.equal(submit.url, 'https://api.example/contacts/message')
  assert.equal(submit.options.rules.reason[0].rule, 'required')
  const payload = submit.options.prepareData({name: 'Reader', email: 'reader@example.com',
    reason: 'spam', details: '<script>untrusted text</script>'})
  assert.deepEqual(Object.keys(payload).sort(), ['email', 'message', 'name'])
  assert.ok(payload.message.includes('Reason: Spam'))
  assert.ok(payload.message.includes(url))
  assert.ok(payload.message.includes('<script>untrusted text</script>'))
  const full = submit.options.prepareData({reason: 'inappropriate-content', details: 'x'.repeat(limit)})
  assert.ok(full.message.length <= 1000)
  assert.equal(submit.options.rules.details[0].value, limit)
})
