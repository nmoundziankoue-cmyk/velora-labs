"use client";

import { useState } from "react";

type RepoResponse = {
  repo_id: string;
  files_found: number;
  status: string;
};

type AskResponse = {
  repo_id?: string;
  question?: string;
  answer?: string;
  files_considered?: string[];
  error?: string;
};

export default function DashboardPage() {
  const [repoUrl, setRepoUrl] = useState("https://github.com/vercel/next.js");
  const [repoId, setRepoId] = useState("");
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState("Idle");
  const [filesFound, setFilesFound] = useState<number | null>(null);
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);

  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  async function ingestAndIndexRepo() {
    try {
      setLoading(true);
      setStatus("Ingesting and indexing repo — this can take a minute for larger repos...");
      setAnswer("");

      const repoRes = await fetch(`${API_BASE}/repo`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_url: repoUrl,
        }),
      });

      if (!repoRes.ok) {
        throw new Error("Repo ingestion failed");
      }

      const repoData: RepoResponse = await repoRes.json();
      setRepoId(repoData.repo_id);
      setFilesFound(repoData.files_found);

      setStatus("Repo ready. You can ask questions.");
    } catch (error) {
      console.error(error);
      setStatus("Something failed. Check backend terminal.");
    } finally {
      setLoading(false);
    }
  }

  async function askQuestion() {
    try {
      if (!repoId) {
        setStatus("Ingest and index a repo first.");
        return;
      }

      setLoading(true);
      setStatus("Asking AI...");

      const askRes = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_id: repoId,
          question,
        }),
      });

      if (!askRes.ok) {
        throw new Error("Ask failed");
      }

      const askData: AskResponse = await askRes.json();

      if (askData.error) {
        setAnswer(askData.error);
      } else {
        setAnswer(askData.answer ?? "No answer returned.");
      }

      setStatus("Answer received.");
    } catch (error) {
      console.error(error);
      setStatus("Question failed. Check backend.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={{ padding: 40, maxWidth: 900 }}>
      <h1>Velora Dashboard</h1>
      <p>Connect a GitHub repository to analyze the code.</p>

      <div style={{ marginTop: 20 }}>
        <input
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/user/repo"
          style={{
            padding: 10,
            width: 420,
            border: "1px solid #ccc",
            borderRadius: 6,
          }}
        />
        <button
          onClick={ingestAndIndexRepo}
          disabled={loading}
          style={{
            marginLeft: 10,
            padding: 10,
            background: "#4F46E5",
            color: "white",
            borderRadius: 6,
            border: "none",
            cursor: "pointer",
          }}
        >
          {loading ? (
            <>
              <span className="spinner" />
              Working...
            </>
          ) : (
            "Ingest Repo"
          )}
        </button>
      </div>

      <div style={{ marginTop: 20 }}>
        <strong>Status:</strong> {status}
      </div>

      {repoId && (
        <div style={{ marginTop: 16 }}>
          <div>
            <strong>Repo ID:</strong> {repoId}
          </div>
          <div>
            <strong>Files found:</strong> {filesFound ?? 0}
          </div>
        </div>
      )}

      <hr style={{ margin: "30px 0" }} />

      <h2>Ask the codebase</h2>
      <div style={{ marginTop: 12 }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="How routing works in this repo?"
          style={{
            padding: 10,
            width: 420,
            border: "1px solid #ccc",
            borderRadius: 6,
          }}
        />
        <button
          onClick={askQuestion}
          disabled={loading}
          style={{
            marginLeft: 10,
            padding: 10,
            background: "#4F46E5",
            color: "white",
            borderRadius: 6,
            border: "none",
            cursor: "pointer",
          }}
        >
          {loading ? (
            <>
              <span className="spinner" />
              Working...
            </>
          ) : (
            "Ask"
          )}
        </button>
      </div>

      {answer && (
        <div
          style={{
            marginTop: 20,
            padding: 16,
            border: "1px solid #ddd",
            borderRadius: 8,
            background: "#fafafa",
          }}
        >
          <strong>AI Answer:</strong>
          <p style={{ marginTop: 8 }}>{answer}</p>
        </div>
      )}
    </main>
  );
}