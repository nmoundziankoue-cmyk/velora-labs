# Politique de rétention des données

## Code source client

Quand un dépôt est soumis via `POST /repo` :

1. Il est cloné localement sur disque (`backend/repos/<repo_id>/`).
2. Il est lu et découpé en fragments, qui sont transformés en embeddings et stockés dans ChromaDB.
3. **Le clone local est supprimé immédiatement après l'indexation**, que celle-ci réussisse ou échoue — voir `main.py`, bloc `finally` de `create_repo()`. Il n'existe aucun chemin de sortie de cette fonction qui laisse le code source cloné sur disque.

Durée de vie : le clone local ne survit jamais à la requête `POST /repo` elle-même (de l'ordre de la durée de l'indexation, jamais plus).

## Embeddings

Les fragments de code sont conservés sous forme d'embeddings (vecteurs + texte du fragment) dans ChromaDB (`backend/chroma_data/`), indexés par `repo_id`, tant que le processus backend tourne.

- Il n'existe aujourd'hui aucune purge automatique des embeddings (pas de TTL, pas d'endpoint de suppression). C'est un écart connu, pas encore corrigé — voir le suivi de l'étape correspondante.
- En pratique, sur le déploiement actuel (Render, plan sans disque persistant), `chroma_data/` est perdu à chaque redeploy — mais ce n'est qu'un effet de bord de la configuration d'infrastructure, pas une garantie applicative.

## Accès aux dépôts privés (v1)

**v1 utilise des Personal Access Tokens (PAT) GitHub, scope lecture seule, fournis par l'utilisateur ; une intégration GitHub App avec tokens à courte durée de vie est prévue pour la version suivante.**

Détail de ce que ça implique aujourd'hui :

- Le token est transmis dans le corps de la requête `POST /repo`, jamais dans l'URL ni en query string (donc jamais dans un log d'accès Uvicorn, qui ne journalise que méthode/chemin/statut).
- Il n'est jamais écrit en base — la table `repos` ne contient que `id`, `owner_id`, `repo_url` et `created_at`, jamais `access_token`.
- Il n'est jamais passé à `print()` ni à aucun mécanisme de log. En cas d'échec du clone, l'erreur remontée à l'utilisateur est un message générique — jamais le détail brut de la commande `git` exécutée (qui pourrait contenir le token).
- Il n'existe qu'en variable locale le temps de la requête (`main.py`, fonction `_run_indexing_job`) et est explicitement effacé juste après l'appel de clonage, avant même le reste de l'indexation.
- Recommandation utilisateur : un token *fine-grained* scopé à "Contents: Read-only" sur le seul dépôt concerné, plutôt qu'un token classique à portée large.

## Connexion (Étape 3)

L'authentification se fait par OAuth GitHub (login uniquement, scope vide — voir `github_oauth.py`), distincte du PAT ci-dessus qui sert à cloner un dépôt privé. Deux mécanismes GitHub différents, pas le même flux :

- OAuth ne demande **aucun scope** : l'app ne peut lire que l'identité publique (id, login), pas le code de l'utilisateur.
- La session est un cookie opaque (jeton aléatoire de 32 octets), pas un JWT. Seul son hash SHA-256 est stocké en base (`sessions.token_hash`) — une fuite de la table ne permet pas de rejouer une session existante.
- Chaque `repo` est lié à un `owner_id` obligatoire. `POST /repo`, `GET /repo/{id}/status` et `POST /ask` vérifient tous les trois que l'utilisateur courant est bien le propriétaire ; un repo inexistant et un repo appartenant à quelqu'un d'autre renvoient la même réponse (404), pour ne jamais laisser deviner qu'un `repo_id` existe.

## Ce qui n'est jamais conservé

- Le token d'accès (PAT), au-delà de la durée de l'appel de clonage qui l'a utilisé — voir section "Accès aux dépôts privés" ci-dessus.
- Le jeton de session en clair — seul son hash est stocké (voir "Connexion" ci-dessus).
- Aucune copie du dépôt cloné au-delà de la fenêtre d'indexation décrite plus haut.
