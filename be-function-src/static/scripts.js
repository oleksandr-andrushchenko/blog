// Form validation & submission
function handleFormSubmit(formSelector, submitUrl, options = {}) {
  const {
    successMessage = "Success!",
    errorMessage = "Something went wrong.",
    networkErrorMessage = "Network error. Please try again.",
    validationFailedMessage = "Please fix the highlighted fields.",
    rules = {},
    preparePayload = (data) => data,
    onSuccess = () => {
    },
    loadingText = "Submitting..."
  } = options

  const form = document.querySelector(formSelector)
  if (!form) return

  // Insert status div before submit button
  const submitBtn = form.querySelector("[type='submit']")
  const statusDiv = document.createElement("div")
  statusDiv.className = "form-status alert d-none d-flex align-items-center"
  statusDiv.setAttribute("role", "alert")
  submitBtn.insertAdjacentElement("beforebegin", statusDiv)

  let originalBtnContent = submitBtn.innerHTML

  let validator = null
  let hasTags = false

  if (typeof window.JustValidate !== "undefined") {
    validator = new window.JustValidate(formSelector, {
      errorFieldCssClass: "is-invalid",
      errorLabelCssClass: "invalid-feedback",
      successFieldCssClass: "is-valid",
      successLabelCssClass: "valid-feedback",
      focusInvalidField: true,
      lockForm: true
    })

    Object.entries(rules).forEach(([field, fieldRules]) => {
      for (const fieldRuleId in fieldRules) {
        if (fieldRules[fieldRuleId].rule === "tags") {
          const {minCnt, maxCnt, minLen, maxLen} = fieldRules[fieldRuleId]
          fieldRules[fieldRuleId] = {
            validator: (value) => {
              if (!value) return true
              const values = JSON.parse(value)
              if (!Array.isArray(values)) return true
              const items = values.map(item => item.value.trim()).filter(Boolean)
              return items.length >= minCnt && items.length <= maxCnt && items.every(t => t.length >= minLen && t.length <= maxLen)
            },
            errorMessage: `Tags must be ${minCnt}–${maxCnt} items, ${minLen}–${maxLen} chars each`
          }
          hasTags = true
        }
      }

      validator.addField(`[name="${field}"]`, fieldRules)
    })

    validator.onSuccess(() => submitForm())
  } else {
    // Fallback: submit without frontend validation
    form.addEventListener("submit", (e) => {
      e.preventDefault()
      submitForm()
    })
  }

  async function submitForm() {
    // Show loading state
    submitBtn.disabled = true
    submitBtn.innerHTML = `<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${loadingText}`

    statusDiv.className = "form-status alert d-none d-flex align-items-center"
    statusDiv.textContent = ""

    let data = {}
    form.querySelectorAll("[name]").forEach(input => {
      data[input.name] = input.value.trim()
      input.classList.remove("is-invalid") // reset invalid states
    })
    data = preparePayload(data, form)
    if (hasTags) {
      const values = JSON.parse(form.tags.value)
      data.tags = values.map(item => item.value.trim()).filter(Boolean)
    }

    try {
      if (!validator) {
        // Simple frontend validation fallback
        let hasError = false
        form.querySelectorAll("[required]").forEach(input => {
          if (!input.value.trim()) {
            input.classList.add("is-invalid")
            hasError = true

            // Optional: create invalid-feedback div if not present
            let feedback = input.nextElementSibling
            if (!feedback || !feedback.classList.contains("invalid-feedback")) {
              feedback = document.createElement("div")
              feedback.className = "invalid-feedback"
              input.insertAdjacentElement("afterend", feedback)
            }
            feedback.textContent = input.getAttribute("data-error") || "This field is required."
          }
        })

        if (hasError) {
          submitBtn.disabled = false
          submitBtn.innerHTML = originalBtnContent
          statusDiv.className = "form-status alert alert-warning d-flex align-items-center"
          statusDiv.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-2"></i> ${validationFailedMessage}`
          return
        }
      }

      // Submit via fetch
      const response = await fetch(submitUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
      })

      submitBtn.disabled = false
      submitBtn.innerHTML = originalBtnContent

      if (response.ok) {
        statusDiv.className = "form-status alert alert-success d-flex align-items-center"
        statusDiv.innerHTML = `<i class="bi bi-check-circle-fill me-2"></i> ${successMessage}`
        form.reset()
        if (validator) validator.refresh()

        let responseBody = {}
        if (response.status !== 204) {
          try {
            responseBody = await response.json()
          } catch (_) {
          }
        }
        onSuccess(responseBody)
        return
      }

      if (response.status === 422) {
        const json = await response.json()
        statusDiv.className = "form-status alert alert-warning d-flex align-items-center"
        statusDiv.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-2"></i> ${validationFailedMessage}`

        if (validator && json.details) {
          const errors = {}
          Object.entries(json.details).forEach(([field, msg]) => {
            errors[`[name="${field}"]`] = msg
          })
          validator.showErrors(errors)
        } else if (!validator && json.details) {
          // fallback: highlight invalid fields without validator
          Object.entries(json.details).forEach(([field, msg]) => {
            const input = form.querySelector(`[name="${field}"]`)
            if (input) {
              input.classList.add("is-invalid")
              let feedback = input.nextElementSibling
              if (!feedback || !feedback.classList.contains("invalid-feedback")) {
                feedback = document.createElement("div")
                feedback.className = "invalid-feedback"
                input.insertAdjacentElement("afterend", feedback)
              }
              feedback.textContent = msg
            }
          })
        }
        return
      }

      statusDiv.className = "form-status alert alert-danger d-flex align-items-center"
      statusDiv.innerHTML = `<i class="bi bi-x-circle-fill me-2"></i> ${errorMessage}`

    } catch (err) {
      submitBtn.disabled = false
      submitBtn.innerHTML = originalBtnContent
      statusDiv.className = "form-status alert alert-danger d-flex align-items-center"
      statusDiv.innerHTML = `<i class="bi bi-x-circle-fill me-2"></i> ${networkErrorMessage}`
    }
  }
}

// Load more
(() => {
  document.addEventListener("click", async function (e) {
    const btn = e.target.closest(".btn-load-more")
    if (!btn) return

    const container = document.querySelector(btn.dataset.container)
    if (!container) return console.error("Container not found:", btn.dataset.container)

    const limit = btn.dataset.limit
    const url = btn.dataset.url
    const offset = btn.dataset.offset

    btn.disabled = true
    const originalText = btn.textContent
    btn.textContent = "Loading..."

    try {
      const u = new URL(url, window.location.origin)
      u.searchParams.set("offset", offset)
      u.searchParams.set("limit", limit)
      const resp = await fetch(u.toString())
      if (!resp.ok) {
        console.log(`Request failed with status ${resp.status}`)
        btn.remove()
        return
      }

      const content = await resp.text()
      if (content === "") {
        btn.remove()
        return
      }

      container.insertAdjacentHTML("beforeend", content)

      const lastElement = container.lastElementChild
      const newOffset = lastElement.dataset.offset

      if (!newOffset || newOffset === offset) {
        btn.remove()
        return
      }

      btn.dataset.offset = newOffset
    } catch (err) {
      console.error(err)
    } finally {
      btn.disabled = false
      btn.textContent = originalText
    }
  })

  // --- Auto-load support only for [data-auto-load] buttons ---
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.click() // Trigger the same handler
      }
    }
  }, {
    rootMargin: "200px" // start loading earlier than fully visible
  })

// Attach observer only to buttons with data-auto-load
  document.querySelectorAll(".btn-load-more[data-auto-click]").forEach(btn => {
    observer.observe(btn)
  })
})();


// Tags input
(() => {
  const input = document.getElementById("tags-input")
  if (!input) return
  const url = input.dataset.url
  const injectHidden = input.dataset.injectHidden
  const autoSubmit = input.dataset.autoSubmit
  const form = input.closest("form")

  const tagify = new Tagify(input, {
    whitelist: [],
    // maxTags: 3,
    enforceWhitelist: false,
    // validate: tag => /^[0-9A-Za-z-.#]{2,20}$/.test(tag.value) || "Invalid tag",
    dropdown: {
      // enabled: 1,
      // maxItems: 10,
      closeOnSelect: true
    }
  })

  let controller // for aborting the previous fetch

  // normalize tags: lowercase + kebab-case
  const toKebabCase = str => str.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "")

  if (injectHidden) {
    input.removeAttribute("name")
    // container for hidden inputs
    let hiddenContainer = document.createElement("div")
    hiddenContainer.style.display = "none"
    form.appendChild(hiddenContainer)

    // rebuild hidden inputs on change
    function syncHiddenInputs() {
      hiddenContainer.innerHTML = ""
      tagify.value.forEach(tag => {
        const hidden = document.createElement("input")
        hidden.type = "hidden"
        hidden.name = "tags"  // use "tags" so backend maps correctly
        hidden.value = tag.value
        hiddenContainer.appendChild(hidden)
      })
    }

    tagify.on("change", syncHiddenInputs)

    // Immediately sync hidden inputs for any preloaded tags
    if (tagify.value.length) {
      syncHiddenInputs()
    }
  }

  // event fired when user types
  tagify.on("input", onInput)

  function onInput(e) {
    const value = e.detail.value
    tagify.whitelist = []
    tagify.dropdown.hide()

    controller && controller.abort()
    controller = new AbortController()

    tagify.loading(true)

    const u = new URL(url, window.location.origin)
    u.searchParams.set("prefix", value)
    fetch(u.toString(), {
      headers: {"Content-Type": "application/json"},
      signal: controller.signal
    })
      .then(async res => {
        if (!res.ok) {
          if (res.status === 422) {
            // handle 422 like form validation errors
            const json = await res.json().catch(() => null)
            if (json?.details) {
              Object.entries(json.details).forEach(([field, msg]) => {
                input.classList.add("is-invalid")
                let feedback = input.nextElementSibling
                if (!feedback || !feedback.classList.contains("invalid-feedback")) {
                  feedback = document.createElement("div")
                  feedback.className = "invalid-feedback"
                  input.insertAdjacentElement("afterend", feedback)
                }
                feedback.textContent = msg
              })
            }
          } else {
            console.error(`Tags fetch failed with status ${res.status}`)
          }
          tagify.loading(false)
          return null
        }

        // success: clear any previous error state
        input.classList.remove("is-invalid")
        const feedback = input.nextElementSibling
        if (feedback && feedback.classList.contains("invalid-feedback")) {
          feedback.remove()
        }

        // parse JSON
        return await res.json()
      })
      .then(data => {
        if (!data) return
        tagify.whitelist = data.map(d => (typeof d === "string" ? d : d.name)).filter(Boolean)
        tagify.loading(false)
        tagify.dropdown.show(value)
      })
      .catch(err => {
        if (err.name !== "AbortError") console.error(err)
        tagify.loading(false)
      })
  }

  // event fired when a tag is added
  tagify.on("add", (e) => {
    const normalized = toKebabCase(e.detail.data.value)
    if (normalized !== e.detail.data.value) {
      tagify.removeTag(e.detail.data.value, true) // remove old
      tagify.addTags([normalized], true) // add normalized
    }
  })

  if (autoSubmit) {
    tagify.on("change", () => form.requestSubmit())
  }
})()