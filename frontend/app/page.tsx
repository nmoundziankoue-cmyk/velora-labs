"use client";

import { useEffect, useState } from "react";

type RepoQueuedResponse = {
  repo_id: string;
  status: string;
};

type RepoStage = "queued" | "cloning" | "reading_files" | "indexing" | "ready" | "error";

type RepoStatusResponse = {
  repo_id: string;
  stage: RepoStage;
  files_found: number | null;
  indexed_files: number;
  total_chunks: number;
  error: string | null;
};

type Source = {
  path: string;
  chunk_index: number;
  line_start: number | null;
  line_end: number | null;
};

type AskResponse = {
  answer?: string;
  sources?: Source[];
  error?: string;
};

type CurrentUser = {
  id: string;
  github_login: string;
};

const ACCENT = "#4F46E5";

// Indexation réelle : le backend indexe en arrière-plan (voir POST /repo,
// qui répond 202 immédiatement) et ce frontend interroge son statut réel via
// GET /repo/{id}/status — plus de minuteur cosmétique déconnecté du backend.
const POLL_INTERVAL_MS = 2000;

const STAGE_LABELS: Record<RepoStage, string> = {
  queued: "Queued...",
  cloning: "Cloning the repository...",
  reading_files: "Reading source files...",
  indexing: "Indexing files...",
  ready: "Ready.",
  error: "Error.",
};

function formatSourceLocation(source: Source): string {
  if (source.line_start == null || source.line_end == null) {
    // Filet de sécurité si jamais line_start/line_end manquent (ne devrait
    // pas arriver : le backend les propage depuis l'Étape 2 du chunker
    // ligne-aware) — mieux vaut afficher le chunk que rien du tout.
    return `chunk ${source.chunk_index}`;
  }
  if (source.line_start === source.line_end) {
    return `line ${source.line_start}`;
  }
  return `lines ${source.line_start}-${source.line_end}`;
}

export default function HomePage() {
  const [repoUrl, setRepoUrl] = useState("https://github.com/vercel/next.js");
  const [accessToken, setAccessToken] = useState("");
  const [repoId, setRepoId] = useState("");
  const [pollingRepoId, setPollingRepoId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState("Idle");
  const [filesFound, setFilesFound] = useState<number | null>(null);
  const [answer, setAnswer] = useState("");
  const [answerError, setAnswerError] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [loadingRepo, setLoadingRepo] = useState(false);
  const [loadingAsk, setLoadingAsk] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  useEffect(() => {
    let cancelled = false;

    async function checkAuth() {
      try {
        const res = await fetch(`${API_BASE}/auth/me`, { credentials: "include" });
        if (cancelled) return;
        if (res.ok) {
          setCurrentUser((await res.json()) as CurrentUser);
        } else {
          setCurrentUser(null);
        }
      } catch {
        if (!cancelled) setCurrentUser(null);
      } finally {
        if (!cancelled) setAuthChecked(true);
      }
    }

    checkAuth();
    return () => {
      cancelled = true;
    };
  }, [API_BASE]);

  async function logOut() {
    try {
      await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" });
    } catch {
      // Rien de plus à faire : on efface l'état local dans tous les cas ci-dessous.
    }
    setCurrentUser(null);
    setRepoId("");
    setPollingRepoId(null);
    setStatus("Idle");
  }

  useEffect(() => {
    if (!pollingRepoId) return;

    let cancelled = false;

    async function pollOnce() {
      let res: Response;
      try {
        res = await fetch(`${API_BASE}/repo/${pollingRepoId}/status`, { credentials: "include" });
      } catch {
        if (!cancelled) {
          setStatus("Lost connection while checking indexing progress. Retrying...");
        }
        return;
      }
      if (cancelled) return;

      const data = (await readJsonSafely(res)) as RepoStatusResponse | null;

      if (!res.ok || !data) {
        setStatus("Lost track of indexing progress. Please try again.");
        setLoadingRepo(false);
        setPollingRepoId(null);
        return;
      }

      if (data.stage === "error") {
        setStatus(data.error ?? "Indexing failed. Please try again.");
        setLoadingRepo(false);
        setPollingRepoId(null);
        return;
      }

      if (data.stage === "ready") {
        setRepoId(data.repo_id);
        setFilesFound(data.files_found);
        setStatus(
          `Repo ready — ${data.indexed_files}/${data.files_found} files indexed (${data.total_chunks} chunks). You can ask questions.`
        );
        setLoadingRepo(false);
        setPollingRepoId(null);
        return;
      }

      if (data.stage === "indexing" && data.files_found) {
        setStatus(
          `Indexing files... ${data.indexed_files}/${data.files_found} (${data.total_chunks} chunks so far)`
        );
      } else {
        setStatus(STAGE_LABELS[data.stage]);
      }
    }

    pollOnce();
    const intervalId = setInterval(pollOnce, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [pollingRepoId, API_BASE]);

  async function readJsonSafely(res: Response) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }

  async function ingestAndIndexRepo() {
    if (!repoUrl.trim() || loadingRepo || !currentUser) return;

    // Garde-fou minimal, pas une validation de format : un token non vide
    // qui devient vide après trim(), ou qui contient un espace/saut de
    // ligne, trahit presque toujours une erreur de copier-coller.
    const trimmedToken = accessToken.trim();
    if (accessToken && (!trimmedToken || /\s/.test(trimmedToken))) {
      setStatus("GitHub token looks off — empty after trimming, or contains a space/newline.");
      return;
    }

    try {
      setLoadingRepo(true);
      setStatus("Queuing repository...");
      setAnswer("");
      setAnswerError(false);
      setSources([]);

      const repoRes = await fetch(`${API_BASE}/repo`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_url: repoUrl,
          ...(trimmedToken ? { access_token: trimmedToken } : {}),
        }),
      });

      if (repoRes.status === 401) {
        setStatus("Your session expired. Please log in again.");
        setCurrentUser(null);
        setLoadingRepo(false);
        return;
      }

      const repoData = (await readJsonSafely(repoRes)) as RepoQueuedResponse | null;

      if (!repoRes.ok || !repoData) {
        setStatus((repoData as { error?: string } | null)?.error ?? "Repo ingestion failed. Please try again.");
        setLoadingRepo(false);
        return;
      }

      // La suite (cloning → indexing → ready) est suivie par l'effet de
      // polling ci-dessus, qui remet loadingRepo à false une fois le job
      // terminé (succès ou erreur) — pas ce bloc.
      setPollingRepoId(repoData.repo_id);
    } catch {
      setStatus("Could not reach the backend. Check your connection and try again.");
      setLoadingRepo(false);
    } finally {
      // Jamais conservé au-delà d'une tentative : le champ se vide que la
      // requête réussisse ou échoue (cohérent avec la politique "jamais
      // persisté" côté backend — voir POLICY.md).
      setAccessToken("");
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
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          repo_id: repoId,
          question,
        }),
      });

      if (askRes.status === 401) {
        setAnswerError(true);
        setAnswer("Your session expired. Please log in again.");
        setStatus("Error.");
        setCurrentUser(null);
        return;
      }

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
      <header
        style={{
          marginBottom: 32,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 16,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 28 }}>
            Velora <span style={{ color: ACCENT }}>Labs</span>
          </h1>
          <p style={{ marginTop: 6, color: "#555" }}>
            Point it at a public or private GitHub repo, then ask questions about the actual code.
          </p>
        </div>

        {authChecked && (
          <div style={{ flexShrink: 0, textAlign: "right" }}>
            {currentUser ? (
              <>
                <div style={{ fontSize: 13, color: "#555" }}>
                  Signed in as <strong>{currentUser.github_login}</strong>
                </div>
                <button
                  onClick={logOut}
                  style={{
                    marginTop: 4,
                    padding: "4px 10px",
                    fontSize: 12.5,
                    background: "none",
                    border: "1px solid #ccc",
                    borderRadius: 6,
                    cursor: "pointer",
                  }}
                >
                  Log out
                </button>
              </>
            ) : (
              <a
                href={`${API_BASE}/auth/github/login`}
                style={{
                  display: "inline-block",
                  padding: "8px 14px",
                  background: ACCENT,
                  color: "white",
                  borderRadius: 8,
                  fontSize: 14,
                  textDecoration: "none",
                  whiteSpace: "nowrap",
                }}
              >
                Log in with GitHub
              </a>
            )}
          </div>
        )}
      </header>

      {authChecked && !currentUser && (
        <p
          style={{
            marginBottom: 20,
            padding: 12,
            background: "#fff7ed",
            border: "1px solid #fed7aa",
            borderRadius: 8,
            fontSize: 13.5,
            color: "#9a3412",
          }}
        >
          Log in with GitHub to index a repository — each indexed repo is private to your account.
        </p>
      )}

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
            disabled={loadingRepo || !repoUrl.trim() || !currentUser}
            style={{
              padding: "10px 16px",
              background: !repoUrl.trim() || !currentUser ? "#9ca3af" : ACCENT,
              color: "white",
              borderRadius: 8,
              border: "none",
              cursor: loadingRepo || !repoUrl.trim() || !currentUser ? "not-allowed" : "pointer",
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

        <div style={{ marginTop: 12 }}>
          <label htmlFor="access-token" style={{ fontSize: 13, fontWeight: 600, color: "#333" }}>
            GitHub token (optional — private repos only)
          </label>
          <input
            id="access-token"
            type="password"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="ghp_... or github_pat_..."
            autoComplete="off"
            style={{
              marginTop: 6,
              width: "100%",
              padding: 10,
              border: "1px solid #ccc",
              borderRadius: 8,
              fontSize: 14,
            }}
          />
          <p style={{ marginTop: 6, fontSize: 12.5, color: "#777" }}>
            Only needed for private repositories. Use the most restrictive read-only scope
            available — a fine-grained token scoped to &quot;Contents: Read-only&quot; on this
            repo, or classic <code>repo</code> read access if your org requires classic tokens.{" "}
            <a
              href="https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens"
              target="_blank"
              rel="noreferrer"
            >
              How to create one
            </a>
            . The token is used in memory only, for this clone, then discarded immediately —
            never stored, never logged. This field clears itself after each attempt.
          </p>
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
                      {s.path}{" "}
                      <span style={{ color: "#999" }}>({formatSourceLocation(s)})</span>
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
