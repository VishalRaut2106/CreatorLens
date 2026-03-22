import { useState, useEffect } from "react"

const API = "http://localhost:8000/api"

export default function CampaignHistory({ onSelect, onBack }) {
  const [campaigns, setCampaigns] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/campaigns`)
      .then(r => r.json())
      .then(data => { setCampaigns(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  return (
    <div style={{ maxWidth: 800 }}>
      <div style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 9, color: "var(--amber)", letterSpacing: "0.3em", marginBottom: 4 }}>CAMPAIGN HISTORY</div>
          <h1 style={{ fontSize: 22, fontWeight: 300 }}>Past campaigns</h1>
        </div>
        <button className="new-search-btn" onClick={onBack}>← NEW CAMPAIGN</button>
      </div>

      <div style={{ border: "1px solid var(--border)" }}>
        {loading && (
          <div style={{ padding: "20px", color: "var(--text-dim)", fontSize: 12 }}>Loading...</div>
        )}
        {!loading && campaigns.length === 0 && (
          <div style={{ padding: "20px", color: "var(--text-dim)", fontSize: 12 }}>No campaigns yet.</div>
        )}
        {campaigns.map((c, i) => {
          const brief = (() => { try { return JSON.parse(c.brief_json) } catch { return {} } })()
          const statusColor = c.status === "complete" ? "var(--green)" : c.status === "failed" ? "var(--red)" : "var(--amber)"
          return (
            <div
              key={c.job_id}
              onClick={() => c.status === "complete" && onSelect(c.job_id)}
              style={{
                display: "grid",
                gridTemplateColumns: "2fr 1fr 1fr 1fr",
                padding: "14px 20px",
                borderBottom: i < campaigns.length - 1 ? "1px solid var(--border)" : "none",
                cursor: c.status === "complete" ? "pointer" : "default",
                gap: 16,
                alignItems: "center",
                transition: "background 0.1s"
              }}
              onMouseOver={e => { if (c.status === "complete") e.currentTarget.style.background = "var(--bg-2)" }}
              onMouseOut={e => e.currentTarget.style.background = "transparent"}
            >
              <div>
                <div style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 500 }}>
                  {brief.niche || "Unknown niche"}
                </div>
                <div style={{ fontSize: 10, color: "var(--text-dim)", marginTop: 2 }}>
                  {brief.target_audience} · {(brief.platforms || []).join(", ")}
                </div>
              </div>
              <div style={{ fontSize: 11, color: statusColor, letterSpacing: "0.1em" }}>
                {c.status.toUpperCase()}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {new Date(c.created_at).toLocaleDateString()}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                {c.status === "complete" ? "VIEW →" : "—"}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
