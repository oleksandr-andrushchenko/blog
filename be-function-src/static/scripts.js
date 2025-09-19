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