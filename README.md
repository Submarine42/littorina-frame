# Littorina — cadre e-ink

Service qui suit le voilier **Littorina** (tracker PredictWind) et prépare les données d'un cadre e-ink.

## Fonctionnement
Toutes les heures, GitHub Actions lance `python -m tracker.run` :
1. **PredictWind** : lecture de la page publique (`__NEXT_DATA__` → `props.pageProps.boatData`), au plus 1×/heure.
2. **Open-Meteo** (Forecast + Marine, sans clé) : vent, houle, température de l'eau et fuseau horaire à la dernière position.
3. Écriture atomique de `data/state.json`, commité dans le dépôt.

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

## Développement
```
pip install -r requirements.txt pytest
python -m pytest -q
PW_TRACKING_URL=... python -m tracker.run --force
```
