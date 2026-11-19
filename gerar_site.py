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

from fundamentos import indicadores

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


def compacto_sinal(v):
    if v is None:
        return "–"
    return ("-" if v < 0 else "") + compacto(abs(v))


def milhar(v):
    return "–" if v is None else num(v, 0)


def mes_ano(ref):
    """'2026-10' -> 'out/2026'"""
    if not ref:
        return ""
    return f"{MESES[int(ref[5:7]) - 1][:3]}/{ref[:4]}"


def barras(hist, largura=520, altura=150):
    """Gráfico de barras SVG do DY mensal (valores em fração)."""
    pts = [(h["mes"], h["dy"] * 100) for h in hist if h.get("dy") is not None]
    if not pts:
        return Markup('<p class="vazio">Sem dados de DY informados.</p>')
    topo = max(v for _, v in pts) or 1
    passo = largura / len(pts)
    larg = max(passo * 0.62, 2)
    partes = []
    for i, (mes, v) in enumerate(pts):
        h = max(v / topo * (altura - 26), 1)
        x = i * passo + (passo - larg) / 2
        partes.append(f'<rect x="{x:.1f}" y="{altura - 18 - h:.1f}" width="{larg:.1f}" height="{h:.1f}" rx="2">'
                      f'<title>{mes_ano(mes)}: {num(v)}%</title></rect>')
        if i % 3 == 0 or i == len(pts) - 1:
            cx = min(max(x + larg / 2, 16), largura - 16)  # não deixa o rótulo sair do gráfico
            partes.append(f'<text x="{cx:.1f}" y="{altura - 4}" text-anchor="middle">{mes_ano(mes)[:3]}/{mes[2:4]}</text>')
    ultimo = pts[-1][1]
    return Markup(f'<svg class="barras" viewBox="0 0 {largura} {altura}" role="img" '
                  f'aria-label="DY mensal; último mês {num(ultimo)}%">{"".join(partes)}</svg>')


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


def logo(url, tamanho=28):
    """Ícone da empresa/fundo (fornecido pela brapi). Some sozinho se não carregar."""
    if not url:
        return Markup("")
    return Markup(f'<img class="logo-ativo" src="{url}" width="{tamanho}" height="{tamanho}" alt="" '
                  f'loading="lazy" decoding="async" onerror="this.remove()">')


def ler_guias():
    """Guias em guias/*.md: cabeçalho 'chave: valor' até a primeira linha em branco, depois o texto."""
    guias = []
    for arq in sorted((BASE / "guias").glob("*.md")):
        cab, _, corpo = arq.read_text(encoding="utf-8").partition("\n\n")
        meta = dict(l.split(": ", 1) for l in cab.splitlines() if ": " in l)
        guias.append({"slug": arq.stem, "corpo": corpo, **meta})
    return sorted(guias, key=lambda g: int(g.get("ordem", 99)))


def imagem_compartilhamento(cfg, destino):
    """Imagem 1200x630 usada na prévia de links (WhatsApp, redes sociais)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False

    def fonte(tam, negrito=True):
        for nome in (["DejaVuSans-Bold.ttf"] if negrito else ["DejaVuSans.ttf"]):
            for pasta in ("/usr/share/fonts/truetype/dejavu/", ""):
                try:
                    return ImageFont.truetype(pasta + nome, tam)
                except OSError:
                    pass
        return ImageFont.load_default(size=tam)

    img = Image.new("RGB", (1200, 630), "#0E2A47")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((80, 80, 170, 170), radius=22, fill="#F2B705")
    d.line([(98, 142), (120, 118), (136, 130), (156, 104)], fill="#0E2A47", width=9, joint="curve")
    d.text((80, 230), cfg["nome"], font=fonte(82), fill="#FFFFFF")
    d.text((80, 345), cfg["slogan"], font=fonte(30, False), fill="#C9D6E3")
    d.text((80, 520), "Juros, câmbio, bolsa, FIIs e ações com dados oficiais", font=fonte(30, False), fill="#9FB3C8")
    d.rectangle((0, 610, 1200, 630), fill="#F2B705")
    img.save(destino, "PNG")
    return True


def main():
    exemplo = "--exemplo" in sys.argv
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    cfg["url"] = cfg["url"].rstrip("/")

    if exemplo:
        boletins = [json.loads((BASE / "data" / "exemplo.json").read_text(encoding="utf-8"))]
    else:
        arquivos = sorted((BASE / "data" / "historico").glob("*.json"), reverse=True)
        boletins = [json.loads(a.read_text(encoding="utf-8")) for a in arquivos]
    if not boletins:
        sys.exit("Nenhum boletim encontrado. Rode coletar.py antes (ou use --exemplo).")

    env = Environment(loader=FileSystemLoader(BASE / "templates"), autoescape=select_autoescape())
    env.filters.update(num=num, data_longa=data_longa, data_curta=data_curta, spark=spark, hora=hora, compacto=compacto,
                       milhar=milhar, mes_ano=mes_ano, barras=barras,
                       compacto_sinal=compacto_sinal, logo=logo)
    env.globals.update(manchete=manchete)
    arq_fiis = BASE / "data" / ("fiis_exemplo.json" if exemplo else "fiis.json")
    fundos = json.loads(arq_fiis.read_text(encoding="utf-8"))["fundos"] if arq_fiis.exists() else []
    arq_acoes = BASE / "data" / ("acoes_exemplo.json" if exemplo else "acoes.json")
    acoes = json.loads(arq_acoes.read_text(encoding="utf-8"))["acoes"] if arq_acoes.exists() else []
    arq_fund = BASE / "data" / ("fundamentos_exemplo.json" if exemplo else "fundamentos.json")
    fund = json.loads(arq_fund.read_text(encoding="utf-8"))["empresas"] if arq_fund.exists() else {}
    for a in acoes:  # P/L, P/VP, ROE etc. com o valor de mercado do dia
        a["fund"] = indicadores(fund.get(a["cnpj"]), a.get("valor_mercado"))
    logos = {a["ticker"]: a.get("logo") for a in acoes}
    guias = ler_guias()

    # Taxas do dia usadas pelas calculadoras e pelos guias
    hoje = boletins[0]
    taxa = lambda grupo, nome: ((hoje.get(grupo) or {}).get(nome) or {}).get("valor")
    taxas = {"selic": taxa("juros", "selic"), "cdi": taxa("juros", "cdi"), "ipca": taxa("inflacao", "ipca_12m"),
             "poupanca_mes": taxa("juros", "poupanca"), "data": hoje["data_boletim"]}

    ctx = {"cfg": cfg, "arquivo": [b["data_boletim"] for b in boletins],
           "fiis_destaque": fundos[:6], "tem_fiis": bool(fundos),
           "tem_acoes": bool(acoes), "acoes_set": {a["ticker"] for a in acoes},
           "logos": logos, "guias": guias, "taxas": taxas}

    if SAIDA.exists():
        shutil.rmtree(SAIDA)
    SAIDA.mkdir()
    paginas = []  # para o sitemap

    def pagina(template, caminho, prioridade="0.5", **variaveis):
        """Renderiza um template e grava em site/<caminho>. 'raiz' é calculada pela profundidade."""
        raiz = "../" * caminho.count("/")
        url = cfg["url"] + "/" + caminho.removesuffix("index.html")
        html = (template if hasattr(template, "render") else env.get_template(template)).render(
            raiz=raiz, url_pagina=url, **ctx, **variaveis)
        destino = SAIDA / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(html, encoding="utf-8")
        paginas.append((url, prioridade))

    tpl = env.get_template("boletim.html")
    for i, b in enumerate(boletins):
        b["comentario_html"] = comentario(b["data_boletim"])
        b["focus_tab"] = focus_tabela(b.get("focus"))
        for lista in (b.get("destaques") or {}).values():  # logo nas listas de altas/baixas
            for a in lista:
                a.setdefault("logo", logos.get(a["ticker"]))
        pagina(tpl, f"boletim/{b['data_boletim']}/index.html", "0.3", b=b)
        if i == 0:
            pagina(tpl, "index.html", "1.0", b=b, capa=True)

    for nome in ["arquivo", "sobre", "aviso-legal", "privacidade"]:
        pagina(f"{nome}.html", f"{nome}/index.html", "0.3")

    # Calculadoras e guias
    pagina("calculadoras.html", "calculadoras/index.html", "0.8")
    pagina("aprenda.html", "aprenda/index.html", "0.8")
    tpl_guia = env.get_template("guia.html")
    for g in guias:
        texto = env.from_string(g["corpo"]).render(taxas=taxas, raiz="../../")
        pagina(tpl_guia, f"aprenda/{g['slug']}/index.html", "0.8",
               g=g, corpo_html=Markup(markdown.markdown(texto, extensions=["tables"])))

    # Uma página por FII + ranking
    if fundos:
        tpl_fii = env.get_template("fii.html")
        for f in fundos:
            relacionados = [r for r in fundos if r["segmento"] == f["segmento"] and r["ticker"] != f["ticker"]][:6]
            pagina(tpl_fii, f"fiis/{f['ticker']}/index.html", "0.6", f=f, relacionados=relacionados)
        segmentos = sorted({f["segmento"] for f in fundos})
        pagina("fiis.html", "fiis/index.html", "0.9", fundos=fundos, segmentos=segmentos)

    # Uma página por ação + lista completa
    if acoes:
        tpl_acao = env.get_template("acao.html")
        for a in acoes:
            irmas = [i for i in acoes if i["cnpj"] == a["cnpj"] and i["ticker"] != a["ticker"]]
            vistos = {a["cnpj"]}
            relacionados = []
            for r in acoes:  # maiores empresas do mesmo setor, uma classe por empresa
                if r["setor"] == a["setor"] and r["cnpj"] not in vistos:
                    vistos.add(r["cnpj"])
                    relacionados.append(r)
                if len(relacionados) == 6:
                    break
            pagina(tpl_acao, f"acoes/{a['ticker']}/index.html", "0.6", a=a, irmas=irmas, relacionados=relacionados)
        setores = sorted({a["setor"] for a in acoes})
        pagina("acoes.html", "acoes/index.html", "0.9", fundos=acoes, segmentos=setores)

    # Índice da busca do topo
    indice = [{"t": a["ticker"], "n": a["nome"], "k": "Ação", "u": f"acoes/{a['ticker']}/index.html", "l": a.get("logo")}
              for a in acoes]
    indice += [{"t": f["ticker"], "n": f["nome"], "k": "FII", "u": f"fiis/{f['ticker']}/index.html", "l": f.get("logo")}
               for f in fundos]
    (SAIDA / "busca.json").write_text(json.dumps(indice, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(BASE / "templates" / "busca.js", SAIDA / "busca.js")

    # Google: mapa do site, robots.txt e imagem de compartilhamento
    hoje_iso = date.today().isoformat()
    itens = "".join(f"<url><loc>{u}</loc><lastmod>{hoje_iso}</lastmod><priority>{p}</priority></url>" for u, p in paginas)
    (SAIDA / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>'
                                       f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{itens}</urlset>',
                                       encoding="utf-8")
    (SAIDA / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {cfg['url']}/sitemap.xml\n", encoding="utf-8")
    imagem_compartilhamento(cfg, SAIDA / "compartilhar.png")

    shutil.copy(BASE / "templates" / "style.css", SAIDA / "style.css")
    (SAIDA / ".nojekyll").write_text("")
    print(f"Site gerado em {SAIDA}: {len(paginas)} páginas ({len(fundos)} FIIs, {len(acoes)} ações, {len(guias)} guias).")


if __name__ == "__main__":
    main()
