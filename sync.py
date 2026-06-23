#!/usr/bin/env python3
"""
Sync (Notion) -> dashboard (index.html).

Lê os boards de tarefas no Notion, junta tudo (deduplicando tarefas
repetidas entre boards pelo título) e reescreve o bloco `const DATA = {...}`
do index.html, entre os marcadores /*DATA:START*/ e /*DATA:END*/.

Roda no GitHub Actions às 8h, 12h, 16h e 20h (BRT). Só usa a stdlib.
"""
import os
import re
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TOKEN = os.environ.get("NOTION_TOKEN")
if not TOKEN:
    sys.exit("ERRO: variável de ambiente NOTION_TOKEN não definida.")

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# Boards de tarefas a agregar. Em todos, status="Status" e projeto="Projeto";
# só o nome do título e do responsável muda entre boards.
BOARDS = [
    {"name": "Mission Control", "db": "51a9778a71e14d089e69861633ed78aa", "title": "Task",   "people": "Nome"},
    {"name": "PO&PM",           "db": "5a82da1e86074878937460102cc5cd11", "title": "Tarefa", "people": "Responsável"},
]

# Status (de qualquer board) -> bucket do dashboard
DONE = {"Feito"}
WIP = {"Em progresso", "Em revisão", "Bloqueado por QA", "Em andamento"}
TODO = {"Iniciar", "Backlog", "A fazer"}


def api_post(path, body):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}")


def fetch_board(db_id):
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = api_post(f"/databases/{db_id}/query", body)
        pages.extend(pg for pg in data.get("results", [])
                     if not pg.get("archived") and not pg.get("in_trash"))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return pages


def p_title(pr, field):
    return "".join(x.get("plain_text", "") for x in pr.get(field, {}).get("title", [])).strip()


def p_status(pr):
    s = pr.get("Status", {}).get("status")
    return s["name"] if s else None


def p_select(pr, name):
    s = pr.get(name, {}).get("select")
    return s["name"] if s else None


def p_people(pr, field):
    return [u.get("name") or "Sem nome" for u in pr.get(field, {}).get("people", [])]


def bucket(status):
    if status in DONE:
        return "d"
    if status in WIP:
        return "w"
    if status in TODO:
        return "t"
    return "s"


def main():
    raw = []
    for b in BOARDS:
        try:
            pages = fetch_board(b["db"])
        except RuntimeError as e:
            print(f"AVISO: board '{b['name']}' indisponível ({e}). Pulando — "
                  f"a integração dash-notion está conectada a ele? (board -> ⋯ -> Conexões)")
            continue
        print(f"  {b['name']}: {len(pages)} cards")
        for pg in pages:
            pr = pg["properties"]
            raw.append({
                "title": p_title(pr, b["title"]),
                "status": p_status(pr),
                "projeto": p_select(pr, "Projeto") or "Sem projeto",
                "people": p_people(pr, b["people"]) or ["Sem responsável"],
            })

    if not raw:
        sys.exit("ERRO: 0 tarefas em todos os boards. A integração dash-notion "
                 "está conectada aos boards? (board -> ⋯ -> Conexões)")

    # Dedup por título: mesma tarefa repetida entre boards conta 1x.
    # Mantém a 1ª ocorrência (ordem dos BOARDS). Cards sem título não deduplicam.
    seen, rows, dups = set(), [], 0
    for r in raw:
        key = r["title"].strip().lower()
        if key and key in seen:
            dups += 1
            continue
        if key:
            seen.add(key)
        rows.append(r)

    total = len(rows)
    g = {"d": 0, "w": 0, "t": 0, "s": 0}
    for r in rows:
        g[bucket(r["status"])] += 1

    # por projeto
    projd = {}
    for r in rows:
        d = projd.setdefault(r["projeto"], {"d": 0, "w": 0, "t": 0, "s": 0, "pe": set()})
        d[bucket(r["status"])] += 1
        for who in r["people"]:
            if who != "Sem responsável":
                d["pe"].add(who)
    proj = [{"n": n, "d": v["d"], "w": v["w"], "t": v["t"], "s": v["s"], "pe": len(v["pe"])}
            for n, v in projd.items()]
    proj.sort(key=lambda x: -(x["d"] + x["w"] + x["t"] + x["s"]))

    # por pessoa + matriz pessoa x projeto
    pessd, matrix = {}, {}
    for r in rows:
        for who in r["people"]:
            d = pessd.setdefault(who, {"d": 0, "w": 0, "t": 0, "s": 0, "fr": set()})
            d[bucket(r["status"])] += 1
            d["fr"].add(r["projeto"])
            mm = matrix.setdefault(who, {})
            mm[r["projeto"]] = mm.get(r["projeto"], 0) + 1
    pess = [{"n": n, "d": v["d"], "w": v["w"], "t": v["t"], "s": v["s"], "fr": len(v["fr"])}
            for n, v in pessd.items()]
    pess.sort(key=lambda x: -(x["d"] + x["w"] + x["t"] + x["s"]))

    done, wip, todo, nost = g["d"], g["w"], g["t"], g["s"]
    pct = round(100 * done / total) if total else 0
    iniciar = sum(1 for r in rows if r["status"] in ("Iniciar", "A fazer"))
    backlog = sum(1 for r in rows if r["status"] == "Backlog")
    blocked = sum(1 for r in rows if r["status"] == "Bloqueado por QA")
    sem_dono = sum(1 for r in rows if r["people"] == ["Sem responsável"])
    sem_proj = sum(1 for r in rows if r["projeto"] == "Sem projeto")
    real = [p for p in pess if p["n"] != "Sem responsável"]
    ativas = len([p for p in real if (p["d"] + p["w"] + p["t"] + p["s"]) > 0])
    projects = len(set(r["projeto"] for r in rows if r["projeto"] != "Sem projeto"))

    kpis = [
        {"l": "Tasks no board", "v": total, "d": f"{ativas} pessoas · {projects} projetos"},
        {"l": "Concluído", "v": f"{pct}%", "d": f"{done} de {total} feitas", "c": "var(--done)"},
        {"l": "Em andamento", "v": wip, "d": "WIP simultâneo", "c": "var(--wip)"},
        {"l": "A fazer", "v": todo, "d": f"{iniciar} iniciar · {backlog} backlog"},
        {"l": "Bloqueado por QA", "v": blocked, "d": "em revisão / QA", "c": "var(--gap)"},
        {"l": "Lacunas de governança", "v": f"{sem_dono} / {sem_proj}", "d": "sem dono / sem projeto", "c": "var(--pri2)"},
    ]
    donut = [{"l": "Feito", "v": done}, {"l": "Em andamento", "v": wip},
             {"l": "A fazer", "v": todo}, {"l": "Sem status", "v": nost}]

    # alertas gerados a partir dos dados
    alerts = []
    if proj and total:
        top = proj[0]
        tt = top["d"] + top["w"] + top["t"] + top["s"]
        bus = f" em {top['pe']} pessoa(s) — bus factor {top['pe']}." if top["pe"] and top["pe"] <= 2 else "."
        alerts.append({"ic": "⬢", "c": "#C9871C",
                       "h": f"{top['n']} = {round(100 * tt / total)}% do board",
                       "p": f"{tt} das {total} tasks concentradas em {top['n']}{bus}"})
    sob = [p for p in real if total and (p["d"] + p["w"] + p["t"] + p["s"]) / total * 100 > 25]
    if sob:
        alerts.append({"ic": "▲", "c": "#D2453C",
                       "h": f"{len(sob)} pessoa(s) sobrecarregada(s)",
                       "p": "Acima de 25% do board: " + ", ".join(p["n"] for p in sob) + "."})
    if blocked:
        alerts.append({"ic": "!", "c": "#D2453C",
                       "h": f"{blocked} task(s) bloqueada(s) por QA",
                       "p": "Frentes travadas aguardando revisão / QA."})
    frag = [p for p in real if p["fr"] >= 5]
    if frag:
        alerts.append({"ic": "⇄", "c": "#6A5FD0", "h": "Fragmentação de contexto",
                       "p": ", ".join(f"{p['n']} ({p['fr']} frentes)" for p in frag)
                            + " — troca de contexto derruba a vazão."})
    alerts.append({"ic": "▤", "c": "#8C87A6", "h": "Higiene do board",
                   "p": f"{sem_dono} sem dono · {sem_proj} sem projeto · {nost} sem status."})

    brt = timezone(timedelta(hours=-3))
    snapshot = datetime.now(timezone.utc).astimezone(brt).strftime("%d/%m/%Y %H:%M")

    data = {
        "snapshot": snapshot, "total": total, "people": ativas, "projects": projects,
        "kpis": kpis, "donut": donut, "proj": proj, "pess": pess,
        "matrix": matrix, "alerts": alerts,
    }

    with open("index.html", encoding="utf-8") as f:
        html = f.read()
    block = "/*DATA:START*/\nconst DATA = " + json.dumps(data, ensure_ascii=False) + ";\n/*DATA:END*/"
    new, n = re.subn(r"/\*DATA:START\*/.*?/\*DATA:END\*/", lambda m: block, html, flags=re.DOTALL)
    if n == 0:
        sys.exit("ERRO: marcadores /*DATA:START*/ … /*DATA:END*/ não encontrados no index.html.")
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(new)
    print(f"OK: {total} tasks ({dups} duplicatas removidas) · {len(proj)} projetos · "
          f"{len(pess)} pessoas · {pct}% concluído.")


if __name__ == "__main__":
    main()
