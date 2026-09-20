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
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
SITE = ROOT / "site"

CHART_FILES = [
    "comparaison_indice_base100.png",
    "comparaison_indice_base100_taux_brut.png",
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

# Relie chaque valeur exacte de source_courte (data/site_stats.json) à une ou plusieurs sources
# numérotées de docs/SOURCES.md. Une valeur absente de ce dictionnaire, ou associée à une liste
# vide, reste un texte libre sans lien dans le tableau des 33 cancers (onglet Détail) — c'est le
# cas des repères de travail non individuellement vérifiés ("Non documenté", "Données
# insuffisantes...").
TABLE_SOURCE_SLUGS = {
    "Inserm 2021, présomption forte": ["inserm-2021"],
    "Inserm 2021, LNH forte": ["inserm-2021"],
    "Inserm 2021, fort chez l'enfant": ["inserm-2021"],
    "Inserm 2021, présomption moyenne": ["inserm-2021"],
    "Inserm/INRAE/Cnam, cocktails alimentaires": ["rebouillat-2021-sein"],
    "Agricultural Health Study ; ARC": ["ahs-lung-cancer-2017", "arc-pesticides-cancers"],
    "Inserm 2021 ; thèse 2021 (OR=1,51 adulte)": ["inserm-2021", "foucault-2021-lam"],
    "ScienceDirect 2023, preuves limitées": ["smd-facteurs-environnementaux-2023"],
    "Inserm 2021 (AGRICAN) ; piclorame 2026": ["inserm-2021", "agrican-colorectal-2022", "picloram-2026"],
    "Ligue contre le cancer ; JCO 2025": ["ligue-cancer-2013-pesticides", "jco-2025-melanome"],
    "Ligue contre le cancer (Inserm 2021)": ["ligue-cancer-2013-pesticides", "inserm-2021"],
    "Fondation ARC ; Cancer-Environnement": ["arc-pesticides-cancers", "cancer-environnement-pesticides"],
    "Études PESTIPAC/EcoPESTIPAC": ["pestipac-2024", "ecopestipac-2024"],
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
                    value = rest.strip()
                    if field == "Lien":
                        # Une source peut citer plusieurs liens (ex. deux articles de synthèse
                        # d'une même expertise) : chaque "- Lien : ..." s'ajoute à une liste,
                        # plutôt que d'écraser le précédent.
                        entry["fields"].setdefault("Lien", []).append(value)
                    else:
                        entry["fields"][field] = value
                continue
            if line.startswith("  ") and field:
                current = entry["fields"].get(field)
                if isinstance(current, list):
                    current[-1] += " " + line.strip()
                else:
                    entry["fields"][field] = (current or "") + " " + line.strip()
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

def build_cancer_table(cancers, cite):
    order = {"Forte": 0, "Moyenne": 1, "Non liée": 2}
    rows = sorted(cancers, key=lambda c: (order[c["presomption"]], c["p_value"]))
    trs = []
    for c in rows:
        slugs = TABLE_SOURCE_SLUGS.get(c["source_courte"]) or []
        src_note = escape(c["source_courte"])
        if slugs:
            src_note += cite(*slugs)
        trs.append(f"""        <tr>
          <td>{escape(c['nom'])}</td>
          <td>{escape(c['presomption'])}</td>
          <td>{sig_badge(c['significativite'])}</td>
          <td>{fmt_p(c['p_value'])}</td>
          <td>{fr_num(c['r2'])}</td>
          <td class="src-note">{src_note}</td>
        </tr>""")
    return "\n".join(trs)


def build_bibliography(sections, citation_locations):
    parts = []
    for title, entries in sections:
        parts.append(f'    <h3>{escape(title)}</h3>\n    <ul class="biblio-list">')
        for e in entries:
            f = e["fields"]
            ref = md_inline(f.get("Référence", ""))
            liens = f.get("Lien", [])
            constat = md_inline(f.get("Constat", ""))
            lien_htmls = []
            for lien in liens:
                if lien.startswith("http"):
                    lien_htmls.append(
                        f'<p class="lien"><a href="{escape(lien)}" target="_blank" '
                        f'rel="noopener">{escape(lien)}</a></p>'
                    )
                else:
                    lien_htmls.append(f'<p class="lien">{md_inline(lien)}</p>')
            lien_html = "\n        ".join(lien_htmls)

            # Lien(s) de retour vers le ou les endroits du texte où cette source est citée. Une
            # source citée plusieurs fois obtient un lien numéroté par occurrence ; une source
            # jamais citée (à vérifier au cas par cas) n'affiche rien.
            anchors = citation_locations.get(e["slug"], [])
            if not anchors:
                back_html = ""
            elif len(anchors) == 1:
                back_html = (
                    f'<p class="backlink"><a href="#{anchors[0]}">↩ Voir dans le texte</a></p>'
                )
            else:
                links = " ".join(
                    f'<a href="#{a}" title="Occurrence {i} dans le texte">↩{i}</a>'
                    for i, a in enumerate(anchors, start=1)
                )
                back_html = f'<p class="backlink">Voir dans le texte : {links}</p>'

            # Le numéro affiché ici doit être identique à celui produit par cite() : les deux
            # viennent de e["n"], assigné une seule fois dans main(). Ne pas le recalculer via un
            # compteur CSS (ça redémarre à 1 à chaque <ul>, un thème = une liste = un décalage).
            parts.append(f"""      <li id="src-{e['slug']}">
        <span class="num">{e['n']}.</span>
        <p class="ref">{ref}</p>
        {lien_html}
        <p class="constat">{constat}</p>
        {back_html}
      </li>""")
        parts.append("    </ul>")
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
    g23_brut = stats["groupes_23_cancers_taux_brut"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies = len(lies)
    n_non_lies = len(stats["cancers"]) - n_lies

    return f"""
  <section id="panel-synthese" class="tab-panel panel-synthese" role="tabpanel" aria-labelledby="tab-synthese" data-tab="synthese" tabindex="0">
    <p class="site-intro">Cette étude présente ses résultats à partir des données du registre des
      cancers de Poitou-Charentes {cite('registre-cancers-pc')}. Elle explique les liens entre
      pesticides, maladies — dont les cancers — et alimentation. Les résultats de cette étude
      montrent l'importance d'une alimentation biologique, seul label garantissant l'absence de
      pesticides de synthèse (voir l'onglet Labels).</p>

    <h2 class="hero-headline">Les cancers liés aux pesticides progressent, les autres reculent</h2>
    <p class="hero-lede">L'incidence des cancers liés aux pesticides croît de façon certaine sur la
      période étudiée. Dans le même temps, l'incidence des cancers non liés aux pesticides
      décroît. Ce constat est inquiétant&nbsp;: il justifie d'en informer la population, afin
      d'agir par prévention plutôt que d'attendre une hypothétique démonstration causale
      définitive.</p>

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
        <li><strong>Cancers non documentés ({n_non_lies})</strong> :
          {fmt_trend(g23['non_lies_pesticides'], unit="points d'indice/an")}</li>
      </ul>

      <h3>Qu'est-ce qu'un taux standardisé ?</h3>
      <p>Le taux <em>standardisé (Europe)</em> utilisé ci-dessus neutralise l'effet du
        vieillissement de la population&nbsp;: il recalcule l'incidence que l'on observerait si la
        pyramide des âges de Poitou-Charentes était identique à celle de la population européenne
        de référence. Sans cette correction, une hausse de taux peut simplement refléter une
        population plus âgée — pas un risque individuel plus élevé.</p>
      <p>Cette correction change-t-elle la conclusion&nbsp;? On refait le même calcul avec le taux
        brut, non corrigé de l'âge&nbsp;:</p>
      <ul class="stat-list">
        <li><strong>Cancers à présomption forte ou moyenne ({n_lies}), taux brut</strong> :
          {fmt_trend(g23_brut['lies_pesticides'], unit="points d'indice/an")}</li>
        <li><strong>Cancers non documentés ({n_non_lies}), taux brut</strong> :
          {fmt_trend(g23_brut['non_lies_pesticides'], unit="points d'indice/an")}</li>
      </ul>
      <p>Sur le taux brut, la hausse des cancers liés aux pesticides est encore plus marquée. En
        revanche, la baisse des cancers non liés disparaît&nbsp;: elle n'est plus significative.
        C'est cohérent avec l'effet du vieillissement de la population&nbsp;: sans la correction
        par âge, une population plus âgée masque une baisse de risque réelle par une hausse
        mécanique des cas. La standardisation ne fabrique donc pas la baisse observée chez les
        cancers non liés — elle la révèle.</p>
    </div>

    <figure class="hero-figure">
      <img src="assets/img/comparaison_indice_base100_taux_brut.png" alt="Même comparaison indexée base 100, taux brut non standardisé, {y0}-{y1}" loading="lazy">
      <figcaption>Même comparaison, taux brut (non standardisé par âge).</figcaption>
    </figure>
  </section>"""


def panel_detail(stats, cancer_table_html):
    y0 = stats["annees"]["min"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies = len(lies)
    n_hausse = sum(1 for c in lies if c["significativite"] == "hausse significative")

    return f"""
  <section id="panel-detail" class="tab-panel" role="tabpanel" aria-labelledby="tab-detail" data-tab="detail" hidden>
    <h2>Détail par cancer</h2>

    <h3 class="hero-headline">{n_hausse} cancers liés aux pesticides en hausse significative,
      contre un seul en baisse</h3>
    <p class="hero-lede">Sur les {n_lies} cancers pour lesquels l'Inserm documente une présomption
      de lien avec les pesticides, <strong>{n_hausse} présentent une hausse d'incidence
      statistiquement significative</strong> depuis {y0} sur le registre de Poitou-Charentes.
      Un seul est en baisse significative.</p>

    <p>Tendance individuelle des <strong>{len(stats['cancers'])} cancers</strong> suivis par le
      registre, triés par niveau de présomption Inserm 2021 puis par p-value. La colonne "Note
      bibliographique" reprend des repères issus de la littérature spécifique à chaque cancer. La
      plupart renvoient vers une source vérifiée de l'onglet Sources. Les mentions "Non documenté"
      et "Données insuffisantes" restent des <strong>repères de travail non individuellement
      vérifiés</strong>.</p>
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
    <h2>Étude statistique : comment lire les résultats</h2>
    <p>Chaque tendance présentée dans cette étude vient d'une <strong>régression linéaire simple</strong>.
      Elle relie l'année et le taux standardisé du cancer concerné. Trois indicateurs permettent d'évaluer la qualité du modèle
     &nbsp;: la p-value, l'intervalle de confiance à 95&nbsp;%, et le R².</p>

    <h3>Pourquoi une droite, et pas une courbe plus complexe ?</h3>
    <p>Cette étude compte seulement {n} points de données par cancer, une valeur par an. Un modèle
      plus complexe qu'une droite, comme un polynôme, une
      exponentielle ou un ajustement local, demanderait d'estimer plus de paramètres. Avec aussi peu de points, il y a un risque de surentraînement du modèle,
      c'est à dire que le modèle colle au bruit d'échantillonnage plutôt qu'à la tendance réelle. Une
      droite n'utilise que deux paramètres, la pente et l'ordonnée à l'origine. La pente est directement interprétable en évolution du taux d'incidence par an. Par ailleurs,il s'agit de la méthode
      standard pour détecter une tendance monotone sur une série courte. Un modèle plus complexe se
      justifierait avec plus d'années de données, ou avec une raison claire d'attendre une rupture
      nette, comme un changement à grande échelle des dépitages, par exemple.</p>

    <h3>La p-value : la tendance est-elle réelle, ou due au hasard ?</h3>
    <p>La p-value mesure le risque de conclure à tort qu'une tendance existe. Le seuil retenu ici
      est de {alpha}. <strong>Si p&nbsp;&lt;&nbsp;0,05, la tendance est dite significative</strong>.
      Elle a moins de 5&nbsp;% de chances d'être due au hasard. Si p&nbsp;≥&nbsp;0,05, les données ne
      permettent pas d'affirmer qu'une évolution réelle existe.</p>

    <h3>L'intervalle de confiance à 95&nbsp;%</h3>
    <p>Cet intervalle donne une fourchette de valeurs plausibles pour la pente réelle.
      <strong>S'il exclut zéro, la tendance est significative</strong>. C'est équivalent à la
      p-value, mais plus parlant. Il montre en plus l'ampleur possible de l'évolution, pas
      seulement son existence.</p>

    <h3>Le R² : la droite explique-t-elle bien les données ?</h3>
    <p>Le R² dit si le modèle capte bien la variance des mesures. <strong>Il varie de 0 à
      1</strong>. Proche de 1, le taux suit une évolution régulière d'une année sur l'autre. Proche
      de 0, le taux varie surtout pour d'autres raisons. Ces raisons peuvent être des écarts d'une
      année sur l'autre ou des facteurs non pris en compte.</p>

    <h3>Une limite qui ne se corrige pas statistiquement</h3>
    <p>Cette étude compte {n} points de données par cancer, une valeur par an. Les tests
      statistiques ont donc une puissance limitée. <strong>Un résultat "non significatif" veut
      dire "non démontrable avec ces données"</strong>. Cela ne veut pas dire "absence de lien". Le
      détail complet des limites de cette étude est dans l'onglet <strong>Limites
      méthodologiques</strong>.</p>
  </section>"""


def panel_endocriniens(cite):
    return f"""
  <section id="panel-endocriniens" class="tab-panel" role="tabpanel" aria-labelledby="tab-endocriniens" data-tab="endocriniens" hidden>
    <h2>Perturbateurs endocriniens</h2>

    <p>Un perturbateur endocrinien (PE) est <strong>une substance qui interfère avec le système
      hormonal</strong>. Toutes les substances de ce type présentes dans notre environnement ne
      proviennent pas des pesticides&nbsp;: certaines viennent des pesticides (organophosphates,
      pyréthrinoïdes, 2,4-D, métolachlore), d'autres des plastiques ou des cosmétiques (bisphénol
      A, phtalates, parabènes, triclosan) et n'ont rien à voir avec l'alimentation. Cette
      distinction compte&nbsp;: toutes les études sur les PE ne parlent pas de pesticides, et
      toutes ne relèvent donc pas de l'onglet Alimentation.</p>

    <p>L'expertise collective Inserm 2021 {cite('inserm-2021', 'natura-sciences-inserm')} établit
      un lien entre <strong>l'exposition aux pesticides</strong> et des <strong>leucémies
      aiguës</strong> et <strong>tumeurs cérébrales</strong> chez l'enfant en cas d'exposition
      parentale avant la naissance. L'expertise Inserm 2021 établit aussi un lien entre
      l'exposition aux pesticides et six familles de troubles non cancéreux&nbsp;: <strong>maladie
      de Parkinson</strong> et autres <strong>troubles neurodégénératifs</strong>, <strong>troubles
      cognitifs</strong>, <strong>troubles digestifs</strong>, <strong>troubles respiratoires (dont
      BPCO)</strong>, <strong>troubles hormonaux</strong> et de la <strong>reproduction</strong>,
      et <strong>troubles du développement neuropsychologique et moteur chez l'enfant exposé in
      utero</strong>. Elle documente aussi des troubles du comportement de type <strong>anxiété
      chez l'enfant</strong>. La présente étude se concentre sur les cancers en
      Poitou-Charentes. Ainsi, les effets des expositions aux pesticides sont plus vastes que ce
      que cette étude suggère.</p>

    <p>Une étude américaine sur la cohorte NHANES {cite('quiros-alcala-2019')} constate un impact
      concret&nbsp;: chez les garçons asthmatiques, une concentration de parabènes dans les urines
      dix fois plus élevée est associée à un quasi-doublement du risque de recours aux urgences
      pour crise d'asthme — un effet non observé chez les filles. Les parabènes sont présents dans
      les cosmétiques, pas dans les pesticides. Cependant cette association illustre que les PE
      peuvent perturber le système immunitaire. Une revue de l'Inrae {cite('inrae-review')}
      explique le mécanisme biologique sous-jacent&nbsp;: elle synthétise comment <strong>les
      perturbateurs endocriniens affectent le développement du système immunitaire pendant la
      grossesse et la petite enfance, une période où ce système est particulièrement
      vulnérable</strong>.</p>

    <p>Sur le terrain, une étude du CHU de Bordeaux {cite('chu-bordeaux')} a été menée sur 281
      enfants de Gironde en zone viticole. Elle relève <strong>15,8&nbsp;% d'asthme et 35&nbsp;%
      de rhinite allergique</strong> chez les enfants suivis, avec un <strong>biomarqueur
      urinaire de fongicide (ETU) plus élevé en période estivale de traitement des
      vignes</strong> — un exemple reliant directement exposition agricole et santé de
      l'enfant.</p>

    <p>Les pesticides peuvent atteindre <strong>le fœtus lui-même</strong>. Une étude suédoise
      {cite('bjorvang-2021')} a recherché 22 polluants organiques persistants dans les organes de
      20 fœtus issus de grossesses interrompues par une mort in utero au 3ᵉ trimestre&nbsp;: au
      moins 15 de ces substances, dont les pesticides organochlorés hexachlorobenzène (HCB) et
      p,p'-DDE (issu du DDT), <strong>ont été retrouvées dans chaque organe analysé, cerveau
      compris, parfois à des niveaux supérieurs à ceux mesurés dans le sang maternel ou le
      placenta</strong>.</p>

    <p>Le projet européen ENDOMIX (2024–2027, Inserm-Irset Rennes) {cite('endomix')} étudie
      spécifiquement l'effet de mélanges de perturbateurs endocriniens sur le système immunitaire,
      sur plusieurs cohortes de la grossesse à l'âge adulte. Nous suivrons ses résultats
      définitifs, qui ne sont pas encore publiés.</p>
  </section>"""


def panel_alimentation(cite):
    return f"""
  <section id="panel-alimentation" class="tab-panel" role="tabpanel" aria-labelledby="tab-alimentation" data-tab="alimentation" hidden>
    <h2>Alimentation, agriculture conventionnelle et bio : impacts, notamment pour les enfants</h2>

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
      laisse une marge de progression directement actionnable localement, notamment via les
      cantines scolaires.</p>
  </section>"""


def panel_labels(cite):
    return f"""
  <section id="panel-labels" class="tab-panel" role="tabpanel" aria-labelledby="tab-labels" data-tab="labels" hidden>
    <h2>Labels alimentaires : pourquoi le bio est le seul qui compte face aux pesticides</h2>

    <p>Plusieurs labels affichés en rayon parlent d'environnement ou de qualité, mais tous ne
      disent pas la même chose sur les pesticides. Voici les trois que l'on croise le plus souvent,
      et ce qu'ils garantissent réellement.</p>

    <h3>Bio (AB / Eurofeuille) : une interdiction de méthode</h3>
    <p>Le label bio européen {cite('ademe-label-bio')} repose sur un règlement qui
      <strong>interdit les pesticides et engrais de synthèse ainsi que les OGM</strong>, vérifié
      par des organismes certificateurs indépendants qui contrôlent l'exploitation elle-même — pas
      seulement le produit fini. Nuance à ne pas perdre de vue&nbsp;: le bio autorise encore une
      trentaine de substances naturelles ou de biocontrôle, comme le cuivre, le soufre, les
      extraits végétaux et les micro-organismes {cite('agence-bio-naturels')}. <strong>Le bio garantit l'absence de
      pesticides de synthèse, pas une absence totale et absolue de tout traitement</strong> — mais
      c'est aujourd'hui le seul label à interdire la méthode elle-même plutôt qu'à mesurer un
      résultat.</p>

    <h3>HVE (Haute Valeur Environnementale) : un score, pas une interdiction</h3>
    <p>Le label d'État HVE {cite('que-choisir-hve')} <strong>n'interdit pas les pesticides de
      synthèse</strong>. Il note l'exploitation sur quatre thématiques (biodiversité, stratégie
      phytosanitaire, fertilisation, irrigation) : un score faible sur les pesticides peut être
      compensé par de bons scores ailleurs. France Nature Environnement, Agir pour
      l'environnement, la Confédération paysanne et Synabio alertent sur une confusion fréquente
      avec le bio dans l'esprit des consommateurs, alors que les deux labels ne garantissent pas
      la même chose.</p>

    <h3>Zéro Résidu de Pesticides : un résultat, pas une méthode</h3>
    <p>Ce label privé {cite('generations-futures-zrp')}, non officiel et sans contrôle indépendant
      obligatoire, fonctionne à l'inverse du bio&nbsp;: <strong>les pesticides de synthèse restent
      autorisés pendant la culture</strong>, à condition qu'il n'en reste pas de trace détectable
      (seuil de 0,01 mg/kg) au moment de la récolte. Concrètement, les sols, l'eau, l'air et la
      biodiversité restent exposés pendant la production&nbsp;; seul le produit acheté est
      concerné.</p>

    <h3>Ce que ça change concrètement</h3>
    <p>Sur les trois, <strong>seul le bio interdit l'usage de pesticides de synthèse par
      construction</strong>, plutôt que de mesurer un résultat après coup ou de compenser un usage
      par d'autres critères. C'est la raison pour laquelle nous le mentionnons comme le repère le
      plus fiable pour un consommateur qui veut réduire son exposition alimentaire aux pesticides
      — cf. l'onglet Alimentation pour l'effet mesuré de ce choix, y compris chez l'enfant.</p>
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
    ("labels", "Labels"),
    ("sources", "Sources"),
    ("limites", "Limites méthodologiques"),
]


# ---------------------------------------------------------------------------
# 5. Page principale
# ---------------------------------------------------------------------------

def build_page(stats, panels):
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]

    tabs_nav = "\n".join(
        f'      <button role="tab" id="tab-{key}" aria-selected="{"true" if i == 0 else "false"}" '
        f'aria-controls="panel-{key}" tabindex="{0 if i == 0 else -1}" data-tab="{key}">{label}</button>'
        for i, (key, label) in enumerate(TABS)
    )

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

    # citation_locations relie chaque source aux ancres de ses citations dans le texte, pour que
    # la bibliographie puisse offrir un lien de retour. Rempli par cite() au fil de la
    # construction des onglets ci-dessous — c'est pourquoi la bibliographie doit être construite
    # après, pas avant.
    citation_locations = defaultdict(list)
    citation_counter = defaultdict(int)

    def cite(*slugs):
        marks = []
        for s in slugs:
            citation_counter[s] += 1
            anchor_id = f"cite-{s}-{citation_counter[s]}"
            citation_locations[s].append(anchor_id)
            marks.append(f'<a href="#src-{s}" id="{anchor_id}">{slug_to_entry[s]["n"]}</a>')
        return f'<sup class="cite">[{", ".join(marks)}]</sup>'

    cancer_table_html = build_cancer_table(stats["cancers"], cite)
    limites_html = build_limites(limites)

    panel_htmls = [
        panel_synthese(stats, cite),
        panel_detail(stats, cancer_table_html),
        panel_methodologie(stats),
        panel_endocriniens(cite),
        panel_alimentation(cite),
        panel_labels(cite),
    ]
    biblio_html = build_bibliography(sections, citation_locations)
    panel_htmls.append(panel_sources(biblio_html))
    panel_htmls.append(panel_limites(limites_html))
    panels = "\n".join(panel_htmls)

    SITE.mkdir(exist_ok=True)
    (SITE / "assets" / "img").mkdir(parents=True, exist_ok=True)
    for name in CHART_FILES:
        shutil.copy(DATA / name, SITE / "assets" / "img" / name)

    html = build_page(stats, panels)
    (SITE / "index.html").write_text(html, encoding="utf-8")

    scripts_dir = Path(__file__).parent
    shutil.copy(scripts_dir / "site_style.css", SITE / "style.css")
    shutil.copy(scripts_dir / "site_tabs.js", SITE / "tabs.js")

    print(f"Site généré dans {SITE} ({len(all_entries)} sources, {len(stats['cancers'])} cancers, "
          f"{sum(1 for c in stats['cancers'] if c['presomption'] != 'Non liée')} liés pesticides).")


if __name__ == "__main__":
    main()
