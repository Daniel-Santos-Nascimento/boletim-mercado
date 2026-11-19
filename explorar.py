"""Script temporário: mostra a estrutura dos balanços (ITR/DFP) da CVM no log."""
import csv, io, zipfile, time
import requests

H = {"User-Agent": "Mozilla/5.0 (compatible; boletim-mercado/1.0)"}
ALVOS = {"33.000.167/0001-01": "PETROBRAS", "60.872.504/0001-23": "ITAU"}

def baixar(url):
    t = time.time(); r = requests.get(url, headers=H, timeout=300)
    print(f"{url} -> {r.status_code} {len(r.content)/1e6:.1f} MB em {time.time()-t:.0f}s")
    return zipfile.ZipFile(io.BytesIO(r.content))

for url in ["https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_2026.zip",
            "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_2025.zip"]:
    z = baixar(url)
    nomes = z.namelist()
    print("arquivos:", nomes)
    for parte in ("DRE_con", "BPP_con", "BPA_con"):
        n = next((x for x in nomes if parte in x), None)
        if not n:
            continue
        t = z.read(n).decode("latin-1")
        print(f"--- {n}: {len(t)/1e6:.1f} MB")
        leitor = csv.DictReader(io.StringIO(t), delimiter=";")
        print("colunas:", leitor.fieldnames)
        vistos = 0
        for l in leitor:
            if l["CNPJ_CIA"] in ALVOS and l["CD_CONTA"].count(".") <= 2:
                print(" ", ALVOS[l["CNPJ_CIA"]], l["DT_REFER"], l.get("ORDEM_EXERC"), l.get("DT_INI_EXERC", ""), l["DT_FIM_EXERC"],
                      l["ESCALA_MOEDA"], l["CD_CONTA"], l["DS_CONTA"][:60], l["VL_CONTA"], l.get("ST_CONTA_FIXA"))
                vistos += 1
                if vistos > 110:
                    break
