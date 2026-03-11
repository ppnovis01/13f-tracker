"""
Processamento e agregação dos dados de 13F.

Funções para calcular métricas, posições consensuais, movers e portfolios.
"""

import pandas as pd
from collections import defaultdict
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _add_weights(holdings: list[dict]) -> list[dict]:
    """Adiciona 'weight_pct' (% do portfolio) a cada posição."""
    total = sum(h["value_usd"] for h in holdings)
    if total == 0:
        return [{**h, "weight_pct": 0.0} for h in holdings]
    return [{**h, "weight_pct": round(h["value_usd"] / total * 100, 2)} for h in holdings]


def _top_n(holdings: list[dict], n: int) -> list[dict]:
    """Retorna as top-N posições por valor."""
    return sorted(holdings, key=lambda x: x["value_usd"], reverse=True)[:n]


def _stock_id(h: dict) -> str:
    """Identificador único de um ativo (prefere CUSIP, fallback para nome)."""
    return h["cusip"] if h.get("cusip") else h["name"]


# ── Overview ─────────────────────────────────────────────────────────────────

def compute_overview_stats(all_holdings: dict[str, list[dict]]) -> dict:
    """Calcula métricas gerais: fundos, ações únicas, posições totais, AUM."""
    unique_stocks: set[str] = set()
    total_positions = 0
    combined_value = 0

    for holdings in all_holdings.values():
        total_positions += len(holdings)
        combined_value += sum(h["value_usd"] for h in holdings)
        for h in holdings:
            unique_stocks.add(_stock_id(h))

    aum_billions = combined_value / 1e9  # value_usd → billions

    return {
        "n_funds": len(all_holdings),
        "unique_stocks": len(unique_stocks),
        "total_positions": total_positions,
        "combined_aum_billions": aum_billions,
    }


# ── Consensual Positions ─────────────────────────────────────────────────────

def compute_consensual_positions(
    all_holdings: dict[str, list[dict]],
    top_n: int,
    prev_holdings: Optional[dict[str, list[dict]]] = None,
) -> pd.DataFrame:
    """
    Calcula as posições mais consensuais entre as gestoras.
    São as ações que aparecem no top-N de mais fundos.
    """
    # Para cada fundo: top-N por nome, e pesos de todas as posições
    fund_top_names: dict[str, set[str]] = {}
    fund_all_weights: dict[str, dict[str, float]] = {}

    for fund, holdings in all_holdings.items():
        weighted = _add_weights(holdings)
        fund_top_names[fund] = {h["name"] for h in _top_n(weighted, top_n)}
        fund_all_weights[fund] = {h["name"]: h["weight_pct"] for h in weighted}

    # Contagem de aparições no top-N + peso médio entre os fundos que a têm no top-N
    count_in_top: dict[str, int] = defaultdict(int)
    all_weights: dict[str, list[float]] = defaultdict(list)

    for fund, top_names in fund_top_names.items():
        for name in top_names:
            count_in_top[name] += 1
            w = fund_all_weights[fund].get(name, 0.0)
            if w > 0:
                all_weights[name].append(w)

    # Média de pesos entre os fundos que a detêm
    def avg_weight(name: str) -> float:
        ws = all_weights[name]
        return sum(ws) / len(ws) if ws else 0.0

    # Ordenar por contagem, depois por peso médio
    sorted_stocks = sorted(
        count_in_top.items(),
        key=lambda x: (-x[1], -avg_weight(x[0]))
    )

    # Calcular mesmo para trimestre anterior
    prev_count_in_top: dict[str, int] = defaultdict(int)
    prev_all_weights: dict[str, list[float]] = defaultdict(list)

    if prev_holdings:
        for fund, holdings in prev_holdings.items():
            weighted = _add_weights(holdings)
            prev_fund_weights = {h["name"]: h["weight_pct"] for h in weighted}
            top_set = {h["name"] for h in _top_n(weighted, top_n)}
            for name in top_set:
                prev_count_in_top[name] += 1
                w = prev_fund_weights.get(name, 0.0)
                if w > 0:
                    prev_all_weights[name].append(w)

    rows = []
    for rank, (name, count) in enumerate(sorted_stocks, start=1):
        avg_w = avg_weight(name)
        row = {
            "#": rank,
            "EMPRESA": name,
            "FUNDOS": count,
            "PESO MÉDIO": f"{avg_w:.1f}%",
        }
        if prev_holdings is not None:
            prev_count = prev_count_in_top.get(name, 0)
            prev_ws = prev_all_weights.get(name, [])
            prev_avg = sum(prev_ws) / len(prev_ws) if prev_ws else 0.0
            row["FUNDOS (T-1)"] = prev_count if prev_count > 0 else ""
            row["PESO MÉDIO (T-1)"] = f"{prev_avg:.1f}%" if prev_avg > 0 else ""
        rows.append(row)

    return pd.DataFrame(rows)


# ── Movers ────────────────────────────────────────────────────────────────────

def compute_movers(
    curr_holdings: dict[str, list[dict]],
    prev_holdings: dict[str, list[dict]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retorna:
    - new_df: posições novas (não existiam no trimestre anterior)
    - closed_df: posições encerradas (existiam no trimestre anterior)
    """
    curr_names: set[str] = set()
    prev_names: set[str] = set()

    curr_stock_data: dict[str, dict] = defaultdict(lambda: {"funds": [], "weights": []})
    prev_stock_data: dict[str, dict] = defaultdict(lambda: {"funds": [], "weights": []})

    def _collect(all_h: dict[str, list[dict]], names: set, stock_data: dict) -> None:
        """Agrega posições por ativo, consolidando entradas duplicadas de um mesmo fundo."""
        for fund, holdings in all_h.items():
            weighted = _add_weights(holdings)
            # Soma peso e valor por ativo dentro deste fundo (elimina duplicatas de sub-contas)
            fund_by_stock: dict[str, float] = defaultdict(float)
            for h in weighted:
                fund_by_stock[h["name"]] += h["weight_pct"]
            for stock_name, w in fund_by_stock.items():
                names.add(stock_name)
                stock_data[stock_name]["funds"].append(fund)
                stock_data[stock_name]["weights"].append(w)

    _collect(curr_holdings, curr_names, curr_stock_data)
    _collect(prev_holdings, prev_names, prev_stock_data)

    def _avg_w(data: dict) -> float:
        ws = data["weights"]
        return sum(ws) / len(ws) if ws else 0.0

    # Novas posições
    new_rows = []
    for name in sorted(curr_names - prev_names, key=lambda n: -_avg_w(curr_stock_data[n])):
        d = curr_stock_data[name]
        funds_list = d["funds"]
        new_rows.append({
            "EMPRESA": name,
            "Nº FUNDOS": len(funds_list),
            "PESO MÉDIO": f"{_avg_w(d):.1f}%",
            "GESTORAS": ", ".join(funds_list[:4]) + ("..." if len(funds_list) > 4 else ""),
        })

    # Posições encerradas
    closed_rows = []
    for name in sorted(prev_names - curr_names, key=lambda n: -_avg_w(prev_stock_data[n])):
        d = prev_stock_data[name]
        funds_list = d["funds"]
        closed_rows.append({
            "EMPRESA": name,
            "Nº FUNDOS": len(funds_list),
            "PESO MÉDIO (T-1)": f"{_avg_w(d):.1f}%",
            "GESTORAS": ", ".join(funds_list[:4]) + ("..." if len(funds_list) > 4 else ""),
        })

    return pd.DataFrame(new_rows), pd.DataFrame(closed_rows)


# ── Individual Portfolio ──────────────────────────────────────────────────────

def get_portfolio_df(holdings: list[dict]) -> pd.DataFrame:
    """Retorna o portfolio de uma gestora como DataFrame formatado."""
    if not holdings:
        return pd.DataFrame()

    # Consolidar posições duplicadas pelo mesmo ativo (mesmo CUSIP ou mesmo nome)
    consolidated: dict[str, dict] = {}
    for h in holdings:
        key = h["cusip"] if h.get("cusip") else h["name"]
        if key in consolidated:
            consolidated[key]["value_usd"] += h["value_usd"]
            consolidated[key]["shares"] = (consolidated[key].get("shares") or 0) + (h.get("shares") or 0)
        else:
            consolidated[key] = {**h}

    weighted = _add_weights(list(consolidated.values()))
    weighted.sort(key=lambda x: -x["value_usd"])

    rows = []
    for i, h in enumerate(weighted, start=1):
        val_m = h["value_usd"] / 1e6
        rows.append({
            "#": i,
            "EMPRESA": h["name"],
            "CUSIP": h.get("cusip", ""),
            "VALOR (US$M)": f"${val_m:,.1f}M",
            "AÇÕES": f"{h['shares']:,}" if h.get("shares") else "—",
            "% PORTFOLIO": f"{h['weight_pct']:.2f}%",
        })

    return pd.DataFrame(rows)
