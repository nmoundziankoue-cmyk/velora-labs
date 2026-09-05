# Getting started with Velora

Velora indexes a GitHub repository and lets you ask questions about its actual code. This guide covers the three things you need: connecting a repo (including a private one), asking a good question, and reading the answer correctly.

## 1. Log in

Click **Log in with GitHub** in the top right. Velora only ever reads your public GitHub identity (your username) to know which repos are yours — this login does not grant it any access to your code.

## 2. Index a repository

Paste a GitHub URL and click **Ingest Repo**. For a public repo, that's it.

### Indexing a private repository

Private repos need a Personal Access Token (PAT) so Velora's server can clone them. Use the narrowest scope you can:

- **Fine-grained token (recommended)**: [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) → under "Repository access", select only the repo you want to index → under "Permissions", set **Contents: Read-only**. Nothing else is needed.
- **Classic token (only if your organization requires it)**: [github.com/settings/tokens/new](https://github.com/settings/tokens/new) → check only the **repo** scope. Classic tokens can't be scoped to a single repository, so this grants read access to everything you can already access — prefer fine-grained tokens whenever your org allows them.

Paste the token into the "GitHub token" field before clicking Ingest Repo. It is used once to clone the repo and is never stored — see the token field's own description in the app, or `backend/POLICY.md` in the repository for the exact guarantee.

### While it's indexing

The status line shows real progress (files and chunks processed), not a fake timer. If some files fail — usually because they're too large or aren't valid text — you'll see them listed under the progress bar with a reason. That's expected on some repos and doesn't mean indexing failed overall; check `indexed_files` vs `files_found` to see how much of the repo actually made it in.

## 3. Ask a question, and know what to trust

Once the repo is ready, ask a question in plain English (e.g. "How does this project handle authentication?").

The response has two parts, and they are **not equally reliable**:

- **The written answer** is Gemini's summary in its own words. It's usually accurate, but it's a language model's interpretation — treat it as a fast first read, not a verified fact.
- **Verified sources**, shown below the answer, list the exact file and line range each piece of context came from. This is the only part of the response backed by something concrete: real lines in the real file at the time of indexing. If you're about to act on what the answer says, open the cited lines yourself and confirm.

If the answer seems off or incomplete, it's often because the relevant code wasn't among the handful of chunks retrieved for that question — try rephrasing, or asking about a more specific file or function.

## Limits worth knowing about (this phase)

- Repos over 1000 matching source files aren't indexed (a "repo too large" error explains this immediately).
- Each indexed repo is private to your account — no one else can query it, and you can't query anyone else's.
- A backend redeploy currently wipes indexed content even though the repo's status may still say "ready" — if a question suddenly returns an empty or confused answer for a repo you indexed a while ago, re-index it.
