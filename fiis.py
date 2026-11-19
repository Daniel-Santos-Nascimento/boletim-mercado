"""
Dados de fundos imobiliários (FIIs).

Fontes:
  - CVM, Informe Mensal Estruturado de FII (dados abertos): segmento, gestão,
    administrador, patrimônio, valor patrimonial da cota, cotistas, taxa de
    administração e dividend yield do mês informado pelo administrador.
  - brapi.dev: cotação de todos os fundos listados, numa única consulta.

Uso: chamado por coletar.py. Saída: data/fiis.json
"""
import csv
import io
import json
import os
import zipfile
from pathlib import Path

URL_CVM = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{ano}.zip"


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _csvs(get, ano):
    z = zipfile.ZipFile(io.BytesIO(get(URL_CVM.format(ano=ano)).content))
    saida = {}
    for nome in z.namelist():
        for parte in ("geral", "complemento"):
            if f"_{parte}_" in nome:
                texto = z.read(nome).decode("latin-1")
                saida[parte] = list(csv.DictReader(io.StringIO(texto), delimiter=";"))
    return saida


def _ticker(isin):
    # BRHGLGCTF004 -> HGLG11 (padrão das cotas de FII negociadas em bolsa)
    isin = (isin or "").strip().upper()
    return isin[2:6] + "11" if len(isin) == 12 and isin.startswith("BR") else None


def _dy_fracao(v):
    # O campo vem como fração (0,0085 = 0,85%), mas alguns administradores
    # informam em percentual (0,85). Acima de 5% ao mês, tratamos como percentual.
    if v is None:
        return None
    return v / 100 if v > 0.05 else v


def _precos(get):
    params = {"type": "fund", "sortBy": "volume", "sortOrder": "desc", "limit": 1000}
    if os.getenv("BRAPI_TOKEN"):
        params["token"] = os.getenv("BRAPI_TOKEN")
    lista = get("https://brapi.dev/api/quote/list", params=params).json().get("stocks", [])
    return {a["stock"]: a for a in lista if a.get("close")}


def coletar_fiis(get, hoje, erros, pasta):
    try:
        geral, compl = [], []
        for ano in (hoje.year - 1, hoje.year):
            try:
                d = _csvs(get, ano)
                geral += d.get("geral", [])
                compl += d.get("complemento", [])
            except Exception as e:
                erros.append(f"FIIs CVM {ano}: {e}")
        if not geral or not compl:
            return None
        precos = _precos(get)

        # Cadastro: informe mais recente de cada fundo
        cadastro = {}
        for l in sorted(geral, key=lambda x: (x["Data_Referencia"], int(x.get("Versao") or 0))):
            cadastro[l["CNPJ_Fundo_Classe"]] = l

        # Série mensal: última versão de cada mês
        series = {}
        for l in sorted(compl, key=lambda x: (x["Data_Referencia"], int(x.get("Versao") or 0))):
            series.setdefault(l["CNPJ_Fundo_Classe"], {})[l["Data_Referencia"][:7]] = l

        fundos = []
        for cnpj, cad in cadastro.items():
            ticker = _ticker(cad.get("Codigo_ISIN"))
            if not ticker or ticker not in precos or cnpj not in series:
                continue
            meses = sorted(series[cnpj].items())[-24:]
            hist = []
            for mes, l in meses:
                hist.append({
                    "mes": mes,
                    "dy": _dy_fracao(_num(l.get("Percentual_Dividend_Yield_Mes"))),
                    "vpc": _num(l.get("Valor_Patrimonial_Cotas")),
                })
            ult = meses[-1][1]
            vpc = _num(ult.get("Valor_Patrimonial_Cotas"))
            p = precos[ticker]
            preco = p["close"]
            dys = [h["dy"] for h in hist[-12:] if h["dy"] is not None]
            taxa = _num(ult.get("Percentual_Despesas_Taxa_Administracao"))
            fundos.append({
                "ticker": ticker,
                "nome": (cad.get("Nome_Fundo_Classe") or "").replace(" - FUNDO DE INVESTIMENTO IMOBILIÁRIO", "").replace("FUNDO DE INVESTIMENTO IMOBILIÁRIO", "FII").strip(" -").title().replace("Fii", "FII"),
                "cnpj": cnpj,
                "segmento": (cad.get("Segmento_Atuacao") or "").strip() or "Não informado",
                "mandato": (cad.get("Mandato") or "").strip(),
                "gestao": (cad.get("Tipo_Gestao") or "").strip(),
                "administrador": (cad.get("Nome_Administrador") or "").strip().title(),
                "publico": (cad.get("Publico_Alvo") or "").strip().capitalize(),
                "preco": preco,
                "var_pct": round(p.get("change") or 0, 2),
                "financeiro": preco * (p.get("volume") or 0),
                "vpc": vpc,
                "pvp": round(preco / vpc, 2) if vpc else None,
                "pl": _num(ult.get("Patrimonio_Liquido")),
                "cotistas": int(_num(ult.get("Total_Numero_Cotistas")) or 0) or None,
                "taxa_adm_mes": taxa,
                "dy_mes": dys[-1] * 100 if dys else None,
                "dy_12m": round(sum(dys) * 100, 2) if len(dys) >= 6 else None,
                "meses_dy": len(dys),
                "ref": meses[-1][0],
                "hist": hist,
            })
        fundos.sort(key=lambda f: f["financeiro"], reverse=True)
        Path(pasta).mkdir(parents=True, exist_ok=True)
        (Path(pasta) / "fiis.json").write_text(
            json.dumps({"atualizado": hoje.isoformat(), "fundos": fundos}, ensure_ascii=False), encoding="utf-8")
        return len(fundos)
    except Exception as e:
        erros.append(f"FIIs: {e}")
        return None
