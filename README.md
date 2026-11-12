# Agent presse quotidien

Workflow Mistral qui publie chaque jour sur Slack un brief d'actualité **vérifié par croisement des sources**, de Médiapart à Frontières.

## Sources

Le workflow interroge chaque jour les flux RSS de 10 médias français couvrant un large spectre éditorial :

| Pôle | Médias |
| --- | --- |
| Investigation / indépendant | Médiapart, Frontières, Blast, Le Média, Orient XXI |
| Presse généraliste | Le Monde, Libération, Le Figaro |
| Économie / service public | Les Échos, France Info |

Chaque source peut tomber en panne sans faire échouer le workflow : le brief signale les sources indisponibles du jour.

## Fonctionnement

1. **Collecte** — activité `fetch_feeds` : télécharge les flux RSS en parallèle et normalise les dépêches.
2. **Vérification croisée** — activité `verify_stories` : un modèle Mistral regroupe les dépêches par sujet, liste tous les médias qui rapportent chaque information et note ce qui la renforce ou l'affaiblit.
3. **Fiabilité calculée en code** — `compute_reliability` : une information n'est marquée « ✅ Confirmé » que si **au moins deux médias distincts** la rapportent ; sinon « ⚠️ Source unique » (à considérer avec prudence). Le modèle ne peut pas contourner cette règle.
4. **Publication** — activité `post_slack` : publie le brief sur Slack avec liens vers chaque article.

Le workflow `brief-quotidien` accepte en entrée un `BriefInput` (`dry_run`, `max_articles_per_source`, `max_stories`, `model`). Avec `dry_run=true`, le brief est construit mais pas publié.

## Exécution manuelle (sans déploiement)

Le moyen le plus simple de l'utiliser sans déploiement managé : une commande qui démarre le worker, exécute le workflow une fois, affiche le brief puis s'arrête.

1. Copiez `.env.example` en `.env` et renseignez `MISTRAL_API_KEY` (niveau gratuit disponible sur [console.mistral.ai](https://console.mistral.ai/)).
2. Pour un essai (affichage du brief, aucune publication) :

```bash
uv run python -m worker.entrypoints.run_once
```

3. Pour publier le brief sur Slack, renseignez aussi `SLACK_BOT_TOKEN` et `SLACK_CHANNEL` dans `.env`, puis :

```bash
uv run python -m worker.entrypoints.run_once --post
```

Pour l'automatiser tous les jours à 18h00 sans payer de déploiement managé, planifiez la commande `--post` avec le planificateur de votre machine (cron, Task Scheduler…).

## Développement local

```bash
uv sync --all-groups
uv run pytest
```

Pour lancer un worker local en continu, copiez `.env.example` en `.env` avec votre `MISTRAL_API_KEY`, puis :

```bash
uv run python -m worker.entrypoints.worker
```

Exécuter le workflow en mode essai (aucune publication) :

```bash
curl -X POST https://api.mistral.ai/v1/workflows/brief-quotidien/execute \
  -H "Authorization: Bearer $MISTRAL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"dry_run": true}}'
```

## Déploiement managé

- Dépôt GitHub : `cyclamenblanc28-ship-it/agent-presse-quotidien`
- Dockerfile : `deploy/docker/Dockerfile.worker` (contexte de build : racine du dépôt)
- Secrets requis (Mistral Studio → Secrets, liés au déploiement) :
  - `SLACK_BOT_TOKEN` — token de bot Slack avec le scope `chat:write`
  - `SLACK_CHANNEL` — canal de publication (ex. `#actu`)

Le worker s'authentifie automatiquement auprès de Mistral via le token de service account injecté par la plateforme (`mistralai-workflows` ≥ 3.10 requis).

## Planification (déploiement managé)

Sur un plan compatible, le workflow peut être planifié chaque jour à 18h00 (heure de Paris) via un schedule Mistral Workflows (`time_zone_name=Europe/Paris`, politique de chevauchement `SKIP`).
