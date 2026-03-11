# 13F Tracker

Dashboard interativo para monitorar os portfolios de hedge funds via SEC EDGAR.

Mostra posições, posições consensuais, movers e portfolios individuais para os trimestres mais recentes.

---

## Como rodar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

Acesse em `http://localhost:8501`

---

## Como hospedar grátis no Streamlit Cloud

1. Faça fork ou push deste repositório para sua conta no GitHub
2. Acesse [https://streamlit.io/cloud](https://streamlit.io/cloud) e faça login com GitHub
3. Clique em **"New app"**
4. Selecione o repositório, branch `main` e arquivo `app.py`
5. Clique em **"Deploy"** — em ~1 minuto o app estará online

---

## Como usar o app

1. **Selecione o trimestre** na barra lateral (ex: `2025Q4`)
2. **Selecione as gestoras** que deseja monitorar (ou clique "Todas")
3. Clique em **"🔄 Atualizar da SEC"** para buscar os dados
   - A primeira busca pode levar alguns minutos
   - Após isso, os dados ficam em cache local (pasta `data/`)
4. Explore as abas:
   - **Overview**: estatísticas gerais e posições mais consensuais
   - **Movers**: posições novas e encerradas vs. trimestre anterior
   - **Portfolios**: portfolio completo de cada gestora

---

## Adicionar novas gestoras

Edite o arquivo `src/funds_config.py` e adicione:

```python
"Nome da Gestora": "CIK_NUMBER",
```

Para encontrar o CIK de qualquer gestora:
👉 https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=13F-HR

---

## Estrutura do projeto

```
app.py                  ← App principal (Streamlit)
requirements.txt
.streamlit/
    config.toml         ← Tema dark
src/
    funds_config.py     ← Lista de gestoras + CIKs
    sec_fetcher.py      ← Busca dados no SEC EDGAR
    data_processor.py   ← Cálculo de métricas
data/                   ← Cache local (criado automaticamente)
```
