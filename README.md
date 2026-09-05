# Velora Labs

Assistant IA qui indexe un dépôt GitHub (embeddings + ChromaDB) et répond à des questions sur son code via Gemini (RAG).

**Nouveau sur Velora ?** Ce README documente le projet pour qui le développe/déploie. Pour l'utiliser (créer un token, indexer un premier repo, lire une réponse), voir [docs/getting-started.md](docs/getting-started.md).

## Stack

- **Backend** : FastAPI, Postgres (utilisateurs, repos, jobs d'indexation, sessions) via SQLAlchemy, OAuth GitHub (connexion) via `urllib` stdlib, ChromaDB (vector store), Google Gemini (`gemini-embedding-001` pour les embeddings, `gemini-flash-latest` pour le chat).
- **Frontend** : Next.js (App Router).

## ⚠️ Persistance partielle entre redeploys

Les métadonnées (repos indexés, statut d'indexation) vivent dans Postgres et **survivent aux redeploys**. En revanche, **le contenu réellement indexé (`backend/chroma_data/`) n'a pas de disque persistant Render configuré** et repart à vide à chaque redeploy.

**Conséquence connue et pas encore corrigée : après un redeploy, `GET /repo/{id}/status` répond toujours `"ready"` pour un repo indexé avant le redeploy (Postgres s'en souvient), mais `POST /ask` sur ce même repo renvoie une réponse dégradée et confuse plutôt qu'une erreur claire — le contexte réel a disparu de Chroma sans que rien ne le signale.** Tant que ce n'est pas corrigé, considère qu'un repo doit être ré-indexé (`POST /repo`) après chaque déploiement du backend, même si son statut affiche "ready".

## Lancer en local

### Backend

```bash
# Postgres local (une fois) :
brew install postgresql@16
createdb velora_dev

cd backend
cp .env.example .env   # GEMINI_API_KEY, DATABASE_URL, GITHUB_OAUTH_CLIENT_ID/SECRET (voir plus bas)
pip install -r requirements.txt
uvicorn main:app --reload   # crée les tables au démarrage si elles n'existent pas
```

**Connexion GitHub en local** : crée une GitHub OAuth App sur [github.com/settings/developers](https://github.com/settings/developers) avec comme "Authorization callback URL" `http://localhost:8000/auth/github/callback`, puis renseigne `GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` dans `.env`. Sans ça, le serveur refuse de démarrer (même échec-rapide que pour `GEMINI_API_KEY`/`DATABASE_URL`).

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
| `DATABASE_URL` | backend | URL de connexion Postgres (obligatoire, le serveur refuse de démarrer sans). Injectée automatiquement en prod par Render (voir render.yaml). |
| `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` | backend | Identifiants de la GitHub OAuth App pour la connexion (obligatoires, le serveur refuse de démarrer sans). À créer sur github.com/settings/developers. |
| `BACKEND_URL` | backend | URL publique de ce backend, pour construire l'URL de callback OAuth (optionnel en dev — fallback `http://localhost:8000`). |
| `FRONTEND_URL` | backend | URL du frontend en prod, pour CORS et redirection post-connexion (optionnel — `localhost:3000` toujours autorisé). |
| `NEXT_PUBLIC_API_BASE` | frontend | URL du backend (optionnel en dev — fallback `localhost:8000`). |

## Déploiement

- Backend : Docker sur Render (`backend/Dockerfile`, `render.yaml` à la racine, qui déclare aussi la base Postgres `velora-db`).
- Frontend : Vercel (déploiement Next.js standard, pas de config particulière requise).

### Étapes manuelles Render (Postgres)

`render.yaml` déclare la base Postgres et la variable `DATABASE_URL`, mais certaines actions ne peuvent pas être automatisées depuis ce dépôt :

1. Vérifier sur le dashboard Render que le plan Postgres `free` est toujours proposé pour une nouvelle base au moment du déploiement (l'offre gratuite a changé plusieurs fois par le passé) — ajuster `plan:` dans `render.yaml` sinon.
2. Si le service backend existe déjà sur Render (déployé avant l'ajout de ce `render.yaml`), un `git push` seul ne suffit pas forcément : ouvrir le dashboard Render et lancer une synchronisation du Blueprint pour qu'il crée la base `velora-db` et relie `DATABASE_URL` au service existant.
3. Confirmer dans l'onglet "Environment" du service backend que `DATABASE_URL` est bien renseignée après la synchronisation (elle doit apparaître comme liée à `velora-db`, pas comme une valeur à saisir à la main).

### Étapes manuelles GitHub OAuth App (connexion)

Impossible à automatiser depuis ce dépôt — nécessite un compte GitHub avec accès au dashboard :

1. Créer une OAuth App sur [github.com/settings/developers](https://github.com/settings/developers) → "New OAuth App".
2. Homepage URL : l'URL Vercel du frontend en prod. Authorization callback URL : `<BACKEND_URL>/auth/github/callback` — doit correspondre **exactement** à la valeur de `BACKEND_URL` définie sur Render (GitHub rejette l'échange sinon, même une différence de trailing slash).
3. Copier le "Client ID" et générer un "Client secret", puis les renseigner dans les variables d'environnement `GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` du service backend sur Render.
4. Définir aussi `BACKEND_URL` sur Render avec l'URL publique réelle du service (ex: `https://velora-backend.onrender.com`) — sans ça, l'URL de callback construite pointera vers `localhost` et l'échange OAuth échouera en prod.
