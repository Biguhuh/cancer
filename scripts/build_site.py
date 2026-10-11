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
PUB_DATE = "octobre 2026"

# Date de la dernière mise en ligne des données par le registre (page "Les chiffres" de son site),
# citée dans l'onglet de comparaison v1/v2 : à mettre à jour avec les données.
REGISTRE_MAJ = "18 août 2026"

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
    "Inserm 2021 ; thèse 2021, OR = 1,51 chez l'adulte": ["inserm-2021", "foucault-2021-lam"],
    "ScienceDirect 2023, preuves limitées": ["smd-facteurs-environnementaux-2023"],
    "Inserm 2021, AGRICAN ; piclorame 2026": ["inserm-2021", "agrican-colorectal-2022", "picloram-2026"],
    "Ligue contre le cancer ; JCO 2025": ["ligue-cancer-2013-pesticides", "jco-2025-melanome"],
    "Ligue contre le cancer, d'après Inserm 2021": ["ligue-cancer-2013-pesticides", "inserm-2021"],
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


def typo_fr(html):
    """Espaces insécables à la française dans le texte affiché, sans toucher aux balises ni aux
    attributs : avant : ; ? ! et %, et à l'intérieur des guillemets « ». Filet de sécurité
    appliqué à toute la page, pour que le texte de SOURCES.md et des onglets reste homogène."""
    parts = re.split(r"(<[^>]+>)", html)
    skip = False
    for i, part in enumerate(parts):
        if part.startswith("<"):
            tag = part.lower()
            if tag.startswith(("<script", "<style", "<code")):
                skip = True
            elif tag.startswith(("</script", "</style", "</code")):
                skip = False
            continue
        if skip:
            continue
        part = re.sub(r"[  ]+([:;?!%])", r"&nbsp;\1", part)
        part = re.sub(r"«[  ]+", "«&nbsp;", part)
        part = re.sub(r"[  ]+»", "&nbsp;»", part)
        parts[i] = part
    return "".join(parts)


def fmt_p(p):
    return "&lt;&nbsp;0,001" if p < 0.001 else fr_num(p, 3)


def sig_badge(label):
    cls = SIG_LABELS.get(label, "sig-none")
    return f'<span class="badge {cls}">{escape(label)}</span>'


def fmt_trend(st, unit="points/an"):
    return (
        f"pente {fr_num(st['pente'])} {unit}, "
        f"IC95&nbsp;% [{fr_num(st['IC95_bas'])}&nbsp;; {fr_num(st['IC95_haut'])}], "
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

# Une ligne de présentation par onglet, affichée sur l'Accueil ; le libellé du lien vient de TABS.
TAB_DESCRIPTIONS = {
    "synthese": "les deux groupes de cancers comparés depuis {y0}, en graphe, et la méthode de classement.",
    "detail": "la liste des {n} cancers suivis, avec la tendance de chacun et son niveau de lien "
              "présumé avec les pesticides.",
    "methodologie": "comment lire les résultats, avec la p-value, l'intervalle de confiance et le R².",
    "versions": "ce qui change avec les données du registre consolidées en {registre_mois}.",
    "endocriniens": "ce que la littérature dit de ces substances, dont certains pesticides.",
    "alimentation": "l'alimentation comme voie d'exposition dominante, et l'effet d'une "
                    "alimentation biologique, notamment chez l'enfant.",
    "riverains": "l'exposition des personnes vivant près des cultures, et les études sur les "
                 "cancers de l'enfant.",
    "labels": "ce que garantissent, ou non, les labels alimentaires face aux pesticides.",
    "sources": "toutes les références citées, avec leurs liens.",
    "limites": "ce que l'étude ne permet pas de conclure.",
}


def panel_accueil(stats, cite):
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]
    n = len(stats["cancers"])
    n_lies = sum(1 for c in stats["cancers"] if c["presomption"] != "Non liée")
    alpha = fr_num(stats["alpha"], 2)
    registre_mois = REGISTRE_MAJ.split(" ", 1)[1]
    onglets = "\n".join(
        f'        <li><a href="#{key}"><strong>{label}</strong></a>&nbsp;: '
        f'{TAB_DESCRIPTIONS[key].format(y0=y0, n=n, registre_mois=registre_mois)}</li>'
        for key, label in TABS if key != "accueil"
    )

    return f"""
  <section id="panel-accueil" class="tab-panel panel-synthese" role="tabpanel" aria-labelledby="tab-accueil" data-tab="accueil" tabindex="0">
    <p class="site-intro">Cette étude présente ses résultats à partir des données du registre des
      cancers de Poitou-Charentes {cite('registre-cancers-pc')}. Elle explique les liens entre
      pesticides, maladies, dont les cancers, et voies d'exposition. L'alimentation est la voie
      dominante pour la population générale {cite('li-2022', 'fantke-jolliet-2009', 'chamacos-nhanes')}.
      L'air compte aussi, notamment près des parcelles traitées {cite('pestiriv-2025')}, et des
      résidus de pesticides sont retrouvés dans l'eau du robinet {cite('anses-eau-2023')}. Les
      onglets Alimentation, Riverains et Labels détaillent ces points.</p>

    <div class="method-block">
      <h3>Sources et méthode</h3>
      <p>Les données viennent du registre des cancers de Poitou-Charentes, qui suit {n} cancers de
        {y0} à {y1}. Pour chaque cancer, l'étude utilise le taux d'incidence standardisé sur l'âge
        et mesure son évolution par une régression linéaire simple. Une tendance est dite
        significative si p&nbsp;&lt;&nbsp;{alpha}. Les cancers sont répartis en deux groupes selon
        l'expertise collective Inserm 2021&nbsp;: {n_lies} cancers à présomption de lien forte ou
        moyenne avec les pesticides, et {n - n_lies} cancers sans lien documenté.</p>
    </div>

    <h3>Les onglets</h3>
    <ul>
{onglets}
    </ul>
  </section>"""


def panel_synthese(stats, cite):
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]
    g23 = stats["groupes_23_cancers"]
    g23_brut = stats["groupes_23_cancers_taux_brut"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies = len(lies)
    n_non_lies = len(stats["cancers"]) - n_lies
    brut_non_lies_txt = {
        "hausse significative": "la baisse des cancers non liés disparaît&nbsp;: leur taux brut "
                                "est même en hausse significative.",
        "non significatif": "la baisse des cancers non liés disparaît&nbsp;: elle n'est plus "
                            "significative.",
    }.get(g23_brut["non_lies_pesticides"]["significativite"],
          "la baisse des cancers non liés reste significative sur le taux brut.")

    return f"""
  <section id="panel-synthese" class="tab-panel panel-synthese" role="tabpanel" aria-labelledby="tab-synthese" data-tab="synthese" hidden>
    <h2 class="hero-headline">Les cancers liés aux pesticides progressent, les autres reculent</h2>
    <p class="hero-lede">L'incidence des cancers liés aux pesticides croît de façon certaine sur la
      période étudiée. Dans le même temps, l'incidence des cancers non liés aux pesticides
      décroît. Ce constat est inquiétant&nbsp;: il justifie d'en informer la population, afin
      d'agir par prévention plutôt que d'attendre une hypothétique démonstration causale
      définitive.</p>

    <figure class="hero-figure">
      <img src="assets/img/comparaison_indice_base100.png" alt="Comparaison indexée base 100 des cancers à présomption forte/moyenne vs non liés, {y0}-{y1}" loading="lazy">
      <figcaption>Comparaison indexée des deux groupes, base 100 en {y0}, classification Inserm 2021.</figcaption>
    </figure>

    <div class="method-block">
      <h3>Méthode : la classification Inserm 2021</h3>
      <p>L'expertise collective Inserm 2021 {cite('inserm-2021')} documente une présomption de
        lien «&nbsp;forte&nbsp;» ou «&nbsp;moyenne&nbsp;» avec les pesticides pour {n_lies} cancers du registre. En
        indexant chaque groupe à 100 en {y0} pour comparer des croissances plutôt que des
        niveaux&nbsp;:</p>
      <ul class="stat-list">
        <li><strong>{n_lies} cancers à présomption forte ou moyenne</strong>&nbsp;:
          {fmt_trend(g23['lies_pesticides'], unit="points d'indice/an")}</li>
        <li><strong>{n_non_lies} cancers non documentés</strong>&nbsp;:
          {fmt_trend(g23['non_lies_pesticides'], unit="points d'indice/an")}</li>
      </ul>

      <h3>Qu'est-ce qu'un taux standardisé ?</h3>
      <p>Le <em>taux standardisé</em> utilisé ci-dessus neutralise l'effet du
        vieillissement de la population&nbsp;: il recalcule l'incidence que l'on observerait si la
        pyramide des âges de Poitou-Charentes était identique à celle de la population européenne
        de référence. Sans cette correction, une hausse de taux peut simplement refléter une
        population plus âgée — pas un risque individuel plus élevé.</p>
      <p>Cette correction change-t-elle la conclusion&nbsp;? On refait le même calcul avec le taux
        brut, non corrigé de l'âge&nbsp;:</p>
      <ul class="stat-list">
        <li><strong>{n_lies} cancers à présomption forte ou moyenne, taux brut</strong>&nbsp;:
          {fmt_trend(g23_brut['lies_pesticides'], unit="points d'indice/an")}</li>
        <li><strong>{n_non_lies} cancers non documentés, taux brut</strong>&nbsp;:
          {fmt_trend(g23_brut['non_lies_pesticides'], unit="points d'indice/an")}</li>
      </ul>
      <p>Sur le taux brut, la hausse des cancers liés aux pesticides est encore plus marquée. En
        revanche, {brut_non_lies_txt}
        C'est cohérent avec l'effet du vieillissement de la population&nbsp;: sans la correction
        par âge, une population plus âgée masque une baisse de risque réelle par une hausse
        mécanique des cas. La standardisation ne fabrique donc pas la baisse observée chez les
        cancers non liés — elle la révèle.</p>
    </div>

    <figure class="hero-figure">
      <img src="assets/img/comparaison_indice_base100_taux_brut.png" alt="Même comparaison indexée base 100, taux brut non standardisé, {y0}-{y1}" loading="lazy">
      <figcaption>Même comparaison, en taux brut, non standardisé par âge.</figcaption>
    </figure>
  </section>"""


def panel_detail(stats, cancer_table_html):
    y0 = stats["annees"]["min"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies = len(lies)
    n_hausse = sum(1 for c in lies if c["significativite"] == "hausse significative")
    n_baisse = sum(1 for c in lies if c["significativite"] == "baisse significative")
    baisse_court = "un seul" if n_baisse == 1 else str(n_baisse)
    baisse_long = ("Un seul est en baisse significative." if n_baisse == 1
                   else f"{n_baisse} sont en baisse significative.")

    return f"""
  <section id="panel-detail" class="tab-panel" role="tabpanel" aria-labelledby="tab-detail" data-tab="detail" hidden>
    <h2>Détail par cancer</h2>

    <h3 class="hero-headline">{n_hausse} cancers liés aux pesticides en hausse significative,
      contre {baisse_court} en baisse</h3>
    <p class="hero-lede">Sur les {n_lies} cancers pour lesquels l'Inserm documente une présomption
      de lien avec les pesticides, <strong>{n_hausse} présentent une hausse d'incidence
      statistiquement significative</strong> depuis {y0} sur le registre de Poitou-Charentes.
      {baisse_long}</p>

    <p>Tendance individuelle des <strong>{len(stats['cancers'])} cancers</strong> suivis par le
      registre, triés par niveau de présomption Inserm 2021 puis par p-value. La colonne «&nbsp;Note
      bibliographique&nbsp;» reprend des repères issus de la littérature spécifique à chaque cancer. La
      plupart renvoient vers une source vérifiée de l'onglet Sources. Les mentions «&nbsp;Non documenté&nbsp;»
      et «&nbsp;Données insuffisantes&nbsp;» restent des <strong>repères de travail non individuellement
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
      Elle relie l'année et le taux standardisé du cancer concerné. Trois indicateurs permettent d'évaluer la qualité du modèle&nbsp;:
      la p-value, l'intervalle de confiance à 95&nbsp;%, et le R².</p>

    <h3>Pourquoi une droite, et pas une courbe plus complexe ?</h3>
    <p>Cette étude compte seulement {n} points de données par cancer, une valeur par an. Un modèle
      plus complexe qu'une droite, comme un polynôme, une
      exponentielle ou un ajustement local, demanderait d'estimer plus de paramètres. Avec aussi peu de points, il y a un risque de surentraînement du modèle,
      c'est-à-dire que le modèle colle au bruit d'échantillonnage plutôt qu'à la tendance réelle. Une
      droite n'utilise que deux paramètres, la pente et l'ordonnée à l'origine. La pente est directement interprétable en évolution du taux d'incidence par an. Par ailleurs, il s'agit de la méthode
      standard pour détecter une tendance monotone sur une série courte. Un modèle plus complexe se
      justifierait avec plus d'années de données, ou avec une raison claire d'attendre une rupture
      nette, comme un changement à grande échelle des dépistages, par exemple.</p>

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
      statistiques ont donc une puissance limitée. <strong>Un résultat «&nbsp;non significatif&nbsp;» veut
      dire «&nbsp;non démontrable avec ces données&nbsp;»</strong>. Cela ne veut pas dire «&nbsp;absence de lien&nbsp;». Le
      détail complet des limites de cette étude est dans l'onglet <strong>Limites
      méthodologiques</strong>.</p>
  </section>"""


def fr_int(n):
    return f"{n:,}".replace(",", "&nbsp;")


def fmt_trend_cell(st):
    return (f"{sig_badge(st['significativite'])}<br>"
            f"<span class=\"src-note\">pente {fr_num(st['pente'], 3)}, p&nbsp;=&nbsp;{fmt_p(st['p_value'])}</span>")


def panel_versions(stats, cite):
    cmp = stats["comparaison_v1"]
    y0, y1 = stats["annees"]["min"], stats["annees"]["max"]
    v1_max = cmp["annees_v1"]["max"]
    don = cmp["donnees"]
    v1, rev, v2 = cmp["v1"], cmp["revisees_periode_v1"], cmp["v2"]
    lies = [c for c in stats["cancers"] if c["presomption"] != "Non liée"]
    n_lies, n_non_lies = len(lies), len(stats["cancers"]) - len(lies)
    cols = (f"<th>v1<br>{y0}–{v1_max}, données d'origine</th>"
            f"<th>Données révisées<br>{y0}–{v1_max}</th>"
            f"<th>v2<br>{y0}–{y1}</th>")

    # --- données
    totaux = cmp["donnees"]["totaux"]
    ecart_pct = {t["annee"]: (t["v2"] / t["v1"] - 1) * 100 for t in totaux}
    an_max = max(ecart_pct, key=lambda a: abs(ecart_pct[a]))
    autres_max = max(abs(e) for a, e in ecart_pct.items() if a != an_max)
    tr_totaux = "\n".join(
        f"""          <tr><td>{t['annee']}</td><td>{fr_int(t['v1'])}</td><td>{fr_int(t['v2'])}</td><td>{'+' if ecart_pct[t['annee']] >= 0 else ''}{fr_num(ecart_pct[t['annee']])}&nbsp;%</td></tr>"""
        for t in totaux)

    # --- groupes
    groupes = [
        ("groupes", "lies_pesticides", f"Liés aux pesticides, {n_lies} cancers, taux standardisé"),
        ("groupes", "non_lies_pesticides", f"Non documentés, {n_non_lies} cancers, taux standardisé"),
        ("groupes_taux_brut", "lies_pesticides", f"Liés aux pesticides, {n_lies} cancers, taux brut"),
        ("groupes_taux_brut", "non_lies_pesticides", f"Non documentés, {n_non_lies} cancers, taux brut"),
    ]
    tr_groupes = "\n".join(
        f"""          <tr><td>{label}</td><td>{fmt_trend_cell(v1[g][k])}</td><td>{fmt_trend_cell(rev[g][k])}</td><td>{fmt_trend_cell(v2[g][k])}</td></tr>"""
        for g, k, label in groupes)
    sig = lambda g, k, r: r[g][k]["significativite"]
    std_stable = all(sig("groupes", k, v1) == sig("groupes", k, v2) for k in ("lies_pesticides", "non_lies_pesticides"))
    if std_stable:
        constat_std = (
            f"<strong>Le constat principal de l'étude ne change pas.</strong> Sur le taux standardisé, "
            f"la comparaison des deux groupes donne le même résultat en v1 et en v2&nbsp;: "
            f"{sig('groupes', 'lies_pesticides', v2)} pour les cancers liés aux pesticides, "
            f"{sig('groupes', 'non_lies_pesticides', v2)} pour les cancers non documentés.")
    else:
        constat_std = ("<strong>La comparaison des deux groupes sur le taux standardisé change entre v1 et "
                       "v2</strong>&nbsp;: voir le tableau ci-dessous.")
    noms_groupes = {"lies_pesticides": "cancers liés aux pesticides", "non_lies_pesticides": "cancers non documentés"}
    brut_changes = [
        f"la tendance des {noms_groupes[k]} passe de «&nbsp;{sig('groupes_taux_brut', k, v1)}&nbsp;» en v1 "
        f"à «&nbsp;{sig('groupes_taux_brut', k, v2)}&nbsp;» en v2"
        for k in noms_groupes if sig("groupes_taux_brut", k, v1) != sig("groupes_taux_brut", k, v2)]
    constat_brut = ""
    if brut_changes:
        constat_brut = (f"<p>Sur le taux brut, non corrigé du vieillissement de la population, "
                        f"{'&nbsp;; '.join(brut_changes)}. L'onglet Synthèse explique pourquoi taux brut et "
                        f"taux standardisé peuvent évoluer différemment.</p>")

    # --- cancers
    noms = sorted(v2["cancers"])
    changes = [n for n in noms
               if len({v1["cancers"][n]["significativite"], rev["cancers"][n]["significativite"],
                       v2["cancers"][n]["significativite"]}) > 1]
    par_revision = [n for n in changes
                    if v1["cancers"][n]["significativite"] != rev["cancers"][n]["significativite"]]
    par_annee = [n for n in changes
                 if rev["cancers"][n]["significativite"] != v2["cancers"][n]["significativite"]]

    origines = []
    if par_revision:
        origines.append(f"<li><strong>Révision des données {y0}–{v1_max}</strong> par le registre&nbsp;: "
                        f"{'&nbsp;; '.join(escape(n) for n in par_revision)}.</li>")
    if par_annee:
        origines.append(f"<li><strong>Ajout de l'année {y1}</strong>&nbsp;: "
                        f"{'&nbsp;; '.join(escape(n) for n in par_annee)}.</li>")
    origines_html = "\n      ".join(origines)
    tr_cancers = "\n".join(
        f"""          <tr><td>{escape(n)}</td><td>{fmt_trend_cell(v1['cancers'][n])}</td><td>{fmt_trend_cell(rev['cancers'][n])}</td><td>{fmt_trend_cell(v2['cancers'][n])}</td></tr>"""
        for n in changes)
    p_v1 = [v1["cancers"][n]["p_value"] for n in changes] or [0]
    fourchette = f"entre {fmt_p(min(p_v1))} et {fmt_p(max(p_v1))}"
    if 0.01 <= min(p_v1) and max(p_v1) <= 0.1:
        seuil_txt = (f"Ces {len(changes)} cancers avaient tous en v1 une p-value proche du seuil de 0,05"
                     f", {fourchette}&nbsp;: leur tendance était déjà à la limite de la significativité.")
    else:
        seuil_txt = f"En v1, la p-value de ces {len(changes)} cancers était comprise {fourchette}."
    bloc_cancers = (
        f"<p><strong>{len(noms) - len(changes)} cancers sur {len(noms)} gardent la même tendance.</strong></p>"
        if not changes else f"""
    <p><strong>{len(noms) - len(changes)} cancers sur {len(noms)} gardent la même tendance qu'en
      v1.</strong> Pour {len(changes)} cancers, la tendance change d'étiquette, par exemple de
      «&nbsp;non significatif&nbsp;» à «&nbsp;hausse significative&nbsp;». {"Ces changements ont deux origines" if len(origines) == 2 else "Origine du changement"}&nbsp;:</p>
    <ul class="text-list">
      {origines_html}
    </ul>
    <p>{seuil_txt}</p>
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Cancer</th>{cols}</tr></thead>
        <tbody>
{tr_cancers}
        </tbody>
      </table>
    </div>""")

    return f"""
  <section id="panel-versions" class="tab-panel" role="tabpanel" aria-labelledby="tab-versions" data-tab="versions" hidden>
    <h2>Mise à jour des données&nbsp;: ce qui change entre la v1 et la v2</h2>
    <p>La première version de ce site, la v1, présentait les données du registre disponibles en
      février 2026, quand nous avons commencé cette étude. Elles portaient sur les années {y0} à
      {v1_max}. La v2 présente les données consolidées par le registre en
      {REGISTRE_MAJ.split(" ", 1)[1]}, sur les années {y0} à {y1}. Nous publions ici la
      comparaison des deux versions, pour que chacun puisse vérifier l'effet de la mise à jour sur les résultats.</p>

    <div class="method-block">
      <h3>En bref</h3>
      <p>{constat_std}</p>
      {constat_brut}
      <p>Les autres changements portent sur des tendances individuelles dont la p-value était
        proche du seuil de 0,05. Le détail figure plus bas.</p>
    </div>

    <h3>Ce qui change dans les données</h3>
    <p>Le {REGISTRE_MAJ}, le registre des cancers de Poitou-Charentes {cite('registre-cancers-pc')}
      a mis en ligne les données {y1}. Il a aussi republié toutes les années {y0} à {v1_max},
      avec des effectifs révisés. Tous sexes confondus, le registre publie un effectif par cancer et
      par année. Sur cette période, {don['n_effectifs_modifies']} de ces {don['n_effectifs']}
      effectifs ont été modifiés, de {fr_num(don['ecart_median_cas'], 0)} cas en valeur médiane. Le
      registre ne détaille pas sur son site les raisons de ces révisions.</p>
    <p>La révision la plus importante porte sur l'année {an_max}, avec
      {'+' if ecart_pct[an_max] >= 0 else ''}{fr_num(ecart_pct[an_max])}&nbsp;% de cas au total.
      Pour les autres années, le nombre total de cas change de moins de
      {fr_num(autres_max)}&nbsp;%.</p>
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Année</th><th>Total des cas, v1</th><th>Total des cas, v2</th><th>Écart</th></tr></thead>
        <tbody>
{tr_totaux}
        </tbody>
      </table>
    </div>

    <h3>Ce qui change dans les résultats</h3>
    <p>Pour distinguer les deux effets, l'étude compare trois calculs faits avec exactement la même
      méthode&nbsp;: les résultats publiés en v1, les mêmes calculs sur les données révisées de la
      même période, {y0}–{v1_max}, et les résultats v2 sur {y0}–{y1}. L'écart entre les deux
      premières colonnes vient uniquement des révisions du registre. L'écart entre les deux
      dernières vient uniquement de l'année ajoutée.</p>

    <h4>Comparaison des deux groupes de cancers, en indice base 100</h4>
    <div class="table-scroll">
      <table class="data-table">
        <thead><tr><th>Groupe</th>{cols}</tr></thead>
        <tbody>
{tr_groupes}
        </tbody>
      </table>
    </div>

    <h4>Tendances par cancer</h4>
{bloc_cancers}

    <h3>Pourquoi une tendance peut changer d'étiquette</h3>
    <p>L'étude qualifie une tendance de significative quand sa p-value est inférieure à 0,05. Ce
      seuil est une convention&nbsp;: une tendance à p&nbsp;=&nbsp;0,045 et une autre à
      p&nbsp;=&nbsp;0,055 sont en pratique d'une solidité très proche, mais reçoivent des
      étiquettes différentes. Quand une p-value est proche du seuil, une petite révision des
      données ou une année de plus suffit à la faire passer d'un côté à l'autre. C'est pourquoi
      l'onglet Détail par cancer donne la p-value de chaque tendance, et pas seulement son
      étiquette.</p>
  </section>"""


def panel_endocriniens(cite):
    return f"""
  <section id="panel-endocriniens" class="tab-panel" role="tabpanel" aria-labelledby="tab-endocriniens" data-tab="endocriniens" hidden>
    <h2>Perturbateurs endocriniens</h2>

    <p>Un perturbateur endocrinien, ou PE, est <strong>une substance qui interfère avec le système
      hormonal</strong>. Toutes les substances de ce type présentes dans notre environnement ne
      proviennent pas des pesticides&nbsp;: certaines viennent des pesticides, comme les organophosphates,
      les pyréthrinoïdes, le 2,4-D ou le métolachlore, d'autres des plastiques ou des cosmétiques,
      comme le bisphénol A, les phtalates, les parabènes ou le triclosan, et n'ont rien à voir avec
      l'alimentation. Cette
      distinction compte&nbsp;: toutes les études sur les PE ne parlent pas de pesticides, et
      toutes ne relèvent donc pas de l'onglet Alimentation.</p>

    <p>L'expertise collective Inserm 2021 {cite('inserm-2021', 'natura-sciences-inserm')} établit
      un lien entre <strong>l'exposition aux pesticides</strong> et des <strong>leucémies
      aiguës</strong> et <strong>tumeurs cérébrales</strong> chez l'enfant en cas d'exposition
      parentale avant la naissance. L'expertise Inserm 2021 établit aussi un lien entre
      l'exposition aux pesticides et six familles de troubles non cancéreux&nbsp;: <strong>maladie
      de Parkinson</strong> et autres <strong>troubles neurodégénératifs</strong>, <strong>troubles
      cognitifs</strong>, <strong>troubles digestifs</strong>, <strong>troubles respiratoires, dont
      la BPCO</strong>, <strong>troubles hormonaux</strong> et de la <strong>reproduction</strong>,
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
      urinaire de fongicide, l'ETU, plus élevé en période estivale de traitement des
      vignes</strong> — un exemple reliant directement exposition agricole et santé de
      l'enfant.</p>

    <p>Les pesticides peuvent atteindre <strong>le fœtus lui-même</strong>. Une étude suédoise
      {cite('bjorvang-2021')} a recherché 22 polluants organiques persistants dans les organes de
      20 fœtus issus de grossesses interrompues par une mort in utero au 3ᵉ trimestre&nbsp;: au
      moins 15 de ces substances, dont les pesticides organochlorés hexachlorobenzène et
      p,p'-DDE, ce dernier issu du DDT, <strong>ont été retrouvées dans chaque organe analysé, cerveau
      compris, parfois à des niveaux supérieurs à ceux mesurés dans le sang maternel ou le
      placenta</strong>.</p>

    <p>Le projet européen ENDOMIX {cite('endomix')}, mené de 2024 à 2027 avec l'Inserm-Irset de
      Rennes, étudie
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
      alimentation conventionnelle. En France, la cohorte bretonne PELAGIE
      {cite('pelagie-cohort')}, portant sur 245 enfants de 6 ans, trouve qu'une consommation fréquente de produits biologiques est
      associée à des taux urinaires plus bas de deux métabolites de pesticides.</p>

    <p>Le levier le plus direct pour l'exposition alimentaire des enfants en dehors du foyer est
      la restauration collective. La loi EGalim {cite('egalim')} impose, depuis 2022, 20&nbsp;% de
      bio et 50&nbsp;% de produits durables dans la restauration collective publique, étendue à
      l'ensemble de la restauration collective depuis 2024. En pratique, <strong>la part de bio
      observée en 2023 était de 12,1&nbsp;% en moyenne — sous l'objectif légal</strong>, ce qui
      laisse une marge de progression directement actionnable localement, notamment via les
      cantines scolaires.</p>
  </section>"""


def panel_riverains(cite):
    return f"""
  <section id="panel-riverains" class="tab-panel" role="tabpanel" aria-labelledby="tab-riverains" data-tab="riverains" hidden>
    <h2>Vivre près des cultures : exposition des riverains et cancers de l'enfant</h2>

    <p>Le registre des cancers de Poitou-Charentes publie des chiffres pour l'ensemble de
      l'ex-région, sans le lieu de résidence des malades. <strong>L'étude ne peut donc pas tester
      elle-même un lien entre proximité des champs et cancers.</strong> Cet onglet rapporte ce
      qu'en dit la littérature scientifique publiée, et ses limites.</p>

    <h3>Ce que montrent les études épidémiologiques</h3>

    <p>Une revue de la littérature publiée en 2026 dans l'<em>International Journal of
      Cancer</em> {cite('vandesteeg-2026')} recense les études parues entre 1980 et 2022 sur les
      leucémies et tumeurs cérébrales de l'enfant. Elle constate des <strong>risques accrus de
      cancers de l'enfant dans les zones de forte densité de cultures</strong>. Il s'agit d'un
      inventaire, pas d'une méta-analyse&nbsp;: elle ne calcule pas de risque global. Trois études
      citées par cette revue illustrent ce constat&nbsp;:</p>

    <ul class="text-list">
      <li><strong>Espagne</strong> {cite('gomez-barroso-2016')}&nbsp;: sur 3&nbsp;350 enfants
        malades et 20&nbsp;365 témoins, les enfants du quart le plus exposé, mesuré par la part de surface
        cultivée dans un rayon de 1&nbsp;km, ont un risque de leucémie environ 2,5 fois plus élevé que ceux
        sans culture à proximité, et un risque de tumeur cérébrale environ 3,7 fois plus
        élevé.</li>
      <li><strong>Danemark</strong> {cite('patel-2020')}&nbsp;: dans une cohorte nationale de
        naissance, le risque de leucémie de l'enfant est <strong>doublé</strong> quand la mère
        vivait, pendant la grossesse, avec plus de 24&nbsp;ha de cultures à moins de 500&nbsp;m.
        L'étude ne trouve pas d'association avec les tumeurs cérébrales.</li>
      <li><strong>Californie</strong> {cite('rull-2009')}&nbsp;: en croisant les adresses des
        enfants avec les déclarations officielles d'épandage, le risque de leucémie aiguë
        lymphoblastique augmente pour une exposition modérée à certaines familles de pesticides,
        notamment les organophosphorés, dans un rayon d'environ 800&nbsp;m, mais pas pour
        l'exposition la plus forte. Les auteurs qualifient cette hausse de modeste.</li>
    </ul>

    <p>En France, l'étude GEOCAP de l'Inserm et de Santé publique France {cite('geocap-2023')}
      porte sur <strong>tous les cas de leucémie de l'enfant diagnostiqués en France
      métropolitaine entre 2006 et 2013</strong>, soit 3&nbsp;711 cas. La simple présence de vignes
      à moins de 1&nbsp;km n'est pas associée à un risque accru. En revanche, plus la surface de
      vignes autour du domicile est grande, plus le risque de leucémie aiguë lymphoblastique
      augmente&nbsp;: <strong>+5&nbsp;% pour chaque hausse de 10 points de la part de vignes dans
      un rayon de 1&nbsp;km</strong>. Les auteurs parlent d'une légère hausse, qui varie selon les
      régions. Environ 20&nbsp;% des enfants témoins de Nouvelle-Aquitaine vivent à moins de
      1&nbsp;km de vignes.</p>

    <h3>Ce que l'on sait de l'exposition réelle des riverains</h3>

    <p>L'étude PestiRiv de Santé publique France et de l'Anses {cite('pestiriv-2025')} a mesuré
      les pesticides chez 1&nbsp;946 adultes et 742 enfants, dans six régions viticoles. Par
      rapport aux personnes vivant à plus de 1&nbsp;km de toute culture, celles qui vivent à moins
      de 500&nbsp;m de vignes sont <strong>majoritairement plus exposées</strong>, dans tous les
      types d'échantillons analysés, qu'il s'agisse des urines, des cheveux, des poussières ou de
      l'air intérieur et extérieur, et davantage
      encore pendant les périodes de traitement. L'exposition augmente quand la distance aux vignes
      diminue et quand la quantité de pesticides utilisée augmente. PestiRiv mesure l'exposition,
      pas ses effets&nbsp;: elle ne dit rien du risque de cancer.</p>

    <h3>Comment lire ces résultats</h3>

    <p>Ces études mesurent la <strong>distance aux champs ou la densité de cultures</strong>, pas
      la quantité de pesticides reçue par chaque enfant. Ce sont des études d'observation&nbsp;:
      elles mettent en évidence des associations, pas une preuve de causalité. Leurs auteurs
      signalent eux-mêmes l'absence de données individuelles, comme le métier des parents ou
      les usages domestiques de pesticides, et des effectifs parfois faibles.</p>

    <p>Ces résultats ne contredisent pas l'onglet Alimentation. Pour la population générale,
      l'alimentation reste la voie d'exposition dominante aux pesticides. Pour les personnes qui
      vivent près des cultures, PestiRiv montre qu'une exposition par l'air et les poussières
      <strong>s'y ajoute</strong>.</p>
  </section>"""


def panel_labels(cite):
    return f"""
  <section id="panel-labels" class="tab-panel" role="tabpanel" aria-labelledby="tab-labels" data-tab="labels" hidden>
    <h2>Labels alimentaires : pourquoi le bio est le seul qui compte face aux pesticides</h2>

    <p>Plusieurs labels affichés en rayon parlent d'environnement ou de qualité, mais tous ne
      disent pas la même chose sur les pesticides. Voici les trois que l'on croise le plus souvent,
      et ce qu'ils garantissent réellement.</p>

    <h3>Bio, AB ou Eurofeuille&nbsp;: une interdiction de méthode</h3>
    <p>Le label bio européen {cite('ademe-label-bio')} repose sur un règlement qui
      <strong>interdit les pesticides et engrais de synthèse ainsi que les OGM</strong>, vérifié
      par des organismes certificateurs indépendants qui contrôlent l'exploitation elle-même — pas
      seulement le produit fini. Nuance à ne pas perdre de vue&nbsp;: le bio autorise encore une
      trentaine de substances naturelles ou de biocontrôle, comme le cuivre, le soufre, les
      extraits végétaux et les micro-organismes {cite('agence-bio-naturels')}. <strong>Le bio garantit l'absence de
      pesticides de synthèse, pas une absence totale et absolue de tout traitement</strong> — mais
      c'est aujourd'hui le seul label à interdire la méthode elle-même plutôt qu'à mesurer un
      résultat.</p>

    <h3>HVE, Haute Valeur Environnementale&nbsp;: un score, pas une interdiction</h3>
    <p>Le label d'État HVE {cite('que-choisir-hve')} <strong>n'interdit pas les pesticides de
      synthèse</strong>. Il note l'exploitation sur quatre thématiques&nbsp;: biodiversité, stratégie
      phytosanitaire, fertilisation et irrigation. Un score faible sur les pesticides peut être
      compensé par de bons scores ailleurs. France Nature Environnement, Agir pour
      l'environnement, la Confédération paysanne et Synabio alertent sur une confusion fréquente
      avec le bio dans l'esprit des consommateurs, alors que les deux labels ne garantissent pas
      la même chose.</p>

    <h3>Zéro Résidu de Pesticides : un résultat, pas une méthode</h3>
    <p>Ce label privé {cite('generations-futures-zrp')}, non officiel et sans contrôle indépendant
      obligatoire, fonctionne à l'inverse du bio&nbsp;: <strong>les pesticides de synthèse restent
      autorisés pendant la culture</strong>, à condition qu'il n'en reste pas de trace détectable
      au moment de la récolte, avec un seuil de 0,01&nbsp;mg/kg. Concrètement, les sols, l'eau, l'air et la
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
    ("accueil", "Accueil"),
    ("synthese", "Synthèse"),
    ("detail", "Détail par cancer"),
    ("methodologie", "Étude statistique"),
    ("versions", "Mise à jour v1 → v2"),
    ("endocriniens", "Perturbateurs endocriniens"),
    ("alimentation", "Alimentation"),
    ("riverains", "Riverains"),
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
<meta name="description" content="Étude statistique de l'évolution des cancers en Poitou-Charentes de {y0} à {y1} au regard de l'exposition aux pesticides, des perturbateurs endocriniens et de l'alimentation conventionnelle vs biologique.">
<link rel="stylesheet" href="style.css">
</head>
<body>

<header class="site-header">
  <div class="wrap header-inner">
    <div class="header-text">
      <h1>Cancers, pesticides et alimentation</h1>
      <p class="subtitle">Étude réalisée à partir du registre des cancers de Poitou-Charentes,
        {y0}–{y1}, et de la littérature scientifique — Santé des Territoires Aigondigné</p>
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
        panel_accueil(stats, cite),
        panel_synthese(stats, cite),
        panel_detail(stats, cancer_table_html),
        panel_methodologie(stats),
        panel_versions(stats, cite),
        panel_endocriniens(cite),
        panel_alimentation(cite),
        panel_riverains(cite),
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

    html = typo_fr(build_page(stats, panels))
    (SITE / "index.html").write_text(html, encoding="utf-8")

    scripts_dir = Path(__file__).parent
    shutil.copy(scripts_dir / "site_style.css", SITE / "style.css")
    shutil.copy(scripts_dir / "site_tabs.js", SITE / "tabs.js")

    print(f"Site généré dans {SITE} ({len(all_entries)} sources, {len(stats['cancers'])} cancers, "
          f"{sum(1 for c in stats['cancers'] if c['presomption'] != 'Non liée')} liés pesticides).")


if __name__ == "__main__":
    main()
