"""Script temporário: mostra a estrutura dos dados de FIIs (CVM e brapi) no log."""
import csv, io, os, zipfile
import requests

H = {"User-Agent": "Mozilla/5.0 (compatible; boletim-mercado/1.0)"}

def zipar(url):
    return zipfile.ZipFile(io.BytesIO(requests.get(url, headers=H, timeout=120).content))

print("=== DICIONARIO ===")
z = zipar("https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/META/meta_inf_mensal_fii.zip")
for n in z.namelist():
    t = z.read(n).decode("latin-1")
    print(f"--- {n} ({len(t)} chars)")
    print(t[:6000])

print("=== DADOS 2026 ===")
z = zipar("https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_2026.zip")
for n in z.namelist():
    t = z.read(n).decode("latin-1")
    linhas = list(csv.reader(io.StringIO(t), delimiter=";"))
    print(f"--- {n}: {len(linhas)} linhas")
    print(" | ".join(linhas[0]))
    hglg = [l for l in linhas if any("HGLG" in c or "PATRIA LOG" in c.upper() for c in l)]
    for l in (hglg[-2:] or linhas[1:3]):
        print("   ", " | ".join(l))

print("=== BRAPI FUNDS ===")
p = {"type": "fund", "sortBy": "volume", "sortOrder": "desc", "limit": 1000}
if os.getenv("BRAPI_TOKEN"):
    p["token"] = os.getenv("BRAPI_TOKEN")
r = requests.get("https://brapi.dev/api/quote/list", params=p, headers=H, timeout=60)
j = r.json()
print(r.status_code, "itens:", len(j.get("stocks", [])), "total:", j.get("totalCount"), "paginas:", j.get("totalPages"), j.get("message"))
for s in j.get("stocks", [])[:3]:
    print("   ", s)
r = requests.get("https://brapi.dev/api/quote/HGLG11", params={"token": os.getenv("BRAPI_TOKEN", ""), "dividends": "true", "range": "1y", "interval": "1mo"}, headers=H, timeout=60)
j = r.json()
q = (j.get("results") or [{}])[0]
print("HGLG11 chaves:", list(q.keys()))
print("dividendsData:", str(q.get("dividendsData"))[:1500])
print("hist:", str(q.get("historicalDataPrice"))[:600])
