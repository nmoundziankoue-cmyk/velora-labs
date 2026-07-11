"use client";

import { useState } from "react";

export default function Dashboard() {
  const [repoUrl, setRepoUrl] = useState("");
  const [repoId, setRepoId] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [status, setStatus] = useState("");

  const API = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  async function ingestRepo() {
    setStatus("Ingesting and indexing repo...");

    const res = await fetch(API + "/repo", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        repo_url: repoUrl,
      }),
    });

    const data = await res.json();

    setRepoId(data.repo_id);
    setStatus("Repo ready.");
  }

  async function askQuestion() {
    setStatus("AI thinking...");

    const res = await fetch(API + "/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        repo_id: repoId,
        question: question,
      }),
    });

    const data = await res.json();

    setAnswer(data.answer);
    setStatus("Done.");
  }

  return (
    <main style={{ padding: 40 }}>
      <h1>Velora Dashboard</h1>

      <p>Connect a GitHub repository</p>

      <input
        placeholder="https://github.com/user/repo"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        style={{ width: 400, padding: 10 }}
      />

      <button onClick={ingestRepo} style={{ marginLeft: 10 }}>
        Ingest Repo
      </button>

      <p>{status}</p>

      <hr />

      <h2>Ask the codebase</h2>

      <input
        placeholder="How authentication works?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        style={{ width: 400, padding: 10 }}
      />

      <button onClick={askQuestion} style={{ marginLeft: 10 }}>
        Ask
      </button>

      <p>{answer}</p>
    </main>
  );
}