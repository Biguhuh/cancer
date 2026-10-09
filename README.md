# Cancers, pesticides et alimentation

Étude statistique de l'évolution des cancers en Poitou-Charentes (2008–2023) et de son lien
éventuel avec l'exposition aux pesticides, complétée par une revue de littérature sur les
perturbateurs endocriniens et l'alimentation (conventionnelle vs biologique), avec un focus sur
les enfants. Réalisé pour Santé des Territoires Aigondigné.

## Structure

- `data/` — PDF sources (registre des cancers de Poitou-Charentes), tableaux Excel extraits et
  résultats statistiques exportés.
- `notebooks/exploration_tous.ipynb` — ETL : extraction des tableaux des PDF via `tabula-py`,
  production de `data/table_*.xlsx` et `data/combined_tables.xlsx`.
- `notebooks/exploration_tous_v2.ipynb` — étude statistique complète : régressions avec IC95 %,
  p-values, R², diagnostics de résidus, classification par niveau de présomption (Inserm 2021).
  Exporte `data/site_stats.json` et les graphiques utilisés par le site.
- `docs/SOURCES.md` — bibliographie publique citée par le site.
- `docs/etude-cancers.md` — notes de travail internes, non publiées.
- `scripts/build_site.py` — génère le site statique dans `site/` à partir de
  `data/site_stats.json` et `docs/SOURCES.md`. Ne fait aucun calcul : uniquement de la mise en
  forme, sans dépendance externe.
- `.github/workflows/deploy-site.yml` — déploie `site/` sur GitHub Pages à chaque push sur
  `master` qui touche les données ou le script de génération.

## Mettre à jour l'étude avec de nouvelles données (ex. année 2023)

1. Figer la version publiée avant de toucher aux données : copier `data/site_stats.json` et
   `data/table_tous.xlsx` dans `data/v1/` (à renommer selon la version). L'onglet "Mise à jour"
   du site compare les nouveaux résultats à cette version figée.
2. Télécharger **toutes** les années depuis la page "Les chiffres" du registre, pas seulement la
   nouvelle : le registre republie les années passées avec des effectifs révisés. Les déposer dans
   `data/tous/`, `data/femmes/` et `data/hommes/`, et mettre à jour `REGISTRE_MAJ` dans
   `scripts/build_site.py`.
3. Réexécuter `notebooks/exploration_tous.ipynb` pour régénérer `data/table_*.xlsx` et
   `data/combined_tables.xlsx`.
4. Réexécuter `notebooks/exploration_tous_v2.ipynb` : toutes les statistiques (régressions,
   comparaisons de groupes) se recalculent automatiquement sur la période étendue, sans
   modification de code. Regénère aussi `data/site_stats.json` et les graphiques PNG du site.
5. Committer les fichiers `data/*.xlsx`, `data/*.png` et `data/site_stats.json` mis à jour, puis
   pousser sur `master` : la CI régénère et republie le site automatiquement.

## Installation

Environnement Python dédié (voir `requirements.txt`) et Java, requis par `tabula-py` pour lire les
PDF :

    pip install -r requirements.txt
    sudo apt install default-jre   # ou équivalent selon l'OS

## Site

    python3 scripts/build_site.py
    python3 -m http.server --directory site

Publication GitHub Pages : dans les paramètres du dépôt GitHub, Pages → Source → "GitHub
Actions". Le workflow se déclenche ensuite automatiquement à chaque push pertinent sur `master`.
