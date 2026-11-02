const assert = require("node:assert/strict")
const {readFileSync} = require("node:fs")
const {join} = require("node:path")
const test = require("node:test")
const {runInNewContext} = require("node:vm")
const profile = readFileSync(join(__dirname, "../web-lambda/templates/fragments/user-form.html"), "utf8")
const walletSetup = profile.slice(profile.indexOf("    function initializeWalletRow"),
  profile.indexOf("    const cryptoContainer ="))

test("currency choices restrict networks, clear stale values, and enable custom currency fields", () => {
  const currency = {value: "", setCustomValidity() {assert.fail("Use explicit validation rules")}, focus() {this.focused = true}}
  const network = {id: "network", value: "", disabled: true, hidden: false,
    replaceChildren(...options) {this.options = options; this.value = ""}}
  const customNetwork = {id: "custom-network", value: "", disabled: true, hidden: true}
  const label = {}
  const customCurrency = {hidden: true}
  const radios = ["BTC", "USDT", "Other"].map(value => ({value, checked: false,
    addEventListener(event, fn) {this.change = fn}}))
  const row = {dataset: {walletNetworks: JSON.stringify({BTC: ["Bitcoin"], USDT: ["Ethereum", "Tron", "Solana"]})},
    querySelectorAll: () => radios,
    querySelector(selector) {
      if (selector === "[data-wallet-custom-currency]") return customCurrency
      if (selector.includes("currency")) return currency
      if (selector === "[data-wallet-network]") return network
      if (selector === "[data-wallet-custom-network]") return customNetwork
      return label
    }}
  const context = {row, Option: class {constructor(text, value) {this.text = text; this.value = value}}}
  runInNewContext(walletSetup + "\ninitializeWalletRow(row)", context)
  assert.equal(network.disabled, true)
  const choose = index => {radios.forEach((radio, i) => {radio.checked = i === index}); radios[index].change()}
  choose(0)
  assert.equal(currency.value, "BTC")
  assert.equal(currency.type, "hidden")
  assert.equal(network.disabled, false)
  assert.deepEqual(Array.from(network.options, option => option.value), ["", "Bitcoin"])
  assert.equal(network.value, "Bitcoin")
  choose(1)
  assert.equal(network.value, "")
  assert.equal(currency.value, "USDT")
  assert.deepEqual(Array.from(network.options, option => option.value), ["", "Ethereum", "Tron", "Solana"])
  choose(2)
  assert.equal(currency.type, "text")
  assert.equal(currency.focused, true)
  assert.equal(customCurrency.hidden, false)
  assert.equal(network.disabled, true)
  assert.equal(network.hidden, true)
  assert.equal(customNetwork.disabled, false)
  assert.equal(customNetwork.hidden, false)
  assert.equal(label.htmlFor, "custom-network")
  customNetwork.value = "Custom chain"
  choose(0)
  assert.equal(customNetwork.value, "")
  assert.equal(customNetwork.disabled, true)
  assert.equal(customCurrency.hidden, true)
  assert.equal(label.htmlFor, "network")
  assert.equal(network.value, "Bitcoin")
  network.value = ""
  runInNewContext("initializeWalletRow(row)", context)
  assert.equal(network.value, "Bitcoin")
  network.value = "Saved custom network"
  runInNewContext("initializeWalletRow(row)", context)
  assert.equal(network.value, "Saved custom network")
})

test("dynamic creator fields register explicit rules and discard removed or disabled controls", () => {
  const registrations = new Map()
  const removed = []
  const support = Object.fromEntries(['name', 'url', 'text'].map(name => [name, {name}]))
  const wallet = Object.fromEntries(['currency', 'network', 'custom-network', 'address', 'instructions'].map(name => [name, {name}]))
  wallet['custom-network'].disabled = true
  const row = fields => ({dataset: {linkId: 'test-wallet'}, querySelector(selector) {
    if (selector === '[data-wallet-network]') return fields.network
    if (selector === '[data-wallet-custom-network]') return fields['custom-network']
    return fields[selector.match(/data-link-field="([^"]+)"/)[1]]
  }})
  let supportRows = [row(support)]
  const context = {
    profileValidator: {
      addField(field, rules, config) {registrations.set(field, {rules, config})},
      removeField(field) {removed.push(field); registrations.delete(field)},
    },
    linkRows: () => supportRows, cryptoRows: () => [row(wallet)], URL,
  }
  const code = profile.slice(profile.indexOf('    const creatorValidationFields'),
    profile.indexOf("    cryptoContainer.addEventListener('change', syncCreatorValidation)"))
  runInNewContext(code + '\nsyncCreatorValidation()', context)
  assert.equal(registrations.size, 7)
  assert.equal(registrations.has(wallet['custom-network']), false)
  const supportUrl = registrations.get(support.url).rules.find(rule => rule.validator).validator
  assert.equal(supportUrl('https://example.com/support'), true)
  for (const url of ['http://example.com', 'https://name:secret@example.com', 'https://exa mple.com']) {
    assert.equal(supportUrl(url), false)
  }
  assert.equal(registrations.get(wallet.currency).config.errorsContainer, '#crypto-test-wallet-currency-errors')
  const addressRules = registrations.get(wallet.address).rules
  assert.equal(addressRules.find(rule => rule.rule === 'maxLength').value, 512)
  assert.equal(addressRules.find(rule => rule.rule === 'customRegexp').value.test('abc def'), false)
  supportRows = []
  wallet.network.disabled = true
  wallet['custom-network'].disabled = false
  runInNewContext('syncCreatorValidation()', context)
  assert.equal(registrations.size, 4)
  assert.equal(removed.length, 7)
  assert.equal(registrations.has(support.url), false)
  assert.equal(registrations.has(wallet.network), false)
  assert.deepEqual(Array.from(registrations.get(wallet['custom-network']).rules, rule => rule.rule), ['maxLength'])
})
