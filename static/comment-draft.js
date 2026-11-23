// Keep a guest's comment in this tab while the browser-bound login flow runs.
function commentDraftStore(articleId) {
  const lifetime = 15 * 60 * 1000
  const storageKey = `comment-draft:${articleId}`
  async function access(mode, operation) {
    return new Promise((resolve, reject) => {
      let db
      let settled = false
      const finish = (error, value) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        db?.close()
        error ? reject(error) : resolve(value)
      }
      const timer = setTimeout(() => finish(new Error("Could not preserve your draft. Please try again")), 10000)
      let request
      try { request = indexedDB.open("comment-drafts", 1) } catch (error) { finish(error); return }
      request.onupgradeneeded = () => request.result.createObjectStore("drafts")
      request.onerror = () => finish(request.error)
      request.onblocked = () => finish(new Error("Close other site tabs and try again"))
      request.onsuccess = () => {
        db = request.result
        if (settled) { db.close(); return }
        try {
          const transaction = db.transaction("drafts", mode)
          const store = transaction.objectStore("drafts")
          let result
          // Expired drafts, including abandoned sign-ins, are removed on the next visit.
          const cursor = store.openCursor()
          cursor.onsuccess = () => {
            const entry = cursor.result
            if (!entry) return
            if (entry.value.expiresAt <= Date.now()) entry.delete()
            entry.continue()
          }
          operation(store, value => { result = value })
          transaction.oncomplete = () => finish(null, result)
          transaction.onabort = transaction.onerror = () => finish(transaction.error || new Error("Could not preserve your draft"))
        } catch (error) { finish(error) }
      }
    })
  }
  return {
    async save(text, items) {
      let key = sessionStorage.getItem(storageKey)
      if (!key) {
        key = crypto.randomUUID()
        sessionStorage.setItem(storageKey, key)
      }
      await access("readwrite", store => store.put({text, items, expiresAt: Date.now() + lifetime}, key))
    },
    async load() {
      const key = sessionStorage.getItem(storageKey)
      if (!key) return null
      return access("readwrite", (store, done) => {
        const request = store.get(key)
        request.onsuccess = () => done(request.result?.expiresAt > Date.now() ? request.result : null)
      })
    },
    async remove() {
      const key = sessionStorage.getItem(storageKey)
      if (!key) return
      await access("readwrite", store => store.delete(key))
      sessionStorage.removeItem(storageKey)
    }
  }
}
