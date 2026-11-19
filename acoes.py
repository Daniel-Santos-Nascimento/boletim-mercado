"""
Dados de ações listadas na B3.

Fontes:
  - brapi.dev: cotação, variação, volume e valor de mercado (uma consulta).
  - CVM, Formulário Cadastral (FCA): liga o código de negociação ao CNPJ e traz
    setor, descrição da atividade, site, controle acionário e segmento de listagem.
  - CVM, documentos IPE: comunicados recentes de cada empresa (fatos relevantes,
    avisos aos acionistas, relatórios de proventos, comunicados ao mercado).

Uso: chamado por coletar.py. Saída: data/acoes.json
"""
import csv
import io
import json
import os
import zipfile
from pathlib import Path

URL_FCA = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip"
URL_IPE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
CATEGORIAS = {"Fato Relevante", "Comunicado ao Mercado", "Aviso aos Acionistas",
              "Relatório Proventos", "Calendário de Eventos Corporativos"}
LIGACOES = {"de", "da", "do", "das", "dos", "e"}


def _ler_zip(get, url):
    z = zipfile.ZipFile(io.BytesIO(get(url).content))
    return {n: list(csv.DictReader(io.StringIO(z.read(n).decode("latin-1")), delimiter=";"))
            for n in z.namelist() if n.endswith(".csv")}


def _arquivo(tabelas, parte):
    return next((linhas for nome, linhas in tabelas.items() if f"_{parte}_" in nome), [])


def _titulo(texto):
    palavras = [p for p in (texto or "").split() if p.lower() not in {"s.a.", "s.a", "s/a", "sa"}]
    return " ".join(p.lower() if p.lower() in LIGACOES and i else p.capitalize()
                    for i, p in enumerate(palavras)).strip(" -.")


def _mais_recente(linhas, chave):
    saida = {}
    for l in sorted(linhas, key=lambda x: (x.get("Data_Referencia", ""), int(x.get("Versao") or 0))):
        saida[l[chave]] = l
    return saida


def coletar_acoes(get, hoje, erros, pasta):
    try:
        params = {"type": "stock", "sortBy": "volume", "sortOrder": "desc", "limit": 1000}
        if os.getenv("BRAPI_TOKEN"):
            params["token"] = os.getenv("BRAPI_TOKEN")
        cotacoes = {a["stock"]: a for a in get("https://brapi.dev/api/quote/list", params=params).json().get("stocks", [])
                    if a.get("close")}

        fca = {}
        for ano in (hoje.year, hoje.year - 1):  # o FCA do ano pode ainda não ter todas as empresas
            try:
                t = _ler_zip(get, URL_FCA.format(ano=ano))
                for parte in ("valor_mobiliario", "geral"):
                    fca.setdefault(parte, [])
                    fca[parte] = _arquivo(t, parte) + fca[parte]
            except Exception as e:
                erros.append(f"Ações FCA {ano}: {e}")
        if not fca.get("valor_mobiliario"):
            return None

        # código de negociação -> linha do FCA (só os que ainda negociam)
        papeis = {}
        for l in sorted(fca["valor_mobiliario"], key=lambda x: (x.get("Data_Referencia", ""), int(x.get("Versao") or 0))):
            cod = (l.get("Codigo_Negociacao") or "").strip().upper()
            if cod and not (l.get("Data_Fim_Negociacao") or "").strip():
                papeis[cod] = l
        empresas = _mais_recente(fca.get("geral", []), "CNPJ_Companhia")

        comunicados = {}
        try:
            for linhas in _ler_zip(get, URL_IPE.format(ano=hoje.year)).values():
                for l in linhas:
                    if l.get("Categoria") in CATEGORIAS:
                        comunicados.setdefault(l["CNPJ_Companhia"], []).append({
                            "data": (l.get("Data_Entrega") or "")[:10],
                            "categoria": l["Categoria"],
                            "assunto": (l.get("Assunto") or "").strip() or l["Categoria"],
                            "link": l.get("Link_Download", ""),
                        })
        except Exception as e:
            erros.append(f"Ações IPE: {e}")

        acoes = []
        for cod, p in cotacoes.items():
            vm = papeis.get(cod)
            if not vm:
                continue
            cnpj = vm["CNPJ_Companhia"]
            emp = empresas.get(cnpj, {})
            docs = sorted(comunicados.get(cnpj, []), key=lambda d: d["data"], reverse=True)
            vistos, recentes = set(), []
            for d in docs:
                if (d["data"], d["assunto"]) not in vistos:
                    vistos.add((d["data"], d["assunto"]))
                    recentes.append(d)
            acoes.append({
                "ticker": cod,
                "nome": _titulo(emp.get("Nome_Empresarial") or vm.get("Nome_Empresarial") or p.get("name")),
                "cnpj": cnpj,
                "classe": (vm.get("Valor_Mobiliario") or "").strip(),
                "listagem": (vm.get("Segmento") or "").strip(),
                "setor": (emp.get("Setor_Atividade") or "").strip() or "Não informado",
                "subsetor": p.get("subsector") or "",
                "atividade": " ".join((emp.get("Descricao_Atividade") or "").split()),
                "site": (emp.get("Pagina_Web") or "").strip(),
                "controle": (emp.get("Especie_Controle_Acionario") or "").strip(),
                "fundacao": (emp.get("Data_Constituicao") or "")[:4],
                "preco": p["close"],
                "var_pct": round(p.get("change") or 0, 2),
                "financeiro": p["close"] * (p.get("volume") or 0),
                "valor_mercado": p.get("market_cap"),
                "comunicados": recentes[:10],
            })
        acoes.sort(key=lambda a: a["valor_mercado"] or 0, reverse=True)
        Path(pasta).mkdir(parents=True, exist_ok=True)
        (Path(pasta) / "acoes.json").write_text(
            json.dumps({"atualizado": hoje.isoformat(), "acoes": acoes}, ensure_ascii=False), encoding="utf-8")
        return len(acoes)
    except Exception as e:
        erros.append(f"Ações: {e}")
        return None
