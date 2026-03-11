"""
SEC EDGAR 13F data fetcher.

Busca dados de 13F-HR direto da API pública do SEC EDGAR.
Mantém cache local em JSON para evitar re-buscar dados já carregados.
"""

import requests
import xml.etree.ElementTree as ET
import json
import os
import re
import time
from datetime import datetime
from typing import Optional

# Headers obrigatórios pela SEC (identificação do solicitante)
HEADERS = {
    "User-Agent": "13F Portfolio Tracker contact@13ftracker.io",
    "Accept-Encoding": "gzip, deflate",
}

BASE_SEC = "https://data.sec.gov"
BASE_EDGAR = "https://www.sec.gov"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_path(cik: str, quarter: str) -> str:
    folder = os.path.join(CACHE_DIR, cik)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{quarter}.json")


def load_cache(cik: str, quarter: str) -> Optional[list]:
    path = _cache_path(cik, quarter)
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def save_cache(cik: str, quarter: str, holdings: list) -> None:
    path = _cache_path(cik, quarter)
    with open(path, "w") as f:
        json.dump(holdings, f)


# ── Quarter helpers ───────────────────────────────────────────────────────────

def period_to_quarter(period: str) -> str:
    """Converte '2025-12-31' → '2025Q4'."""
    if not period:
        return ""
    try:
        dt = datetime.strptime(period[:10], "%Y-%m-%d")
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}Q{q}"
    except Exception:
        return ""


def get_previous_quarter(quarter: str) -> str:
    """Retorna o trimestre anterior. Ex: '2025Q1' → '2024Q4'."""
    try:
        year = int(quarter[:4])
        q = int(quarter[5])
        if q == 1:
            return f"{year - 1}Q4"
        return f"{year}Q{q - 1}"
    except Exception:
        return ""


# ── SEC API ───────────────────────────────────────────────────────────────────

def get_fund_filings(cik: str) -> list[dict]:
    """
    Retorna lista de 13F-HR filings disponíveis para uma gestora.
    Cada item: {"quarter": "2025Q4", "period": "2025-12-31", "accession": "..."}
    """
    cik_padded = str(cik).zfill(10)
    url = f"{BASE_SEC}/submissions/CIK{cik_padded}.json"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    periods = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])

    seen_quarters: set[str] = set()
    results = []

    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        period = periods[i] if i < len(periods) else ""
        quarter = period_to_quarter(period)
        acc = accessions[i] if i < len(accessions) else ""
        if quarter and acc and quarter not in seen_quarters:
            seen_quarters.add(quarter)
            results.append({"quarter": quarter, "period": period, "accession": acc})

    return sorted(results, key=lambda x: x["quarter"], reverse=True)


def _find_info_table_url(cik: str, accession: str) -> Optional[str]:
    """
    Encontra a URL do arquivo XML com a tabela de posições dentro do filing.

    Padrão típico dos filings 13F-HR:
      - primary_doc.xml          → cabeçalho do formulário (NÃO é a tabela)
      - infotable.xml / 50240.xml → info table (as posições)
      - xslForm13F_X02/*.xml      → versão estilizada para o browser (ignorar)
    """
    cik_int = int(cik)
    acc_clean = accession.replace("-", "")
    base_path = f"/Archives/edgar/data/{cik_int}/{acc_clean}/"
    index_url = f"{BASE_EDGAR}{base_path}{accession}-index.htm"

    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    # Coleta todos os hrefs da pasta do filing
    all_hrefs = re.findall(r'href="(/Archives/[^"]+)"', html)

    # Separa: arquivos diretos (sem subpastas) vs. arquivos em subpastas (xslForm13F_X02/)
    direct_xml: list[str] = []
    for href in all_hrefs:
        if not href.startswith(base_path):
            continue
        remainder = href[len(base_path):]  # ex: "infotable.xml" ou "xslForm13F_X02/..."
        if "/" not in remainder and remainder.lower().endswith(".xml"):
            direct_xml.append(href)

    # 1. Prefere arquivos com "infotable", "holding" no nome
    for href in direct_xml:
        fname = href.split("/")[-1].lower()
        if any(kw in fname for kw in ("infotable", "inftable", "holding")):
            return f"{BASE_EDGAR}{href}"

    # 2. Qualquer XML direto que NÃO seja o primary_doc
    for href in direct_xml:
        fname = href.split("/")[-1].lower()
        if "primary" not in fname and "header" not in fname:
            return f"{BASE_EDGAR}{href}"

    # 3. Último recurso: qualquer XML direto
    if direct_xml:
        return f"{BASE_EDGAR}{direct_xml[-1]}"

    return None


def _strip_ns(tag: str) -> str:
    """Remove namespace de uma tag XML. Ex: '{http://...}infoTable' → 'infoTable'."""
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_holdings_xml(xml_text: str) -> list[dict]:
    """
    Parseia o XML de informationTable e retorna lista de posições.
    Cada item: {"name", "cusip", "value_usd", "shares"}
    Nota: o campo 'value' nos XMLs 13F modernos está em dólares (não milhares).
    """
    holdings = []
    try:
        root = ET.fromstring(xml_text)

        # Encontra todos os elementos infoTable (independente do namespace)
        info_tables = [el for el in root.iter() if _strip_ns(el.tag) == "infoTable"]

        for table in info_tables:
            # Mapa tag_local → texto
            children = {_strip_ns(child.tag): child for child in table.iter()}

            def get(tag: str) -> str:
                el = children.get(tag)
                return el.text.strip() if el is not None and el.text else ""

            name = get("nameOfIssuer")
            cusip = get("cusip")
            value_str = get("value")
            shares_str = get("sshPrnamt")

            if not name or not value_str:
                continue

            try:
                value = int(value_str.replace(",", ""))
                shares = int(shares_str.replace(",", "")) if shares_str else 0
                holdings.append({
                    "name": name.upper().strip(),
                    "cusip": cusip,
                    "value_usd": value,
                    "shares": shares,
                })
            except ValueError:
                continue

    except ET.ParseError:
        pass

    return holdings


def fetch_holdings_from_sec(cik: str, accession: str) -> list[dict]:
    """Baixa e parseia as posições de um filing específico."""
    xml_url = _find_info_table_url(cik, accession)
    if not xml_url:
        return []

    try:
        time.sleep(0.12)  # respeita rate limit da SEC (max ~10 req/s)
        resp = requests.get(xml_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return _parse_holdings_xml(resp.text)
    except Exception:
        return []


def get_holdings(
    cik: str,
    fund_name: str,
    quarter: str,
    force_refresh: bool = False,
) -> list[dict]:
    """
    Retorna as posições de uma gestora em um trimestre.
    Usa cache local; busca na SEC somente se necessário.
    """
    if not force_refresh:
        cached = load_cache(cik, quarter)
        if cached is not None:
            return cached

    filings = get_fund_filings(cik)
    target = next((f for f in filings if f["quarter"] == quarter), None)

    if not target:
        save_cache(cik, quarter, [])
        return []

    holdings = fetch_holdings_from_sec(cik, target["accession"])
    save_cache(cik, quarter, holdings)
    return holdings


def get_all_quarters_from_sec(ciks: list[str]) -> list[str]:
    """Retorna a união de trimestres disponíveis entre todas as gestoras."""
    quarters: set[str] = set()
    for cik in ciks:
        for filing in get_fund_filings(cik)[:8]:
            quarters.add(filing["quarter"])
    return sorted(quarters, reverse=True)
