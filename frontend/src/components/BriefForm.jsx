import { useState } from "react"

const API = "http://localhost:8000/api"

export default function BriefForm({ onSubmit }) {
  const [form, setForm] = useState({
    niche: "",
    target_audience: "",
    budget_min: "",
    budget_max: "",
    platforms: ["instagram"],
    keywords: [],
  })
  const [keywordInput, setKeywordInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const togglePlatform = (p) => {
    setForm((f) => ({
      ...f,
      platforms: f.platforms.includes(p)
        ? f.platforms.filter((x) => x !== p)
        : [...f.platforms, p],
    }))
  }

  const addKeyword = () => {
    const kw = keywordInput.trim()
    if (kw && !form.keywords.includes(kw)) {
      setForm((f) => ({ ...f, keywords: [...f.keywords, kw] }))
      setKeywordInput("")
    }
  }

  const removeKeyword = (kw) => {
    setForm((f) => ({ ...f, keywords: f.keywords.filter((k) => k !== kw) }))
  }

  const handleSubmit = async () => {
    if (!form.niche || !form.target_audience || !form.budget_min || !form.budget_max || form.platforms.length === 0) {
      setError("Fill in all required fields.")
      return
    }
    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${API}/run-campaign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          budget_min: parseInt(form.budget_min),
          budget_max: parseInt(form.budget_max),
        }),
      })
      const data = await resp.json()
      if (data.job_id) {
        onSubmit(data.job_id)
      } else {
        setError("Unexpected response from server.")
      }
    } catch (e) {
      setError(`Connection error: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="brief-form">
      <div className="form-header">
        <div className="form-eyebrow">NEW CAMPAIGN BRIEF</div>
        <h1 className="form-title">
          Find the right influencer.<br />
          <span>Verify them. Know what to pay.</span>
        </h1>
        <p className="form-subtitle">
          Submit a brief and CreatorLens will discover, audit, and rank influencers using parallel AI agents.
        </p>
      </div>

      <div className="form-grid">
        <div className="form-row full">
          <div className="form-field">
            <div className="field-label">NICHE *</div>
            <input
              placeholder="e.g. fitness supplements, skincare, tech gadgets"
              value={form.niche}
              onChange={(e) => setForm({ ...form, niche: e.target.value })}
            />
          </div>
        </div>

        <div className="form-row full">
          <div className="form-field">
            <div className="field-label">TARGET AUDIENCE *</div>
            <input
              placeholder="e.g. women 20-35 India, men 18-30 urban"
              value={form.target_audience}
              onChange={(e) => setForm({ ...form, target_audience: e.target.value })}
            />
          </div>
        </div>

        <div className="form-row">
          <div className="form-field">
            <div className="field-label">BUDGET RANGE (USD) *</div>
            <div className="budget-row">
              <input
                placeholder="min"
                value={form.budget_min}
                onChange={(e) => setForm({ ...form, budget_min: e.target.value })}
                type="number"
              />
              <span className="budget-sep">—</span>
              <input
                placeholder="max"
                value={form.budget_max}
                onChange={(e) => setForm({ ...form, budget_max: e.target.value })}
                type="number"
              />
            </div>
          </div>
          <div className="form-field">
            <div className="field-label">PLATFORMS *</div>
            <div className="platform-row">
              {["instagram", "twitter", "youtube"].map((p) => (
                <button
                  key={p}
                  className={`platform-btn ${form.platforms.includes(p) ? "active" : ""}`}
                  onClick={() => togglePlatform(p)}
                >
                  {p.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="form-row full">
          <div className="form-field">
            <div className="field-label">KEYWORDS (optional)</div>
            <div className="keywords-input-row">
              <input
                placeholder="add keyword and press +"
                value={keywordInput}
                onChange={(e) => setKeywordInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addKeyword()}
              />
              <button className="keyword-add-btn" onClick={addKeyword}>+</button>
            </div>
            {form.keywords.length > 0 && (
              <div className="keyword-tags">
                {form.keywords.map((kw) => (
                  <span key={kw} className="keyword-tag">
                    {kw}
                    <button onClick={() => removeKeyword(kw)}>×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {error && (
        <div style={{ marginTop: 12, fontSize: 11, color: "var(--red)" }}>
          ✗ {error}
        </div>
      )}

      <div className="form-actions">
        <button className="submit-btn" onClick={handleSubmit} disabled={loading}>
          {loading ? "LAUNCHING..." : "LAUNCH CAMPAIGN →"}
        </button>
        <span className="form-note">~2 min · parallel agents · AI scoring</span>
      </div>
    </div>
  )
}