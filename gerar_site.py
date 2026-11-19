"""
Gera o site estático a partir dos JSONs em data/historico e dos seus
comentários em comentarios/AAAA-MM-DD.md.

Uso:
  python gerar_site.py            -> usa os dados reais coletados
  python gerar_site.py --exemplo  -> usa data/exemplo.json (para visualizar)

Saída: pasta site/  (é ela que vai para o GitHub Pages)
"""
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE = Path(__file__).parent
SAIDA = BASE / "site"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
        "sexta-feira", "sábado", "domingo"]


def num(v, casas=2):
    if v is None:
        return "–"
    s = f"{v:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def data_longa(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def data_curta(iso):
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    return f"{d.day:02d}/{d.month:02d}"


def comentario(iso):
    arq = BASE / "comentarios" / f"{iso}.md"
    if arq.exists():
        return markdown.markdown(arq.read_text(encoding="utf-8"))
    return ""


def main():
    exemplo = "--exemplo" in sys.argv
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

    if exemplo:
        boletins = [json.loads((BASE / "data" / "exemplo.json").read_text(encoding="utf-8"))]
    else:
        arquivos = sorted((BASE / "data" / "historico").glob("*.json"), reverse=True)
        boletins = [json.loads(a.read_text(encoding="utf-8")) for a in arquivos]
    if not boletins:
        sys.exit("Nenhum boletim encontrado. Rode coletar.py antes (ou use --exemplo).")

    env = Environment(loader=FileSystemLoader(BASE / "templates"), autoescape=select_autoescape())
    env.filters.update(num=num, data_longa=data_longa, data_curta=data_curta)
    css = (BASE / "templates" / "style.css").read_text(encoding="utf-8")
    ctx = {"cfg": cfg, "css": css, "arquivo": [b["data_boletim"] for b in boletins]}

    if SAIDA.exists():
        shutil.rmtree(SAIDA)
    SAIDA.mkdir()

    def salvar(caminho, html):
        destino = SAIDA / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(html, encoding="utf-8")

    tpl = env.get_template("boletim.html")
    for i, b in enumerate(boletins):
        b["comentario_html"] = comentario(b["data_boletim"])
        raiz = "../../"
        salvar(f"boletim/{b['data_boletim']}/index.html", tpl.render(b=b, raiz=raiz, **ctx))
        if i == 0:
            salvar("index.html", tpl.render(b=b, raiz="", capa=True, **ctx))

    for pagina in ["arquivo", "sobre", "aviso-legal", "privacidade"]:
        salvar(f"{pagina}/index.html", env.get_template(f"{pagina}.html").render(raiz="../", **ctx))

    (SAIDA / ".nojekyll").write_text("")
    print(f"Site gerado em {SAIDA} com {len(boletins)} boletim(ns).")


if __name__ == "__main__":
    main()
