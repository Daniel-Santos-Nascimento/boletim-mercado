"""
Coleta diária dos dados do boletim.

Fontes (todas oficiais ou baseadas em dados públicos):
  - Banco Central (SGS): Selic, CDI, IPCA, dólar e euro PTAX
  - Banco Central (Olinda): Boletim Focus
  - brapi.dev: Ibovespa e IFIX (precisa de token gratuito em BRAPI_TOKEN)
  - CVM (dados abertos): fatos relevantes entregues pelas companhias

Uso:  python coletar.py
Saída: data/latest.json  e  data/historico/AAAA-MM-DD.json

Se uma fonte falhar, o boletim sai assim mesmo, sem aquele bloco,
e o erro fica registrado em "erros" no JSON.
"""
import csv
import io
import json
import os
import zipfile
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).parent
FUSO_BR = timezone(timedelta(hours=-3))
HOJE = datetime.now(FUSO_BR).date()
TIMEOUT = 40
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; boletim-mercado/1.0; +https://daniel-santos-nascimento.github.io/boletim-mercado)"}
erros = []


def get(url, **kw):
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, **kw)
    r.raise_for_status()
    return r


def br_para_iso(d):  # "19/11/2026" -> "2026-11-19"
    return datetime.strptime(d, "%d/%m/%Y").date().isoformat()


# ---------------------------------------------------------------- Banco Central
def sgs(codigo, dias):
    """Série do SGS por período (a API limita a consulta "últimos N" a 20 valores)."""
    ini = (HOJE - timedelta(days=dias)).strftime("%d/%m/%Y")
    fim = (HOJE + timedelta(days=60)).strftime("%d/%m/%Y")
    url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
           f"?formato=json&dataInicial={ini}&dataFinal={fim}")
    pontos = get(url).json()
    return [{"data": br_para_iso(p["data"]), "valor": float(p["valor"])} for p in pontos]


def serie(nome, codigo, dias=15, com_variacao=False, grafico=False, passo=1):
    try:
        pts = sgs(codigo, dias)
        atual = pts[-1]
        item = {"valor": atual["valor"], "data": min(atual["data"], HOJE.isoformat())}
        if com_variacao and len(pts) >= 2 and pts[-2]["valor"]:
            item["var_pct"] = round((atual["valor"] / pts[-2]["valor"] - 1) * 100, 2)
        if grafico:
            item["hist"] = [p["valor"] for p in pts[::-1][::passo][::-1]]
        return item
    except Exception as e:
        erros.append(f"{nome}: {e}")
        return None


def focus():
    """Medianas do Focus para o ano corrente e o próximo, com a da semana anterior."""
    url = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais"
    anos = [str(HOJE.year), str(HOJE.year + 1)]
    linhas, data_ref = [], None
    for indicador, rotulo in [("IPCA", "IPCA (%)"), ("Selic", "Selic (% a.a.)"),
                              ("Câmbio", "Dólar (R$)"), ("PIB Total", "PIB (%)")]:
        try:
            # O Olinda não aceita "+" no lugar de espaço: a URL é montada com %20
            consulta = (
                "?$filter=" + quote(f"Indicador eq '{indicador}' and baseCalculo eq 0")
                + "&$orderby=" + quote("Data desc")
                + "&$top=120&$format=json&$select=Indicador,Data,DataReferencia,Mediana"
            )
            vals = get(url + consulta).json()["value"]
            datas = sorted({v["Data"] for v in vals}, reverse=True)
            if not datas:
                continue
            ultima = datas[0]
            data_ref = data_ref or ultima
            alvo = (date.fromisoformat(ultima) - timedelta(days=7)).isoformat()
            anterior = next((d for d in datas if d <= alvo), None)
            for ano in anos:
                atual = next((v["Mediana"] for v in vals if v["Data"] == ultima and v["DataReferencia"] == ano), None)
                ant = next((v["Mediana"] for v in vals if v["Data"] == anterior and v["DataReferencia"] == ano), None)
                if atual is not None:
                    linhas.append({"indicador": rotulo, "ano": ano, "mediana": atual, "semana_anterior": ant})
        except Exception as e:
            erros.append(f"Focus {indicador}: {e}")
    return {"data": data_ref, "linhas": linhas} if linhas else None


# ---------------------------------------------------------------- Bolsa (brapi)
def brapi(ticker, nome):
    token = os.getenv("BRAPI_TOKEN", "")
    if not token:
        erros.append(f"{nome}: defina o secret BRAPI_TOKEN no GitHub")
        return None
    try:
        url = f"https://brapi.dev/api/quote/{ticker}"
        try:  # com histórico de 1 mês para o minigráfico
            r = get(url, params={"token": token, "range": "1mo", "interval": "1d"}).json()
        except Exception:  # se o plano não permitir histórico, pega só a cotação
            r = get(url, params={"token": token}).json()
        q = r["results"][0]
        item = {
            "valor": q["regularMarketPrice"],
            "var_pct": round(q.get("regularMarketChangePercent") or 0, 2),
            "data": (q.get("regularMarketTime") or "")[:10],
        }
        hist = [h.get("close") for h in (q.get("historicalDataPrice") or []) if h.get("close")]
        if len(hist) >= 2:
            item["hist"] = hist
        return item
    except Exception as e:
        erros.append(f"{nome}: {e}")
        return None


def limpar_nome(nome):
    """'CIA BRASILEIRA DE DISTRIBUICAO S/A' -> 'Cia Brasileira de Distribuicao'"""
    ligacoes = {"de", "da", "do", "das", "dos", "e"}
    fora = {"s.a.", "s.a", "s/a", "sa", "-"}
    palavras = [p for p in (nome or "").split() if p.lower() not in fora]
    return " ".join(p.lower() if p.lower() in ligacoes and i else p.capitalize()
                    for i, p in enumerate(palavras)).strip(" -.")


def destaques_bolsa():
    """Maiores altas, baixas e mais negociadas entre as 150 ações de maior volume."""
    try:
        params = {"sortBy": "volume", "sortOrder": "desc", "limit": 150, "type": "stock"}
        if os.getenv("BRAPI_TOKEN"):
            params["token"] = os.getenv("BRAPI_TOKEN")
        lista = get("https://brapi.dev/api/quote/list", params=params).json().get("stocks", [])
        acoes = []
        for a in lista:
            if a.get("close") is None or a.get("change") is None or (a.get("close") or 0) < 1:
                continue
            acoes.append({
                "ticker": a["stock"],
                "nome": limpar_nome(a.get("name")),
                "preco": a["close"],
                "var_pct": round(a["change"], 2),
                "financeiro": a["close"] * (a.get("volume") or 0),
            })
        if not acoes:
            return None
        return {
            "altas": sorted(acoes, key=lambda x: x["var_pct"], reverse=True)[:5],
            "baixas": sorted(acoes, key=lambda x: x["var_pct"])[:5],
            "negociadas": sorted(acoes, key=lambda x: x["financeiro"], reverse=True)[:5],
        }
    except Exception as e:
        erros.append(f"Destaques da bolsa: {e}")
        return None


def cripto():
    try:
        ids = {"bitcoin": "Bitcoin", "ethereum": "Ethereum", "solana": "Solana"}
        r = get("https://api.coingecko.com/api/v3/simple/price",
                params={"ids": ",".join(ids), "vs_currencies": "brl", "include_24hr_change": "true"}).json()
        return [{"nome": nome, "valor": r[i]["brl"], "var_pct": round(r[i].get("brl_24h_change") or 0, 2)}
                for i, nome in ids.items() if i in r]
    except Exception as e:
        erros.append(f"Cripto: {e}")
        return []


# ---------------------------------------------------------------- CVM
def fatos_relevantes():
    """Fatos relevantes entregues no último dia útil (e hoje, se já houver)."""
    try:
        url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{HOJE.year}.zip"
        z = zipfile.ZipFile(io.BytesIO(get(url).content))
        nome = next(n for n in z.namelist() if n.endswith(".csv"))
        texto = z.read(nome).decode("latin-1")
        todos = []
        for l in csv.DictReader(io.StringIO(texto), delimiter=";"):
            if l.get("Categoria") != "Fato Relevante":
                continue
            entrega = l.get("Data_Entrega", "")[:10]
            if entrega and entrega <= HOJE.isoformat():
                todos.append({
                    "empresa": l.get("Nome_Companhia", "").strip().title(),
                    "assunto": (l.get("Assunto") or "").strip(),
                    "data": entrega,
                    "link": l.get("Link_Download", ""),
                })
        if not todos:
            erros.append("CVM: nenhum fato relevante encontrado no arquivo do ano")
            return []
        # A CVM atualiza o arquivo com algum atraso: pega as 2 datas de entrega mais recentes
        datas = sorted({i["data"] for i in todos}, reverse=True)[:2]
        itens = [i for i in todos if i["data"] in datas]
        vistos, unicos = set(), []
        for i in sorted(itens, key=lambda x: (x["data"], x["empresa"]), reverse=True):
            chave = (i["empresa"], i["assunto"])
            if chave not in vistos:
                vistos.add(chave)
                unicos.append(i)
        return unicos[:25]
    except Exception as e:
        erros.append(f"CVM fatos relevantes: {e}")
        return []


# ---------------------------------------------------------------- Notícias (RSS)
# (fonte, feed, seções aceitas no link — None aceita tudo)
SECOES_INFOMONEY = ("/mercados/", "/economia/", "/onde-investir/", "/negocios/", "/minhas-financas/")
FONTES_MANCHETES = [
    ("InfoMoney", "https://www.infomoney.com.br/feed/", SECOES_INFOMONEY),
    ("Money Times", "https://www.moneytimes.com.br/mercados/feed/", None),
    ("Seu Dinheiro", "https://www.seudinheiro.com/bolsa-dolar/feed/", None),
    ("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml", None),
]
FONTES_OFICIAIS = [
    ("Banco Central", "https://www.bcb.gov.br/api/feed/sitebcb/sitefeeds/notasImprensa", None),
    ("CVM", f"https://www.gov.br/cvm/pt-br/assuntos/noticias/{HOJE.year}/RSS", None),
]


def _data_feed(texto):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        d = parsedate_to_datetime(texto)          # RSS 2.0: "Thu, 19 Nov 2026 10:00:00 -0300"
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(texto.replace("Z", "+00:00"))  # Atom / RDF
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=FUSO_BR)
    return d.astimezone(FUSO_BR)


def ler_feed(fonte, url, limite=8, secoes=None):
    """Lê RSS 2.0, RSS 1.0 (RDF) ou Atom e devolve título, link, fonte e horário."""
    raiz = ET.fromstring(get(url).content)
    itens = []
    for el in raiz.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        filhos = {c.tag.split("}")[-1]: c for c in el}
        titulo = (filhos.get("title").text or "").strip() if "title" in filhos else ""
        link = ""
        if "link" in filhos:
            link = (filhos["link"].text or "").strip() or filhos["link"].get("href", "")
        quando = None
        for campo in ("pubDate", "date", "published", "updated"):
            if campo in filhos:
                quando = _data_feed(filhos[campo].text)
                if quando:
                    break
        if secoes and not any(sec in link for sec in secoes):
            continue
        if titulo and link and not titulo.isdigit():  # o feed da CVM inclui pastas como "2026"
            itens.append({"titulo": " ".join(titulo.split()), "link": link, "fonte": fonte,
                          "quando": quando.isoformat(timespec="minutes") if quando else None})
    itens.sort(key=lambda n: n["quando"] or "", reverse=True)
    return itens[:limite]


def noticias(fontes, total, por_fonte, horas):
    agora = datetime.now(FUSO_BR)
    todas = []
    vistos = set()
    for fonte, url, secoes in fontes:
        try:
            itens = ler_feed(fonte, url, 30, secoes)
        except Exception as e:
            erros.append(f"Notícias {fonte}: {e}")
            continue
        da_fonte = 0
        for n in itens:  # mais recentes primeiro; no máximo "por_fonte" de cada veículo
            if not n["quando"] or agora - datetime.fromisoformat(n["quando"]) > timedelta(hours=horas):
                continue
            chave = "".join(ch for ch in n["titulo"].lower() if ch.isalnum())[:60]
            if chave in vistos:  # mesma notícia publicada por dois veículos
                continue
            vistos.add(chave)
            todas.append(n)
            da_fonte += 1
            if da_fonte >= por_fonte:
                break
    todas.sort(key=lambda n: n["quando"], reverse=True)
    return todas[:total]


# ---------------------------------------------------------------- Principal
def main():
    dados = {
        "data_boletim": HOJE.isoformat(),
        "gerado_em": datetime.now(FUSO_BR).isoformat(timespec="minutes"),
        "exemplo": False,
        "juros": {"selic": serie("Selic", 432, 365, grafico=True, passo=7),
                  "cdi": serie("CDI", 4389, 365, grafico=True, passo=7)},
        "inflacao": {"ipca_mes": serie("IPCA mês", 433, 100),
                     "ipca_12m": serie("IPCA 12m", 13522, 760, grafico=True)},
        "cambio": {"dolar": serie("Dólar", 1, 45, com_variacao=True, grafico=True),
                   "euro": serie("Euro", 21619, 45, com_variacao=True, grafico=True)},
        "bolsa": {"ibov": brapi("^BVSP", "Ibovespa"), "ifix": brapi("IFIX", "IFIX")},
        "focus": focus(),
        "destaques": destaques_bolsa(),
        "cripto": cripto(),
        "fatos_relevantes": fatos_relevantes(),
        "manchetes": noticias(FONTES_MANCHETES, total=12, por_fonte=3, horas=36),
        "oficiais": noticias(FONTES_OFICIAIS, total=6, por_fonte=3, horas=24 * 30),
    }
    from fiis import coletar_fiis  # páginas de FIIs: grava data/fiis.json
    dados["fiis_total"] = coletar_fiis(get, HOJE, erros, BASE / "data")
    from acoes import coletar_acoes  # páginas de ações: grava data/acoes.json
    dados["acoes_total"] = coletar_acoes(get, HOJE, erros, BASE / "data")
    dados["erros"] = erros

    (BASE / "data" / "historico").mkdir(parents=True, exist_ok=True)
    js = json.dumps(dados, ensure_ascii=False, indent=2)
    (BASE / "data" / "latest.json").write_text(js, encoding="utf-8")
    (BASE / "data" / "historico" / f"{HOJE.isoformat()}.json").write_text(js, encoding="utf-8")
    print(f"Boletim {HOJE} coletado. Erros: {len(erros)}")
    for e in erros:
        print("  -", e)


if __name__ == "__main__":
    main()
