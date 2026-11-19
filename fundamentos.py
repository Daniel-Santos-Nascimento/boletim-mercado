"""
Indicadores fundamentalistas a partir dos balanços entregues à CVM.

Fontes (dados abertos da CVM, demonstrações consolidadas):
  - DFP: demonstrações anuais
  - ITR: informações trimestrais (acumulado do ano e mesmo período do ano anterior)

Para cada empresa calcula, nos últimos 12 meses disponíveis:
  receita, lucro líquido dos controladores, patrimônio líquido dos controladores,
  dívida bruta e caixa (exceto instituições financeiras), mais o histórico anual.

Como os arquivos são grandes, o cálculo roda uma vez por dia; o resultado fica em
data/fundamentos.json (pequeno, salvo no repositório).
"""
import csv
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path

URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{tipo}/DADOS/{tipo_min}_cia_aberta_{ano}.zip"
RE_LUCRO_CONTROL = re.compile(r"atribu[ií]d[oa]s? a s[óo]cios da (empresa )?controladora", re.I)
RE_LUCRO_PERIODO = re.compile(r"lucro.*(consolidado|l[ií]quido).*per[ií]odo", re.I)


def _num(v, escala):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x * 1000 if (escala or "").upper() == "MIL" else x


def _nivel(cd):
    return cd.count(".")


def _ler(get, tipo, ano, partes):
    """Lê do zip só as linhas de 'ÚLTIMO'/'PENÚLTIMO' das tabelas pedidas."""
    url = URL.format(tipo=tipo, tipo_min=tipo.lower(), ano=ano)
    z = zipfile.ZipFile(io.BytesIO(get(url).content))
    saida = {}
    for parte in partes:
        nome = next((n for n in z.namelist() if f"_{parte}_" in n), None)
        if not nome:
            continue
        linhas = []
        with z.open(nome) as f:
            for l in csv.DictReader(io.TextIOWrapper(f, encoding="latin-1"), delimiter=";"):
                cd = l["CD_CONTA"]
                if _nivel(cd) > 2:
                    continue
                linhas.append({
                    "cnpj": l["CNPJ_CIA"], "ref": l["DT_REFER"], "versao": int(l.get("VERSAO") or 0),
                    "ordem": l["ORDEM_EXERC"], "ini": l.get("DT_INI_EXERC") or "", "fim": l["DT_FIM_EXERC"],
                    "cd": cd, "ds": l["DS_CONTA"], "vl": _num(l["VL_CONTA"], l["ESCALA_MOEDA"]),
                })
        saida[parte] = linhas
    return saida


def _ultima_versao(linhas):
    """Mantém, para cada (empresa, data de referência), só a versão mais recente do documento."""
    maior = {}
    for l in linhas:
        k = (l["cnpj"], l["ref"])
        maior[k] = max(maior.get(k, 0), l["versao"])
    return [l for l in linhas if l["versao"] == maior[(l["cnpj"], l["ref"])]]


def _dre(linhas):
    """Receita e lucro dos controladores de um conjunto de linhas de DRE do mesmo período."""
    receita = next((l["vl"] for l in linhas if l["cd"] == "3.01"), None)
    lucro = next((l["vl"] for l in linhas if _nivel(l["cd"]) == 2 and l["cd"].startswith("3.")
                  and RE_LUCRO_CONTROL.search(l["ds"])), None)
    if lucro is None:
        lucro = next((l["vl"] for l in linhas if _nivel(l["cd"]) == 1 and RE_LUCRO_PERIODO.search(l["ds"])), None)
    financeira = any(l["cd"] == "3.01" and "intermedia" in l["ds"].lower() for l in linhas)
    return receita, lucro, financeira


def _balanco(bpa, bpp, financeira):
    pl = next((l for l in bpp if _nivel(l["cd"]) == 1 and l["ds"].lower().startswith("patrimônio líquido")), None)
    if not pl:
        return {}
    minoritarios = next((l["vl"] for l in bpp if l["cd"].startswith(pl["cd"] + ".")
                         and "não controladores" in l["ds"].lower()), 0) or 0
    saida = {"pl": (pl["vl"] or 0) - minoritarios}
    if not financeira:
        divida = sum(l["vl"] or 0 for l in bpp if _nivel(l["cd"]) == 2 and l["cd"][:4] in ("2.01", "2.02")
                     and l["ds"].lower().startswith("empréstimos e financiamentos"))
        caixa = sum(l["vl"] or 0 for l in bpa if _nivel(l["cd"]) == 2 and l["cd"].startswith("1.01.")
                    and l["ds"].lower().startswith(("caixa e equivalentes", "aplicações financeiras")))
        saida.update({"divida": divida, "caixa": caixa})
    return saida


def _agrupar(linhas):
    g = {}
    for l in linhas:
        g.setdefault(l["cnpj"], []).append(l)
    return g


def coletar_fundamentos(get, hoje, erros, pasta, forcar=False):
    destino = Path(pasta) / "fundamentos.json"
    if destino.exists() and not forcar:
        try:
            if json.loads(destino.read_text(encoding="utf-8")).get("atualizado") == hoje.isoformat():
                return "em cache"
        except Exception:
            pass
    try:
        ano = hoje.year
        dfp, itr = {}, {}
        for a in (ano - 1, ano - 2):
            try:
                d = _ler(get, "DFP", a, ("DRE_con", "BPA_con", "BPP_con"))
                for k, v in d.items():
                    dfp.setdefault(k, []).extend(v)
            except Exception as e:
                erros.append(f"Fundamentos DFP {a}: {e}")
        for a in (ano, ano - 1):
            try:
                d = _ler(get, "ITR", a, ("DRE_con", "BPA_con", "BPP_con"))
                for k, v in d.items():
                    itr.setdefault(k, []).extend(v)
            except Exception as e:
                erros.append(f"Fundamentos ITR {a}: {e}")
        if not dfp.get("DRE_con"):
            return None

        dfp = {k: _agrupar(_ultima_versao(v)) for k, v in dfp.items()}
        itr = {k: _agrupar(_ultima_versao(v)) for k, v in itr.items()}

        empresas = {}
        for cnpj, linhas in dfp["DRE_con"].items():
            # Histórico anual: exercícios completos (1/jan a 31/dez) de qualquer DFP
            anual = {}
            for l in linhas:
                if l["ini"][5:] == "01-01" and l["fim"][5:] == "12-31" and l["ini"][:4] == l["fim"][:4]:
                    anual.setdefault(l["fim"][:4], []).append(l)
            anos = {}
            financeira = False
            for a, ls in anual.items():
                r, lu, fin = _dre(ls)
                financeira = financeira or fin
                if lu is not None:
                    anos[a] = {"receita": r, "lucro": lu}
            if not anos:
                continue
            ultimo_ano = max(anos)

            # Últimos 12 meses: anual + acumulado do ano corrente - mesmo período do ano anterior
            ttm = dict(anos[ultimo_ano], ate=f"{ultimo_ano}-12-31", base="anual")
            trim = itr.get("DRE_con", {}).get(cnpj, [])
            refs = sorted({l["ref"] for l in trim if l["ref"][:4] == str(int(ultimo_ano) + 1)})
            if refs:
                ref = refs[-1]
                atual = [l for l in trim if l["ref"] == ref and l["ordem"] == "ÚLTIMO" and l["ini"][5:] == "01-01"]
                anterior = [l for l in trim if l["ref"] == ref and l["ordem"] == "PENÚLTIMO" and l["ini"][5:] == "01-01"]
                ra, la, _ = _dre(atual)
                rp, lp, _ = _dre(anterior)
                base = anos[ultimo_ano]
                if None not in (la, lp, base["lucro"]):
                    ttm = {"receita": None if None in (ra, rp, base["receita"]) else base["receita"] + ra - rp,
                           "lucro": base["lucro"] + la - lp, "ate": ref, "base": "12 meses"}

            # Balanço mais recente (ITR do ano corrente, se houver; senão o anual)
            bpa_t, bpp_t = itr.get("BPA_con", {}).get(cnpj, []), itr.get("BPP_con", {}).get(cnpj, [])
            ref_bal = max([l["ref"] for l in bpp_t if l["ordem"] == "ÚLTIMO"], default=None)
            if ref_bal and ref_bal >= ttm["ate"]:
                bpa = [l for l in bpa_t if l["ref"] == ref_bal and l["ordem"] == "ÚLTIMO"]
                bpp = [l for l in bpp_t if l["ref"] == ref_bal and l["ordem"] == "ÚLTIMO"]
            else:
                ref_bal = f"{ultimo_ano}-12-31"
                bpa = [l for l in dfp.get("BPA_con", {}).get(cnpj, []) if l["fim"] == ref_bal and l["ordem"] == "ÚLTIMO"]
                bpp = [l for l in dfp.get("BPP_con", {}).get(cnpj, []) if l["fim"] == ref_bal and l["ordem"] == "ÚLTIMO"]
            bal = _balanco(bpa, bpp, financeira)

            empresas[cnpj] = {
                "financeira": financeira,
                "ttm": ttm,
                "balanco": dict(bal, data=ref_bal),
                "anos": [dict(v, ano=a) for a, v in sorted(anos.items())][-4:],
            }

        Path(pasta).mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps({"atualizado": hoje.isoformat(), "empresas": empresas},
                                      ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return len(empresas)
    except Exception as e:
        erros.append(f"Fundamentos: {e}")
        return None


def indicadores(f, valor_mercado):
    """Indicadores com o valor de mercado do dia. Retorna {} se não houver dados."""
    if not f:
        return {}
    lucro, receita = f["ttm"].get("lucro"), f["ttm"].get("receita")
    pl = f["balanco"].get("pl")
    out = {"lucro_12m": lucro, "receita_12m": receita, "pl": pl, "ate": f["ttm"]["ate"],
           "base": f["ttm"]["base"], "financeira": f["financeira"], "anos": f["anos"],
           "data_balanco": f["balanco"].get("data")}
    if valor_mercado and lucro and lucro > 0:
        out["pl_ratio"] = round(valor_mercado / lucro, 2)
    if valor_mercado and pl and pl > 0:
        out["pvp"] = round(valor_mercado / pl, 2)
    if lucro is not None and pl and pl > 0:
        out["roe"] = round(lucro / pl * 100, 2)
    if lucro is not None and receita and receita > 0 and not f["financeira"]:
        out["margem"] = round(lucro / receita * 100, 2)
    if not f["financeira"] and pl and pl > 0 and "divida" in f["balanco"]:
        out["div_liq"] = f["balanco"]["divida"] - f["balanco"]["caixa"]
        out["div_liq_pl"] = round(out["div_liq"] / pl, 2)
    return out
