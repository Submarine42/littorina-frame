# Littorina — cadre e-ink

Service qui suit le voilier **Littorina** (tracker PredictWind) et prépare les données d'un cadre e-ink.

## Fonctionnement
Toutes les heures, GitHub Actions lance `python -m tracker.run` :
1. **PredictWind** : lecture de la page publique (`__NEXT_DATA__` → `props.pageProps.boatData`), au plus 1×/heure.
2. **Open-Meteo** (Forecast + Marine, sans clé) : vent, houle, température de l'eau et fuseau horaire à la dernière position.
3. **Stats ludiques** (`tracker/stats.py`) : milles, jours, escales (arrêt ≥ 24 h), mode escale / navigation, traversée en cours ; noms d'escales par **Nominatim** (OpenStreetMap), mis en cache.
4. Écriture atomique de `data/state.json`, commité dans le dépôt.
5. **Rendu** (`tracker/render.py`) de la piste UX B « Carte marine » en PNG 800 × 480 via Chromium, réduit aux 5 couleurs pures de la palette (noir, blanc, rouge, jaune, bleu), publié sur **GitHub Pages** seulement si l'image a changé (ETag stable → pas de rafraîchissement inutile de la dalle).

Image pour le cadre : `https://submarine42.github.io/littorina-frame/frame.png` (aperçu : `…/index.html`).

## Pannes
| Situation | Comportement |
|---|---|
| Site injoignable, HTTP ≠ 200 | `fetch_error`, dernier état conservé, nouvel essai à l'heure suivante |
| Structure de page modifiée, >5 % de points illisibles, trace tronquée ou qui recule | `format_changed`, dernier état conservé, job en échec → e-mail GitHub |
| Open-Meteo en panne | météo précédente conservée si le bateau n'a pas bougé, sinon vidée |
| Position de plus de 6 h | `track.stale = true` → le rendu affiche la date des données |
| `state.json` corrompu | redémarrage propre à partir de zéro |

## Mise en place
1. Créer un dépôt GitHub, y pousser ce dossier.
2. *Settings → Secrets and variables → Actions* : secret `PW_TRACKING_URL` = `https://www.predictwind.com/tracking/Littorina`.
3. Onglet *Actions* : lancer « Mise à jour Littorina » une première fois à la main.

## Fond de carte et polices
- `assets/carte-pacifique.svg` : régénérable avec `cd tools/carte && npm ci && node carte-gen.js` (Natural Earth 1:10 m, domaine public).
- `assets/fonts/` : IBM Plex Sans Condensed et IBM Plex Serif (licence OFL, `assets/fonts/OFL.txt`).

## Développement
```
pip install -r requirements.txt pytest
python -m pytest -q
PW_TRACKING_URL=... python -m tracker.run --force
```
