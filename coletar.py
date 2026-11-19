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
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).parent
FUSO_BR = timezone(timedelta(hours=-3))
HOJE = datetime.now(FUSO_BR).date()
TIMEOUT = 40
HEADERS = {"User-Agent": "boletim-mercado/1.0"}
erros = []


def get(url, **kw):
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, **kw)
    r.raise_for_status()
    return r


def br_para_iso(d):  # "19/11/2026" -> "2026-11-19"
    return datetime.strptime(d, "%d/%m/%Y").date().isoformat()


# ---------------------------------------------------------------- Banco Central
def sgs(codigo, n=2):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json"
    pontos = get(url).json()
    return [{"data": br_para_iso(p["data"]), "valor": float(p["valor"])} for p in pontos]


def serie(nome, codigo, com_variacao=False):
    try:
        pts = sgs(codigo, 2)
        atual = pts[-1]
        item = {"valor": atual["valor"], "data": min(atual["data"], HOJE.isoformat())}
        if com_variacao and len(pts) == 2 and pts[0]["valor"]:
            item["var_pct"] = round((atual["valor"] / pts[0]["valor"] - 1) * 100, 2)
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
        r = get(f"https://brapi.dev/api/quote/{ticker}", params={"token": token}).json()
        q = r["results"][0]
        return {
            "valor": q["regularMarketPrice"],
            "var_pct": round(q.get("regularMarketChangePercent") or 0, 2),
            "data": (q.get("regularMarketTime") or "")[:10],
        }
    except Exception as e:
        erros.append(f"{nome}: {e}")
        return None


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


# ---------------------------------------------------------------- Principal
def main():
    dados = {
        "data_boletim": HOJE.isoformat(),
        "gerado_em": datetime.now(FUSO_BR).isoformat(timespec="minutes"),
        "exemplo": False,
        "juros": {"selic": serie("Selic", 432), "cdi": serie("CDI", 4389)},
        "inflacao": {"ipca_mes": serie("IPCA mês", 433), "ipca_12m": serie("IPCA 12m", 13522)},
        "cambio": {"dolar": serie("Dólar", 1, True), "euro": serie("Euro", 21619, True)},
        "bolsa": {"ibov": brapi("^BVSP", "Ibovespa"), "ifix": brapi("IFIX", "IFIX")},
        "focus": focus(),
        "fatos_relevantes": fatos_relevantes(),
    }
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = Path(__file__).parent
FUSO_BR = timezone(timedelta(hours=-3))
HOJE = datetime.now(FUSO_BR).date()
TIMEOUT = 40
HEADERS = {"User-Agent": "boletim-mercado/1.0"}
erros = []


def get(url, **kw):
    r = requests.get(url, timeout=TIMEOUT, headers=HEADERS, **kw)
    r.raise_for_status()
    return r


def br_para_iso(d):  # "19/11/2026" -> "2026-11-19"
    return datetime.strptime(d, "%d/%m/%Y").date().isoformat()


# ---------------------------------------------------------------- Banco Central
def sgs(codigo, n=2):
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{n}?formato=json"
    pontos = get(url).json()
    return [{"data": br_para_iso(p["data"]), "valor": float(p["valor"])} for p in pontos]


def serie(nome, codigo, com_variacao=False):
    try:
        pts = sgs(codigo, 2)
        atual = pts[-1]
        item = {"valor": atual["valor"], "data": atual["data"]}
        if com_variacao and len(pts) == 2 and pts[0]["valor"]:
            item["var_pct"] = round((atual["valor"] / pts[0]["valor"] - 1) * 100, 2)
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
            params = {
                "$filter": f"Indicador eq '{indicador}' and baseCalculo eq 0",
                "$orderby": "Data desc",
                "$top": "120",
                "$format": "json",
                "$select": "Indicador,Data,DataReferencia,Mediana",
            }
            vals = get(url, params=params).json()["value"]
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
        r = get(f"https://brapi.dev/api/quote/{ticker}", params={"token": token}).json()
        q = r["results"][0]
        return {
            "valor": q["regularMarketPrice"],
            "var_pct": round(q.get("regularMarketChangePercent") or 0, 2),
            "data": (q.get("regularMarketTime") or "")[:10],
        }
    except Exception as e:
        erros.append(f"{nome}: {e}")
        return None


# ---------------------------------------------------------------- CVM
def fatos_relevantes():
    """Fatos relevantes entregues no último dia útil (e hoje, se já houver)."""
    try:
        url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{HOJE.year}.zip"
        z = zipfile.ZipFile(io.BytesIO(get(url).content))
        nome = next(n for n in z.namelist() if n.endswith(".csv"))
        texto = z.read(nome).decode("latin-1")
        corte = HOJE - timedelta(days=3 if HOJE.weekday() == 0 else 1)
        itens = []
        for l in csv.DictReader(io.StringIO(texto), delimiter=";"):
            if l.get("Categoria") != "Fato Relevante":
                continue
            entrega = l.get("Data_Entrega", "")[:10]
            if entrega and entrega >= corte.isoformat():
                itens.append({
                    "empresa": l.get("Nome_Companhia", "").strip().title(),
                    "assunto": (l.get("Assunto") or "").strip(),
                    "data": entrega,
                    "link": l.get("Link_Download", ""),
                })
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


# ---------------------------------------------------------------- Principal
def main():
    dados = {
        "data_boletim": HOJE.isoformat(),
        "gerado_em": datetime.now(FUSO_BR).isoformat(timespec="minutes"),
        "exemplo": False,
        "juros": {"selic": serie("Selic", 432), "cdi": serie("CDI", 4389)},
        "inflacao": {"ipca_mes": serie("IPCA mês", 433), "ipca_12m": serie("IPCA 12m", 13522)},
        "cambio": {"dolar": serie("Dólar", 1, True), "euro": serie("Euro", 21619, True)},
        "bolsa": {"ibov": brapi("^BVSP", "Ibovespa"), "ifix": brapi("IFIX", "IFIX")},
        "focus": focus(),
        "fatos_relevantes": fatos_relevantes(),
    }
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
