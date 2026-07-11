# Velora Labs

Assistant IA qui indexe un dépôt GitHub (embeddings + ChromaDB) et répond à des questions sur son code via Gemini (RAG).

## Stack

- **Backend** : FastAPI, ChromaDB (vector store), Google Gemini (`gemini-embedding-001` pour les embeddings, `gemini-flash-latest` pour le chat).
- **Frontend** : Next.js (App Router).

## ⚠️ Persistance non garantie entre redeploys

Ce projet utilise une persistance locale sur disque (`backend/repos_state.json` et `backend/chroma_data/`) et n'a **pas de disque persistant Render** configuré (choix assumé pour ce portfolio, pas un oubli — un disque payant n'est pas nécessaire pour une démo).

**Conséquence : à chaque redeploy du backend sur Render, tous les repos indexés sont perdus.** Il faut réindexer un repo (`POST /repo`) après chaque déploiement avant de pouvoir l'interroger avec `/ask`.

## Lancer en local

### Backend

```bash
cd backend
cp .env.example .env   # puis renseigne GEMINI_API_KEY (récupérable sur aistudio.google.com/apikey)
pip install -r requirements.txt
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE, fallback localhost:8000 si non défini
npm install
npm run dev
```

## Variables d'environnement

| Variable | Où | Description |
|---|---|---|
| `GEMINI_API_KEY` | backend | Clé API Gemini (obligatoire, le serveur refuse de démarrer sans). |
| `FRONTEND_URL` | backend | URL du frontend en prod, pour CORS (optionnel — `localhost:3000` toujours autorisé). |
| `NEXT_PUBLIC_API_BASE` | frontend | URL du backend (optionnel en dev — fallback `localhost:8000`). |

## Déploiement

- Backend : Docker sur Render (`backend/Dockerfile`, `render.yaml` à la racine).
- Frontend : Vercel (déploiement Next.js standard, pas de config particulière requise).
