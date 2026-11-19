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
from markupsafe import Markup

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


def spark(item, largura=132, altura=40, neutro=False):
    """Minigráfico SVG (linha + área) a partir de item["hist"]."""
    vals = (item or {}).get("hist") or []
    if len(vals) < 2:
        return Markup("")
    lo, hi = min(vals), max(vals)
    faixa = (hi - lo) or 1
    passo = largura / (len(vals) - 1)
    pts = [(i * passo, altura - 3 - (v - lo) / faixa * (altura - 6)) for i, v in enumerate(vals)]
    linha = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{altura} " + linha + f" {largura},{altura}"
    # juros e inflação: cor fixa, porque subir ou cair não é "bom" ou "ruim" por si só
    tendencia = "fixo" if neutro else ("sobe" if vals[-1] > vals[0] else ("desce" if vals[-1] < vals[0] else "neutro"))
    x, y = pts[-1]
    return Markup(
        f'<svg class="spark {tendencia}" viewBox="0 0 {largura} {altura}" preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon class="area" points="{area}"/><polyline class="linha" points="{linha}" pathLength="1"/>'
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6"/></svg>'
    )


def manchete(b):
    """Frase do dia montada a partir dos números."""
    partes = []
    ibov = (b.get("bolsa") or {}).get("ibov")
    dolar = (b.get("cambio") or {}).get("dolar")
    if ibov and ibov.get("var_pct") is not None:
        v = ibov["var_pct"]
        verbo = "sobe" if v > 0.05 else ("cai" if v < -0.05 else "fica estável")
        partes.append(f"Ibovespa {verbo}" + (f" {num(abs(v))}%" if abs(v) > 0.05 else ""))
    if dolar and dolar.get("var_pct") is not None:
        v = dolar["var_pct"]
        verbo = "sobe" if v > 0.05 else ("cai" if v < -0.05 else "fica estável")
        partes.append(f"dólar {verbo}" + (f" a R$ {num(dolar['valor'])}" if abs(v) > 0.05 else f" em R$ {num(dolar['valor'])}"))
    if not partes:
        selic = (b.get("juros") or {}).get("selic")
        return f"Selic em {num(selic['valor'])}% ao ano" if selic else "O mercado hoje"
    return ", ".join(partes)



def compacto(v):
    """R$ 1,2 bi / R$ 350 mi"""
    if v is None:
        return "–"
    if v >= 1e9:
        return f"R$ {num(v / 1e9, 1)} bi"
    if v >= 1e6:
        return f"R$ {num(v / 1e6, 0)} mi"
    return f"R$ {num(v, 0)}"


def hora(iso, referencia=None):
    """Mostra só a hora se for do mesmo dia do boletim; senão, a data."""
    if not iso:
        return ""
    if referencia and iso[:10] == referencia[:10]:
        return iso[11:16].replace(":", "h")
    return data_curta(iso)


def focus_tabela(focus):
    """Agrupa as linhas do Focus por indicador, mantendo a ordem."""
    if not focus:
        return []
    grupos = {}
    for l in focus["linhas"]:
        g = grupos.setdefault(l["indicador"], {"indicador": l["indicador"], "anos": []})
        dif = None if l.get("semana_anterior") is None else round(l["mediana"] - l["semana_anterior"], 2)
        g["anos"].append({**l, "dif": dif})
    return list(grupos.values())


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
    env.filters.update(num=num, data_longa=data_longa, data_curta=data_curta, spark=spark, hora=hora, compacto=compacto)
    env.globals.update(manchete=manchete)
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
        b["focus_tab"] = focus_tabela(b.get("focus"))
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
