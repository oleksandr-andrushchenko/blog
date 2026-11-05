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
    elements: {status: {value: 'published'}, comment: {value: '', setAttribute(name, value) {this[name] = value}}},
    addEventListener(event, fn) {if (event === 'change') change = fn}}
  runInNewContext(script(fragment('article-status-script.html')), {
    document: {addEventListener(event, fn) {fn()}, getElementById() {return form}},
    window: {location: {reload() {reloads++}}},
    handleFormSubmit(selector, url, options) {submit = {selector, url, options}}
  })
  assert.equal(submit.url, form.action)
  assert.equal(submit.options.resetOnSuccess, false)
  assert.equal(form.elements.comment['aria-required'], 'false')
  form.elements.status.value = 'rejected'
  change()
  assert.equal(form.elements.comment['aria-required'], 'true')
  assert.equal(submit.options.rules.comment[0].validator('   '), false)
  assert.equal(submit.options.rules.comment[0].validator('Needs references'), true)
  assert.throws(() => submit.options.beforeSubmit({status: 'rejected', comment: null}), /rejection reason/)
  const prepared = submit.options.prepareData({status: 'rejected', comment: 'Needs references', ignored: 'value'})
  assert.deepEqual(JSON.parse(JSON.stringify(prepared)), {status: 'rejected', comment: 'Needs references'})
  submit.options.beforeSubmit(prepared)
  submit.options.onSuccess()
  assert.equal(reloads, 1)
  form.elements.status.value = 'published'
  change()
  assert.equal(form.elements.comment['aria-required'], 'false')
})
