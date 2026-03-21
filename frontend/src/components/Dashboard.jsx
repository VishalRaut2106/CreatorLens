import { useState, useEffect } from "react"

const API = "http://localhost:8000/api"

const STEPS = [
  { id: 1, name: "Keyword expansion via Ollama" },
  { id: 2, name: "Discovering influencers via TinyFish agents" },
  { id: 3, name: "Parallel audit — engagement, brand safety, pricing" },
  { id: 4, name: "LLM scoring & ranking" },
  { id: 5, name: "Finalizing results" },
]

function fmt(n) {
  if (!n) return "—"
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M"
  if (n >= 1000) return (n / 1000).toFixed(0) + "K"
  return n
}

function scoreClass(s) {
  if (s >= 75) return "high"
  if (s >= 45) return "mid"
  return "low"
}

function RiskBadge({ flag }) {
  return (
    <span className={`risk-badge ${flag || "green"}`}>
      {(flag || "green").toUpperCase()}
    </span>
  )
}

function DossierPanel({ influencer }) {
  const bd = influencer.score_breakdown || {}
  const breakdown = [
    { label: "ENGAGEMENT", key: "engagement_quality" },
    { label: "BRAND FIT", key: "brand_fit" },
    { label: "RISK SCORE", key: "risk_score" },
    { label: "PRICE FIT", key: "price_fairness" },
  ]

  return (
    <div className="dossier-panel">
      <div className="dossier-top">
        <div>
          <div className="dossier-handle">@{influencer.handle}</div>
          <div className="dossier-platform">{(influencer.platform || "").toUpperCase()}</div>
        </div>
        <div className="dossier-score-block">
          <div className="dossier-score-label">COMPOSITE SCORE</div>
          <div className={`dossier-score-num ${scoreClass(influencer.composite_score)}`}>
            {influencer.composite_score?.toFixed(1)}
          </div>
        </div>
      </div>

      <div className="dossier-grid">
        <div className="dossier-stat">
          <div className="stat-label">FOLLOWERS</div>
          <div className="stat-value">{fmt(influencer.followers)}</div>
        </div>
        <div className="dossier-stat">
          <div className="stat-label">ENGAGEMENT</div>
          <div className="stat-value">{influencer.engagement_rate ? `${influencer.engagement_rate}%` : "—"}</div>
        </div>
        <div className="dossier-stat">
          <div className="stat-label">PRICE RANGE</div>
          <div className="stat-value">
            {influencer.price_low && influencer.price_high
              ? `$${fmt(influencer.price_low)}–$${fmt(influencer.price_high)}`
              : "—"}
          </div>
          <div className="stat-sub">per post</div>
        </div>
        <div className="dossier-stat">
          <div className="stat-label">RISK</div>
          <div className="stat-value">
            <RiskBadge flag={influencer.risk_flag} />
          </div>
        </div>
      </div>

      {Object.keys(bd).length > 0 && (
        <div className="breakdown-grid">
          {breakdown.map(({ label, key }) => (
            <div className="breakdown-item" key={key}>
              <div className="breakdown-label">
                <span>{label}</span>
                <span className="breakdown-val">{bd[key] ?? "—"}</span>
              </div>
              <div className="breakdown-bar-bg">
                <div
                  className="breakdown-bar-fill"
                  style={{ width: `${bd[key] || 0}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {influencer.ai_summary && (
        <div className="dossier-summary">{influencer.ai_summary}</div>
      )}

      {(influencer.risk_evidence || influencer.risk_flag !== "green") && (
        <div className="dossier-risk">
          <div className="risk-label">BRAND SAFETY INTELLIGENCE</div>
          <div className={`risk-evidence ${influencer.risk_flag === "red" ? "flagged" : ""}`}>
            {influencer.risk_evidence || "No significant risk signals detected."}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Dashboard({ jobId, onComplete, onReset, loading, results }) {
  const [currentStep, setCurrentStep] = useState(1)
  const [ticker, setTicker] = useState("Initialising pipeline...")
  const [selected, setSelected] = useState(0)

  // Poll for results
  useEffect(() => {
    if (!loading || !jobId) return

    const messages = [
      "Expanding keywords with Ollama...",
      "Firing discovery agents across Instagram, TikTok, YouTube...",
      "Running parallel audit agents — engagement, brand safety, pricing...",
      "Scoring and ranking candidates with LLM...",
      "Saving results to database...",
    ]

    let pollCount = 0
    const interval = setInterval(async () => {
      pollCount++
      // Advance step ticker for UX
      const stepIdx = Math.min(Math.floor(pollCount / 3), STEPS.length - 1)
      setCurrentStep(stepIdx + 1)
      setTicker(messages[stepIdx])

      try {
        const resp = await fetch(`${API}/status/${jobId}`)
        const data = await resp.json()
        if (data.status === "complete") {
          clearInterval(interval)
          onComplete(data.results)
        } else if (data.status === "failed") {
          clearInterval(interval)
          setTicker("Pipeline failed. Check backend logs.")
        }
      } catch (e) {
        setTicker(`Polling error: ${e.message}`)
      }
    }, 5000)

    return () => clearInterval(interval)
  }, [loading, jobId])

  const handleCancel = async () => {
    try {
      await fetch(`${API}/cancel-agents`, { method: "POST" })
    } catch (e) {}
    onReset()
  }

  // Loading state
  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-header">
          <div>
            <div className="loading-title">PIPELINE RUNNING</div>
            <div className="loading-job-id">JOB {jobId}</div>
          </div>
          <button className="cancel-btn" onClick={handleCancel}>
            CANCEL ×
          </button>
        </div>

        <div className="pipeline-steps">
          {STEPS.map((step) => (
            <div
              key={step.id}
              className={`step ${step.id === currentStep ? "active" : ""} ${step.id < currentStep ? "done" : ""}`}
            >
              <span className="step-num">0{step.id}</span>
              <span className="step-name">{step.name}</span>
              <span className={`step-status ${step.id < currentStep ? "done" : step.id === currentStep ? "running" : "waiting"}`}>
                {step.id < currentStep ? "DONE" : step.id === currentStep ? "RUNNING" : "WAIT"}
              </span>
            </div>
          ))}
        </div>

        <div className="loading-ticker">
          <div className="ticker-label">STATUS</div>
          {ticker}
        </div>
      </div>
    )
  }

  // Results state
  const data = results || []

  return (
    <div className="dashboard">
      <div className="dash-header">
        <div className="dash-title-block">
          <div className="eyebrow">CAMPAIGN RESULTS</div>
          <h1>{data.length} influencers ranked</h1>
        </div>
        <div className="dash-meta">
          <div>JOB {jobId?.slice(0, 8).toUpperCase()}</div>
          <button className="new-search-btn" onClick={onReset}>
            ← NEW CAMPAIGN
          </button>
        </div>
      </div>

      <div className="results-table">
        <div className="table-header">
          <span className="th">INFLUENCER</span>
          <span className="th">FOLLOWERS</span>
          <span className="th">ENGAGEMENT</span>
          <span className="th">PRICE RANGE</span>
          <span className="th">RISK</span>
          <span className="th">SCORE</span>
        </div>
        {data.map((inf, i) => (
          <div
            key={inf.handle}
            className={`result-row ${selected === i ? "selected" : ""}`}
            onClick={() => setSelected(i)}
          >
            <div>
              <div className="influencer-name">@{inf.handle}</div>
              <div className="influencer-platform">{(inf.platform || "").toUpperCase()}</div>
            </div>
            <span className="cell-value">{fmt(inf.followers)}</span>
            <span className="cell-value highlight">
              {inf.engagement_rate ? `${inf.engagement_rate}%` : "—"}
            </span>
            <span className="cell-value">
              {inf.price_low && inf.price_high
                ? `$${fmt(inf.price_low)}–$${fmt(inf.price_high)}`
                : "—"}
            </span>
            <RiskBadge flag={inf.risk_flag} />
            <div className="score-bar">
              <span className={`score-num ${scoreClass(inf.composite_score)}`}>
                {inf.composite_score?.toFixed(1)}
              </span>
            </div>
          </div>
        ))}
      </div>

      {data[selected] && <DossierPanel influencer={data[selected]} />}
    </div>
  )
}