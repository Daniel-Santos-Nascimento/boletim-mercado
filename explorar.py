"""Script temporário: mostra a estrutura dos dados de ações (CVM e brapi) no log."""
import csv, io, os, zipfile
import requests

H = {"User-Agent": "Mozilla/5.0 (compatible; boletim-mercado/1.0)"}

def zipar(url):
    return zipfile.ZipFile(io.BytesIO(requests.get(url, headers=H, timeout=180).content))

print("=== FCA 2026 ===")
z = zipar("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_2026.zip")
for n in z.namelist():
    t = z.read(n).decode("latin-1")
    linhas = list(csv.reader(io.StringIO(t), delimiter=";"))
    print(f"--- {n}: {len(linhas)} linhas")
    print(" | ".join(linhas[0]))
    alvo = [l for l in linhas if any("PETR" in c or "PETROLEO BRASILEIRO" in c.upper() for c in l)]
    for l in (alvo[-2:] or linhas[1:2]):
        print("   ", " | ".join(l)[:700])

print("=== IPE categorias ===")
z = zipar("https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_2026.zip")
t = z.read(z.namelist()[0]).decode("latin-1")
linhas = list(csv.DictReader(io.StringIO(t), delimiter=";"))
print("colunas:", list(linhas[0].keys()))
cont = {}
for l in linhas:
    cont[l["Categoria"]] = cont.get(l["Categoria"], 0) + 1
print(sorted(cont.items(), key=lambda x: -x[1])[:20])
print([ (l["Data_Entrega"], l["Categoria"], l["Assunto"][:60]) for l in linhas if "PETROLEO BRASILEIRO" in l["Nome_Companhia"].upper()][-5:])

print("=== BRAPI STOCKS ===")
p = {"type": "stock", "sortBy": "volume", "sortOrder": "desc", "limit": 1000}
if os.getenv("BRAPI_TOKEN"):
    p["token"] = os.getenv("BRAPI_TOKEN")
j = requests.get("https://brapi.dev/api/quote/list", params=p, headers=H, timeout=60).json()
print("itens:", len(j.get("stocks", [])), "total:", j.get("totalCount"))
setores = {}
for s in j.get("stocks", []):
    setores[s.get("sector")] = setores.get(s.get("sector"), 0) + 1
print(setores)
print([s for s in j.get("stocks", []) if s["stock"] in ("PETR4", "ITUB4", "WEGE3")])
