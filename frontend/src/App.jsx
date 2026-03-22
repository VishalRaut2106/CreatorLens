import { useState } from "react"
import BriefForm from "./components/BriefForm"
import Dashboard from "./components/Dashboard"
import CampaignHistory from "./components/CampaignHistory"
import "./App.css"

export default function App() {
  const [screen, setScreen] = useState("form") // "form" | "loading" | "results"
  const [jobId, setJobId] = useState(null)
  const [results, setResults] = useState(null)

  const handleSubmit = (id) => {
    setJobId(id)
    setScreen("loading")
  }

  const handleComplete = (data) => {
    setResults(data)
    setScreen("results")
  }

  const handleReset = () => {
    setScreen("form")
    setJobId(null)
    setResults(null)
  }

  const handleSelectFromHistory = async (jobId) => {
    setJobId(jobId)
    const resp = await fetch(`http://localhost:8000/api/status/${jobId}`)
    const data = await resp.json()
    setResults(data.results)
    setScreen("results")
  }

  return (
    <div className="app">
      <header className="header">
        <div className="header-left">
          <span className="logo">CREATORLENS</span>
          <span className="logo-tag">INFLUENCER INTELLIGENCE</span>
        </div>
        <div className="header-right">
          <span className="status-dot" />
          <span className="status-text">LIVE</span>
          <button
            onClick={() => setScreen("history")}
            style={{
              background: "transparent",
              border: "1px solid var(--border-bright)",
              color: "var(--text-secondary)",
              fontFamily: "var(--mono)",
              fontSize: "10px",
              letterSpacing: "0.15em",
              padding: "4px 12px",
              cursor: "pointer",
              marginLeft: "16px"
            }}
          >
            HISTORY
          </button>
        </div>
      </header>

      <main className="main">
        {screen === "form" && <BriefForm onSubmit={handleSubmit} />}
        {screen === "history" && (
          <CampaignHistory
            onSelect={handleSelectFromHistory}
            onBack={() => setScreen("form")}
          />
        )}
        {screen === "loading" && (
          <Dashboard
            jobId={jobId}
            onComplete={handleComplete}
            onReset={handleReset}
            loading={true}
          />
        )}
        {screen === "results" && (
          <Dashboard
            jobId={jobId}
            results={results}
            onReset={handleReset}
            loading={false}
          />
        )}
      </main>
    </div>
  )
}