import { useState } from "react"
import BriefForm from "./components/BriefForm"
import Dashboard from "./components/Dashboard"
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
        </div>
      </header>

      <main className="main">
        {screen === "form" && <BriefForm onSubmit={handleSubmit} />}
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