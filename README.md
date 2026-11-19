# Boletim do Mercado

Site estático com um boletim diário do mercado (juros, inflação, câmbio, bolsa, FIIs, Focus e fatos relevantes),
gerado automaticamente todo dia útil às 6h pelo GitHub Actions.

## Como colocar no ar (uma vez só)

1. **Crie o repositório** no GitHub: botão *New repository* → nome `boletim-mercado` → **Public** → *Create*.
2. **Envie os arquivos**: na página do repositório, *Add file → Upload files*, arraste **todo o conteúdo** desta pasta
   (inclusive a pasta `.github`) e clique em *Commit changes*.
   > Se o navegador não mostrar a pasta `.github` (ela é oculta), crie o arquivo pelo site:
   > *Add file → Create new file*, nome `.github/workflows/diario.yml`, e cole o conteúdo.
3. **Ative o GitHub Pages**: *Settings → Pages → Build and deployment → Source:* **GitHub Actions**.
4. **Token da brapi** (para Ibovespa e IFIX): crie conta grátis em brapi.dev, copie o token e salve em
   *Settings → Secrets and variables → Actions → New repository secret* com o nome `BRAPI_TOKEN`.
5. **Primeira execução**: aba *Actions → Boletim diário → Run workflow*. Em ~2 minutos o site estará em
   `https://SEU-USUARIO.github.io/boletim-mercado`.
6. Edite `config.json` com o nome do site, seu e-mail e o endereço real.

## Rotina diária (15 minutos)

1. Às 6h o robô coleta os dados e publica o boletim sozinho.
2. Abra o site, leia os números.
3. No GitHub, crie `comentarios/AAAA-MM-DD.md` (ex.: `comentarios/2026-11-20.md`) com a sua leitura do dia,
   em texto simples. Ao salvar, o site é regerado com o seu comentário em ~1 minuto.

O comentário é o que diferencia o site. Regra de ouro: **explicar e contextualizar, nunca recomendar ativo específico**.

## Estrutura

| Arquivo | Para que serve |
|---|---|
| `coletar.py` | Busca os dados no Banco Central, CVM e brapi e salva em `data/` |
| `gerar_site.py` | Monta as páginas HTML na pasta `site/` |
| `templates/` | Layout das páginas e o visual (`style.css`) |
| `comentarios/` | Seus textos diários, um arquivo por dia |
| `config.json` | Nome do site, slogan, autor, link da newsletter |
| `.github/workflows/diario.yml` | O robô diário |

## Testar no computador (opcional)

```bash
pip install -r requirements.txt
python gerar_site.py --exemplo   # visual com dados de exemplo
python coletar.py                # dados reais (precisa de internet)
python gerar_site.py
```
Abra `site/index.html` no navegador.

## Fontes

- Banco Central do Brasil — SGS (Selic 432, CDI 4389, IPCA 433 e 13522, dólar 1, euro 21619) e Focus (API Olinda)
- CVM — dados abertos, documentos IPE (fatos relevantes)
- brapi.dev — Ibovespa (`^BVSP`) e IFIX
