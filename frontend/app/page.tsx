"use client";

import { useRef, useState } from "react";

type RepoResponse = {
  repo_id: string;
  files_found: number;
  indexed_files: number;
  total_chunks: number;
  status: string;
};

type Source = {
  path: string;
  chunk_index: number;
};

type AskResponse = {
  answer?: string;
  sources?: Source[];
  error?: string;
};

const ACCENT = "#4F46E5";

// Repos réels : l'indexing peut prendre 1-2 min. Un statut qui bouge évite
// l'impression de freeze pendant l'attente.
const INDEXING_STAGE_MESSAGES = [
  "Cloning the repository...",
  "Reading source files...",
  "Generating embeddings...",
  "Almost done...",
];
const INDEXING_STAGE_INTERVAL_MS = 6000;

export default function HomePage() {
  const [repoUrl, setRepoUrl] = useState("https://github.com/vercel/next.js");
  const [repoId, setRepoId] = useState("");
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState("Idle");
  const [filesFound, setFilesFound] = useState<number | null>(null);
  const [answer, setAnswer] = useState("");
  const [answerError, setAnswerError] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [loadingRepo, setLoadingRepo] = useState(false);
  const [loadingAsk, setLoadingAsk] = useState(false);
  const stageIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  async function readJsonSafely(res: Response) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }

  async function ingestAndIndexRepo() {
    if (!repoUrl.trim() || loadingRepo) return;

    try {
      setLoadingRepo(true);
      setAnswer("");
      setAnswerError(false);
      setSources([]);

      let stageIndex = 0;
      setStatus(INDEXING_STAGE_MESSAGES[stageIndex]);
      stageIntervalRef.current = setInterval(() => {
        stageIndex = Math.min(stageIndex + 1, INDEXING_STAGE_MESSAGES.length - 1);
        setStatus(INDEXING_STAGE_MESSAGES[stageIndex]);
      }, INDEXING_STAGE_INTERVAL_MS);

      const repoRes = await fetch(`${API_BASE}/repo`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_url: repoUrl,
        }),
      });

      const repoData = await readJsonSafely(repoRes);

      if (!repoRes.ok) {
        setStatus(repoData?.error ?? "Repo ingestion failed. Please try again.");
        return;
      }

      const data = repoData as RepoResponse;
      setRepoId(data.repo_id);
      setFilesFound(data.files_found);
      setStatus(
        `Repo ready — ${data.indexed_files}/${data.files_found} files indexed (${data.total_chunks} chunks). You can ask questions.`
      );
    } catch {
      setStatus("Could not reach the backend. Check your connection and try again.");
    } finally {
      if (stageIntervalRef.current) {
        clearInterval(stageIntervalRef.current);
        stageIntervalRef.current = null;
      }
      setLoadingRepo(false);
    }
  }

  async function askQuestion() {
    if (!question.trim() || !repoId || loadingAsk) return;

    try {
      setLoadingAsk(true);
      setStatus("Asking AI...");
      setAnswer("");
      setAnswerError(false);
      setSources([]);

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

      const askData = (await readJsonSafely(askRes)) as AskResponse | null;

      if (!askRes.ok || askData?.error) {
        setAnswerError(true);
        setAnswer(askData?.error ?? "Something went wrong answering this question.");
        setStatus("Error.");
        return;
      }

      setAnswer(askData?.answer ?? "No answer returned.");
      setSources(askData?.sources ?? []);
      setStatus("Answer received.");
    } catch {
      setAnswerError(true);
      setAnswer("Could not reach the backend. Check your connection and try again.");
      setStatus("Error.");
    } finally {
      setLoadingAsk(false);
    }
  }

  return (
    <main
      style={{
        padding: "40px 24px",
        maxWidth: 760,
        margin: "0 auto",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <header style={{ marginBottom: 32 }}>
        <h1 style={{ margin: 0, fontSize: 28 }}>
          Velora <span style={{ color: ACCENT }}>Labs</span>
        </h1>
        <p style={{ marginTop: 6, color: "#555" }}>
          Point it at a public GitHub repo, then ask questions about the actual code.
        </p>
      </header>

      <section
        style={{
          padding: 20,
          border: "1px solid #e5e5e5",
          borderRadius: 12,
          background: "#fafafa",
        }}
      >
        <label style={{ fontWeight: 600, fontSize: 14 }}>1. Index a repository</label>
        <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
          <input
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="https://github.com/user/repo"
            style={{
              flex: 1,
              padding: 10,
              border: "1px solid #ccc",
              borderRadius: 8,
              fontSize: 14,
            }}
          />
          <button
            onClick={ingestAndIndexRepo}
            disabled={loadingRepo || !repoUrl.trim()}
            style={{
              padding: "10px 16px",
              background: !repoUrl.trim() ? "#9ca3af" : ACCENT,
              color: "white",
              borderRadius: 8,
              border: "none",
              cursor: loadingRepo || !repoUrl.trim() ? "not-allowed" : "pointer",
              fontSize: 14,
              whiteSpace: "nowrap",
            }}
          >
            {loadingRepo ? (
              <>
                <span className="spinner" />
                Working...
              </>
            ) : (
              "Ingest Repo"
            )}
          </button>
        </div>

        <div style={{ marginTop: 14, fontSize: 14, color: "#333" }}>
          <strong>Status:</strong> {status}
        </div>

        {repoId && (
          <div style={{ marginTop: 10, fontSize: 13, color: "#666" }}>
            <div>
              <strong>Repo ID:</strong> {repoId}
            </div>
            <div>
              <strong>Files found:</strong> {filesFound ?? 0}
            </div>
          </div>
        )}
      </section>

      <section
        style={{
          marginTop: 20,
          padding: 20,
          border: "1px solid #e5e5e5",
          borderRadius: 12,
        }}
      >
        <label style={{ fontWeight: 600, fontSize: 14 }}>2. Ask the codebase</label>
        <div style={{ marginTop: 10, display: "flex", gap: 10 }}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="How does routing work in this repo?"
            style={{
              flex: 1,
              padding: 10,
              border: "1px solid #ccc",
              borderRadius: 8,
              fontSize: 14,
            }}
          />
          <button
            onClick={askQuestion}
            disabled={loadingAsk || !repoId || !question.trim()}
            style={{
              padding: "10px 16px",
              background: !repoId || !question.trim() ? "#9ca3af" : ACCENT,
              color: "white",
              borderRadius: 8,
              border: "none",
              cursor: loadingAsk || !repoId || !question.trim() ? "not-allowed" : "pointer",
              fontSize: 14,
            }}
          >
            {loadingAsk ? (
              <>
                <span className="spinner" />
                Working...
              </>
            ) : (
              "Ask"
            )}
          </button>
        </div>

        {!repoId && (
          <p style={{ marginTop: 10, fontSize: 13, color: "#888" }}>
            Index a repository above before asking a question.
          </p>
        )}

        {answer && (
          <div
            style={{
              marginTop: 18,
              padding: 16,
              border: `1px solid ${answerError ? "#fca5a5" : "#ddd"}`,
              borderRadius: 8,
              background: answerError ? "#fef2f2" : "#f8f8ff",
            }}
          >
            <strong style={{ color: answerError ? "#b91c1c" : "#111" }}>
              {answerError ? "Error" : "AI Answer"}
            </strong>
            <p
              style={{
                marginTop: 8,
                whiteSpace: "pre-wrap",
                color: answerError ? "#b91c1c" : "#111",
              }}
            >
              {answer}
            </p>

            {!answerError && sources.length > 0 && (
              <div style={{ marginTop: 14, borderTop: "1px solid #e5e5e5", paddingTop: 12 }}>
                <strong style={{ fontSize: 13 }}>Sources</strong>
                <ul style={{ marginTop: 6, paddingLeft: 18, fontSize: 13, color: "#555" }}>
                  {sources.map((s, i) => (
                    <li key={`${s.path}-${s.chunk_index}-${i}`}>
                      {s.path} <span style={{ color: "#999" }}>(chunk {s.chunk_index})</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>
    </main>
  );
}
