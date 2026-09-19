#!/usr/bin/env python3
"""Génère le site statique (site/) à partir de data/site_stats.json et docs/SOURCES.md.

Ne fait aucun calcul statistique : toute la logique d'analyse vit dans
notebooks/exploration_tous_v2.ipynb, qui exporte data/site_stats.json. Ce script se contente de
mettre en forme ces résultats déjà calculés, plus la bibliographie de docs/SOURCES.md, en une page
HTML statique à onglets. Aucune dépendance externe : uniquement la bibliothèque standard.

Usage :
    python scripts/build_site.py
"""
import json
import re
import shutil
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
SITE = ROOT / "site"

CHART_FILES = [
    "comparaison_indice_base100.png",
    "graphes_indicateurs_complets.png",
]

# Date de publication du site (distincte de la période couverte par les données, {annees.min}-
# {annees.max}) : à mettre à jour manuellement à chaque republication.
PUB_DATE = "septembre 2026"

SIG_LABELS = {
    "hausse significative": "sig-up",
    "baisse significative": "sig-down",
    "non significatif": "sig-none",
}


# ---------------------------------------------------------------------------
# 1. Parsing de docs/SOURCES.md
# ---------------------------------------------------------------------------

def parse_sources(path):
    """Parse le format simple utilisé par SOURCES.md :

    ## Titre de section
    ### Titre de la source {#slug}
    - Référence : ...
    - Lien : ...
    - Constat : ... (peut continuer sur les lignes suivantes indentées de 2 espaces)

    Une section "## Limites méthodologiques..." est traitée à part : ses puces sont collectées
    telles quelles, sans devenir des entrées de bibliographie numérotées.
    """
    header_re = re.compile(r"^### (.+?)(?:\s*\{#([a-z0-9-]+)\})?\s*$")

    sections = []  # [(titre_section, [entrée, ...])]
    limites = []
    section_entries = None
    in_limites = False
    entry = None
    field = None

    def push_entry():
        if entry is not None and section_entries is not None:
            section_entries.append(entry)

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()

        if line.startswith("## "):
            push_entry()
            entry, field = None, None
            title = line[3:].strip()
            in_limites = "Limites méthodologiques" in title
            section_entries = None if in_limites else []
            if not in_limites:
                sections.append((title, section_entries))
            continue

        if line.startswith("### ") and not in_limites:
            push_entry()
            m = header_re.match(line)
            title = m.group(1).strip()
            slug = m.group(2) or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            entry = {"title": title, "slug": slug, "fields": {}}
            field = None
            continue

        if in_limites:
            if line.startswith("- "):
                limites.append(line[2:].strip())
            elif line.startswith("  ") and limites:
                limites[-1] += " " + line.strip()
            continue

        if entry is not None:
            if line.startswith("- "):
                label, sep, rest = line[2:].partition(" : ")
                if sep:
                    field = label.strip()
                    entry["fields"][field] = rest.strip()
                continue
            if line.startswith("  ") and field:
                entry["fields"][field] += " " + line.strip()
                continue

    push_entry()
    return sections, limites


def md_inline(text):
    """Échappe le HTML puis convertit le seul marqueur markdown utilisé dans SOURCES.md : *italique*."""
    text = escape(text)
    return re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)


# ---------------------------------------------------------------------------
# 2. Formatage des nombres et statistiques (data/site_stats.json)
# ---------------------------------------------------------------------------

def fr_num(x, decimals=2):
    return f"{x:.{decimals}f}".replace(".", ",")


def fmt_p(p):
    return "< 0,001" if p < 0.001 else fr_num(p, 3)


def sig_badge(label):
    cls = SIG_LABELS.get(label, "sig-none")
    return f'<span class="badge {cls}">{escape(label)}</span>'


def fmt_trend(st, unit="points/an"):
    return (
        f"pente {fr_num(st['pente'])} {unit} "
        f"(IC95&nbsp;% [{fr_num(st['IC95_bas'])}&nbsp;; {fr_num(st['IC95_haut'])}]), "
        f"p&nbsp;=&nbsp;{fmt_p(st['p_value'])}, R²&nbsp;=&nbsp;{fr_num(st['r2'])} — {sig_badge(st['significativite'])}"
    )


# ---------------------------------------------------------------------------
# 3. Construction des blocs HTML
# ---------------------------------------------------------------------------

def build_cancer_table(cancers):
    order = {"Forte": 0, "Moyenne": 1, "Non liée": 2}
    rows = sorted(cancers, key=lambda c: (order[c["presomption"]], c["p_value"]))
    trs = []
    for c in rows:
        trs.append(f"""        <tr>
          <td>{escape(c['nom'])}</td>
          <td>{escape(c['presomption'])}</td>
          <td>{sig_badge(c['significativite'])}</td>
          <td>{fmt_p(c['p_value'])}</td>
          <td>{fr_num(c['r2'])}</td>
          <td class="src-note">{escape(c['source_courte'])}</td>
        </tr>""")
    return "\n".join(trs)


def build_bibliography(sections):
    parts = []
    for title, entries in sections:
        parts.append(f'    <h3>{escape(title)}</h3>\n    <ol class="biblio-list">')
        for e in entries:
            f = e["fields"]
            ref = md_inline(f.get("Référence", ""))
            lien = f.get("Lien")
            constat = md_inline(f.get("Constat", ""))
            lien_html = ""
            if lien:
                if lien.startswith("http"):
                    lien_html = (
                        f'<p class="lien"><a href="{escape(lien)}" target="_blank" '
                        f'rel="noopener">{escape(lien)}</a></p>'
                    )
                else:
                    lien_html = f'<p class="lien">{md_inline(lien)}</p>'
            parts.append(f"""      <li id="src-{e['slug']}">
        <p class="ref">{ref}</p>
        {lien_html}
        <p class="constat">{constat}</p>
      </li>""")
        parts.append("    </ol>")
    return "\n".join(parts)


def build_limites(limites):
    items = "\n".join(f"      <li>{md_inline(l)}</li>" for l in limites)
    return f'    <ul class="limites-list">\n{items}\n    </ul>'


# ---------------------------------------------------------------------------
# 4. Contenu de chaque onglet
# ---------------------------------------------------------------------------

def panel_synthese(stats, cite):
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]
    g23 = stats["groupes_23_cancers"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies = len(lies)
    n_hausse = sum(1 for c in lies if c["significativite"] == "hausse significative")

    return f"""
  <section id="panel-synthese" class="tab-panel panel-synthese" role="tabpanel" aria-labelledby="tab-synthese" data-tab="synthese" tabindex="0">
    <h2 class="hero-headline">{n_hausse} cancers liés aux pesticides en hausse significative,
      contre un seul en baisse</h2>
    <p class="hero-lede">Sur les {n_lies} cancers pour lesquels l'Inserm documente une présomption
      de lien avec les pesticides, <strong>{n_hausse} présentent une hausse d'incidence
      statistiquement significative</strong> depuis {y0} sur le registre de Poitou-Charentes.
      Un seul est en baisse significative. Ce déséquilibre est inquiétant&nbsp;: il justifie d'en
      informer la population, pour agir par prévention plutôt que d'attendre une démonstration
      causale définitive.</p>

    <figure class="hero-figure">
      <img src="assets/img/comparaison_indice_base100.png" alt="Comparaison indexée base 100 des cancers à présomption forte/moyenne vs non liés, {y0}-{y1}" loading="lazy">
      <figcaption>Comparaison indexée (base 100 en {y0}) des deux groupes, classification Inserm 2021.</figcaption>
    </figure>

    <div class="method-block">
      <h3>Méthode : la classification Inserm 2021</h3>
      <p>L'expertise collective Inserm 2021 {cite('inserm-2021')} documente une présomption de
        lien "forte" ou "moyenne" avec les pesticides pour {n_lies} cancers du registre. En
        indexant chaque groupe à 100 en {y0} pour comparer des croissances plutôt que des
        niveaux&nbsp;:</p>
      <ul class="stat-list">
        <li><strong>Cancers à présomption forte ou moyenne ({n_lies})</strong> :
          {fmt_trend(g23['lies_pesticides'], unit="points d'indice/an")}</li>
        <li><strong>Cancers non documentés ({len(stats['cancers']) - n_lies})</strong> :
          {fmt_trend(g23['non_lies_pesticides'], unit="points d'indice/an")}</li>
      </ul>
    </div>
  </section>"""


def panel_detail(stats, cancer_table_html):
    return f"""
  <section id="panel-detail" class="tab-panel" role="tabpanel" aria-labelledby="tab-detail" data-tab="detail" hidden>
    <h2>Détail par cancer</h2>
    <p>Tendance individuelle des <strong>{len(stats['cancers'])} cancers</strong> suivis par le
      registre, triés par niveau de présomption Inserm 2021 puis par p-value. La colonne "Note
      bibliographique" reprend des repères issus de la littérature spécifique à chaque cancer ; ce
      sont des notes de travail abrégées, distinctes de la bibliographie numérotée de ce site, et
      <strong>non individuellement vérifiées</strong> à ce niveau de détail.</p>
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr><th>Cancer</th><th>Présomption</th><th>Tendance</th><th>p-value</th><th>R²</th><th>Note bibliographique</th></tr>
        </thead>
        <tbody>
{cancer_table_html}
        </tbody>
      </table>
    </div>

    <figure>
      <img src="assets/img/graphes_indicateurs_complets.png" alt="Grille de graphiques individuels par cancer, avec bande de confiance à 95%" loading="lazy">
      <figcaption>Régression et bande de confiance à 95&nbsp;% pour chacun des {len(stats['cancers'])} cancers suivis par le registre.</figcaption>
    </figure>
  </section>"""


def panel_methodologie(stats):
    n = stats["total"]["n"]
    alpha = fr_num(stats["alpha"], 2)
    return f"""
  <section id="panel-methodologie" class="tab-panel" role="tabpanel" aria-labelledby="tab-methodologie" data-tab="methodologie" hidden>
    <h2>Étude statistique : comment lire la p-value et le R²</h2>
    <p>Chaque tendance présentée sur ce site vient d'une <strong>régression linéaire simple</strong> :
      pour un cancer donné, on ajuste une droite entre l'année et le taux standardisé (Europe)
      correspondant.</p>
    <div class="formula">taux(année) ≈ a&nbsp;×&nbsp;année&nbsp;+&nbsp;b</div>
    <p><em>a</em> (la pente) et <em>b</em> (l'ordonnée à l'origine) sont ajustés par la
      <strong>méthode des moindres carrés</strong>, qui minimise la somme des carrés des écarts
      entre les points observés et la droite.</p>

    <h3>La p-value : la pente est-elle réelle, ou due au hasard ?</h3>
    <p>On teste l'<strong>hypothèse nulle H₀</strong>&nbsp;: la vraie pente est nulle, il n'y a pas
      de tendance réelle. La statistique de test est :</p>
    <div class="formula">t = a / erreur-type(a)</div>
    <p>Sous H₀, <em>t</em> suit une loi de Student à <em>n</em>−2 degrés de liberté (<em>n</em> =
      nombre d'années observées, {n} sur ce site). <strong>La p-value est la probabilité
      d'observer, par pur hasard d'échantillonnage, une valeur de <em>t</em> au moins aussi extrême
      que celle mesurée</strong> sur les données réelles. Seuil retenu ici&nbsp;:
      α&nbsp;=&nbsp;{alpha}. <strong>Si p&nbsp;&lt;&nbsp;0,05, la tendance est dite
      significative</strong>&nbsp;: elle a moins de 5&nbsp;% de chances d'être un simple artefact
      d'échantillonnage.</p>

    <h3>L'intervalle de confiance à 95&nbsp;%</h3>
    <div class="formula">IC95 = a ± t(0,025&nbsp;; n−2) × erreur-type(a)</div>
    <p>Cette fourchette donne les valeurs plausibles pour la vraie pente, avec 95&nbsp;% de
      confiance. <strong>Si elle exclut 0, la tendance est significative</strong> — c'est
      strictement équivalent au test de p-value au seuil de 5&nbsp;%, mais plus lisible&nbsp;: on
      voit directement l'ampleur possible de l'effet, pas seulement s'il existe.</p>

    <h3>Le R² : la droite explique-t-elle bien les données ?</h3>
    <div class="formula">R² = 1 − SS_res / SS_tot</div>
    <p>avec SS_res&nbsp;=&nbsp;Σ(taux observé − taux prédit)² (ce que la droite ne capture pas) et
      SS_tot&nbsp;=&nbsp;Σ(taux observé − taux moyen)² (la variation totale du taux d'une année sur
      l'autre). <strong>R² varie de 0 à 1</strong>&nbsp;: proche de 1, la quasi-totalité de la
      variation du taux au fil des années suit une évolution linéaire régulière&nbsp;; proche de 0,
      le taux varie surtout pour d'autres raisons (bruit d'une année sur l'autre, facteurs non
      modélisés).</p>

    <h3>Une limite qui ne se corrige pas statistiquement</h3>
    <p>Avec seulement {n} points de données par cancer (une valeur par an), ces tests ont une
      puissance statistique limitée&nbsp;: <strong>un résultat "non significatif" veut dire "non
      démontrable avec ces données", pas "absence de lien"</strong>. Le détail complet des limites
      de cette étude est dans l'onglet <strong>Limites méthodologiques</strong>.</p>
  </section>"""


def panel_endocriniens(cite):
    return f"""
  <section id="panel-endocriniens" class="tab-panel" role="tabpanel" aria-labelledby="tab-endocriniens" data-tab="endocriniens" hidden>
    <h2>Perturbateurs endocriniens</h2>

    <p>Un perturbateur endocrinien (PE) est <strong>une substance qui interfère avec le système
      hormonal</strong>. Toutes les substances de ce type ne sont pas des pesticides&nbsp;:
      certaines viennent des pesticides (organophosphates, pyréthrinoïdes, 2,4-D, métolachlore),
      d'autres des plastiques ou des cosmétiques (bisphénol A, phtalates, parabènes, triclosan) et
      n'ont rien à voir avec l'alimentation issue de l'agriculture. Cette distinction
      compte&nbsp;: toutes les études sur les PE ne parlent pas de pesticides, et toutes ne
      relèvent donc pas de l'onglet Alimentation.</p>

    <p>L'expertise collective Inserm 2021 {cite('inserm-2021', 'natura-sciences-inserm')} établit
      un lien entre <strong>l'exposition aux pesticides</strong> et six familles de troubles non
      cancéreux&nbsp;: <strong>maladie de Parkinson</strong> et autres <strong>troubles
      neurodégénératifs</strong>, <strong>troubles cognitifs</strong>, <strong>troubles
      digestifs</strong>, <strong>troubles respiratoires (dont BPCO)</strong>, <strong>troubles
      hormonaux</strong> et de la <strong>reproduction</strong>, et <strong>troubles du
      développement neuropsychologique et moteur chez l'enfant exposé in utero</strong>. Elle
      documente aussi des troubles du comportement de type <strong>anxiété chez l'enfant</strong>,
      ainsi que des <strong>leucémies aiguës</strong> et <strong>tumeurs cérébrales</strong> chez
      l'enfant en cas d'exposition parentale avant la naissance.</p>

    <p>Une étude américaine sur la cohorte NHANES {cite('quiros-alcala-2019')} constate un impact
      concret&nbsp;: chez les garçons asthmatiques, une exposition urinaire aux parabènes dix fois
      plus élevée est associée à un quasi-doublement du risque de recours aux urgences pour crise
      d'asthme — un effet non observé chez les filles. Les parabènes ne sont pas des pesticides
      (ils viennent des cosmétiques), mais cette association illustre que les PE peuvent perturber
      l'immunité. Une revue de l'Inrae {cite('inrae-review')} explique le mécanisme biologique
      sous-jacent&nbsp;: elle synthétise comment <strong>les perturbateurs endocriniens affectent
      le développement du système immunitaire pendant la période périnatale (grossesse et petite
      enfance), une fenêtre où ce système est particulièrement vulnérable</strong>.</p>

    <p>Sur le terrain, une étude du CHU de Bordeaux menée en zone viticole (281 enfants de
      Gironde) {cite('chu-bordeaux')} relève <strong>15,8&nbsp;% d'asthme et 35&nbsp;% de rhinite
      allergique</strong> chez les enfants suivis, avec un <strong>biomarqueur urinaire de
      fongicide (ETU) plus élevé en période estivale de traitement des vignes</strong> — un exemple
      français reliant directement exposition agricole et santé de l'enfant.</p>

    <p>Les pesticides peuvent atteindre <strong>le fœtus lui-même</strong>. Une étude suédoise
      {cite('bjorvang-2021')} a recherché 22 polluants organiques persistants dans les organes de
      20 fœtus issus de grossesses interrompues par une mort in utero au 3ᵉ trimestre&nbsp;: au
      moins 15 de ces substances, dont les pesticides organochlorés hexachlorobenzène (HCB) et
      p,p'-DDE (issu du DDT), <strong>ont été retrouvées dans chaque organe analysé, cerveau
      compris, parfois à des niveaux supérieurs à ceux mesurés dans le sang maternel ou le
      placenta</strong>.</p>

    <p>Le projet européen ENDOMIX (2024–2027, Inserm-Irset Rennes) {cite('endomix')} étudie
      spécifiquement l'effet de mélanges de perturbateurs endocriniens sur le système immunitaire,
      sur plusieurs cohortes de la grossesse à l'âge adulte&nbsp;; ses résultats définitifs ne sont
      pas encore publiés.</p>
  </section>"""


def panel_alimentation(cite):
    return f"""
  <section id="panel-alimentation" class="tab-panel" role="tabpanel" aria-labelledby="tab-alimentation" data-tab="alimentation" hidden>
    <h2>Alimentation, agriculture conventionnelle et bio : quel impact pour les enfants ?</h2>

    <p>Plusieurs travaux convergents, sur des populations et des méthodes différentes,
      identifient <strong>l'alimentation comme la voie d'exposition dominante aux
      pesticides</strong> pour la population générale&nbsp;: un modèle de simulation portant sur
      les pesticides actuellement utilisés {cite('li-2022')}, une analyse de 62&nbsp;151
      échantillons de résidus alimentaires {cite('fantke-jolliet-2009')}, une cohorte américaine de
      4&nbsp;466 personnes suivie par biomarqueurs urinaires {cite('mesa-study', 'epa-2006')}, et
      une comparaison entre une population résidant en zone agricole et un échantillon national
      représentatif {cite('chamacos-nhanes')}. Chez l'enfant spécifiquement, une cohorte chinoise
      de naissance de 643 enfants trouve le même résultat pour les néonicotinoïdes
      {cite('sheyang-cohort')}.</p>

    <p>Réduire l'exposition alimentaire aux pesticides a un effet mesurable, y compris chez
      l'enfant. Une étude d'intervention américaine {cite('bradman-2015')} montre qu'
      <strong>une semaine d'alimentation strictement biologique fait baisser les résidus urinaires
      de pesticides chez l'enfant</strong>, et que ces résidus remontent dès le retour à une
      alimentation conventionnelle. En France, la cohorte bretonne PELAGIE (245 enfants de 6 ans)
      {cite('pelagie-cohort')} trouve qu'une consommation fréquente de produits biologiques est
      associée à des taux urinaires plus bas de deux métabolites de pesticides.</p>

    <p>Le levier le plus direct pour l'exposition alimentaire des enfants en dehors du foyer est
      la restauration collective. La loi EGalim {cite('egalim')} impose, depuis 2022, 20&nbsp;% de
      bio et 50&nbsp;% de produits durables dans la restauration collective publique, étendue à
      l'ensemble de la restauration collective depuis 2024. En pratique, <strong>la part de bio
      observée en 2023 était de 12,1&nbsp;% en moyenne — sous l'objectif légal</strong>, ce qui
      laisse une marge de progression directement actionnable localement (cantines scolaires
      notamment).</p>
  </section>"""


def panel_sources(biblio_html):
    return f"""
  <section id="panel-sources" class="tab-panel" role="tabpanel" aria-labelledby="tab-sources" data-tab="sources" hidden>
    <h2>Sources</h2>
    <p>Bibliographie complète, par thème. Document source : <code>docs/SOURCES.md</code> dans le
      dépôt du projet.</p>
{biblio_html}
  </section>"""


def panel_limites(limites_html):
    return f"""
  <section id="panel-limites" class="tab-panel" role="tabpanel" aria-labelledby="tab-limites" data-tab="limites" hidden>
    <h2>Limites méthodologiques</h2>
{limites_html}
  </section>"""


TABS = [
    ("synthese", "Synthèse"),
    ("detail", "Détail par cancer"),
    ("methodologie", "Étude statistique"),
    ("endocriniens", "Perturbateurs endocriniens"),
    ("alimentation", "Alimentation"),
    ("sources", "Sources"),
    ("limites", "Limites méthodologiques"),
]


# ---------------------------------------------------------------------------
# 5. Page principale
# ---------------------------------------------------------------------------

def build_page(stats, cite, cancer_table_html, biblio_html, limites_html):
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]

    tabs_nav = "\n".join(
        f'      <button role="tab" id="tab-{key}" aria-selected="{"true" if i == 0 else "false"}" '
        f'aria-controls="panel-{key}" tabindex="{0 if i == 0 else -1}" data-tab="{key}">{label}</button>'
        for i, (key, label) in enumerate(TABS)
    )

    panels = "\n".join([
        panel_synthese(stats, cite),
        panel_detail(stats, cancer_table_html),
        panel_methodologie(stats),
        panel_endocriniens(cite),
        panel_alimentation(cite),
        panel_sources(biblio_html),
        panel_limites(limites_html),
    ])

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cancers, pesticides et alimentation</title>
<meta name="description" content="Étude statistique de l'évolution des cancers en Poitou-Charentes ({y0}–{y1}) au regard de l'exposition aux pesticides, des perturbateurs endocriniens et de l'alimentation conventionnelle vs biologique.">
<link rel="stylesheet" href="style.css">
</head>
<body>

<header class="site-header">
  <div class="wrap header-inner">
    <div class="header-text">
      <h1>Cancers, pesticides et alimentation</h1>
      <p class="subtitle">Étude réalisée à partir du registre des cancers de Poitou-Charentes
        ({y0}–{y1}) et de la littérature scientifique — Santé des Territoires Aigondigné</p>
    </div>
    <p class="pub-date">Publié en {PUB_DATE}</p>
  </div>
</header>

<nav class="tabs" role="tablist" aria-label="Sections du site">
  <div class="wrap tabs-inner">
{tabs_nav}
  </div>
</nav>

<main class="wrap">
{panels}
</main>

<footer class="site-footer">
  <div class="wrap">
    <p>Données : registre des cancers de Poitou-Charentes, {y0}–{y1}. Analyse statistique :
      régression linéaire, IC95&nbsp;%, seuil α&nbsp;=&nbsp;{fr_num(stats['alpha'], 2)}.
      Page générée le {stats['generated_at']} par
      <code>scripts/build_site.py</code> à partir de <code>data/site_stats.json</code> — Santé des
      Territoires Aigondigné.</p>
  </div>
</footer>

<script src="tabs.js"></script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 6. Orchestration
# ---------------------------------------------------------------------------

def main():
    stats = json.loads((DATA / "site_stats.json").read_text(encoding="utf-8"))
    sections, limites = parse_sources(DOCS / "SOURCES.md")

    all_entries = [e for _, entries in sections for e in entries]
    for i, e in enumerate(all_entries, start=1):
        e["n"] = i
    slug_to_entry = {e["slug"]: e for e in all_entries}

    def cite(*slugs):
        marks = [f'<a href="#src-{slug_to_entry[s]["slug"]}">{slug_to_entry[s]["n"]}</a>' for s in slugs]
        return f'<sup class="cite">[{", ".join(marks)}]</sup>'

    cancer_table_html = build_cancer_table(stats["cancers"])
    biblio_html = build_bibliography(sections)
    limites_html = build_limites(limites)

    SITE.mkdir(exist_ok=True)
    (SITE / "assets" / "img").mkdir(parents=True, exist_ok=True)
    for name in CHART_FILES:
        shutil.copy(DATA / name, SITE / "assets" / "img" / name)

    html = build_page(stats, cite, cancer_table_html, biblio_html, limites_html)
    (SITE / "index.html").write_text(html, encoding="utf-8")

    scripts_dir = Path(__file__).parent
    shutil.copy(scripts_dir / "site_style.css", SITE / "style.css")
    shutil.copy(scripts_dir / "site_tabs.js", SITE / "tabs.js")

    print(f"Site généré dans {SITE} ({len(all_entries)} sources, {len(stats['cancers'])} cancers, "
          f"{sum(1 for c in stats['cancers'] if c['presomption'] != 'Non liée')} liés pesticides).")


if __name__ == "__main__":
    main()
