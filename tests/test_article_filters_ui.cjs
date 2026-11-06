const assert = require('node:assert/strict')
const {readFileSync} = require('node:fs')
const {join} = require('node:path')
const test = require('node:test')
const {runInNewContext} = require('node:vm')
const script = readFileSync(join(__dirname, '../web-lambda/templates/articles.html'), 'utf8')
  .match(/<script>([\s\S]*?)<\/script>/)[1]

function serialize(params) {
  let ready, submitData
  runInNewContext(script, {document: {
    addEventListener(event, callback) {assert.equal(event, 'DOMContentLoaded'); ready = callback},
    getElementById(id) {
      assert.equal(id, 'article-filters')
      return {addEventListener(event, callback) {assert.equal(event, 'formdata'); submitData = callback}}
    }
  }})
  ready()
  const formData = new URLSearchParams(params)
  submitData({formData})
  return formData
}

test('all categories omits the empty slug while keeping repeated tags and sort/status', () => {
  const params = serialize('category=&tags=caching&tags=redis&type=popular&status=unpublished')
  assert.equal(params.has('category'), false)
  assert.deepEqual(params.getAll('tags'), ['caching', 'redis'])
  assert.equal(params.get('type'), 'popular')
  assert.equal(params.get('status'), 'unpublished')
})

test('selected category and other filters survive form serialization', () => {
  const query = 'category=distributed-systems&tags=redis&type=latest&status=published'
  assert.equal(serialize(query).toString(), query)
})
