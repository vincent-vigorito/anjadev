#!/usr/bin/env python3
"""mcp_bybit_lite.py — Bybit minimal MCP wrapper (Fase 16+).

Espone SOLO 5 tool curati (vs 329 di bybit-mcp-v214 originale):
  - bybit.ticker(symbol)       — prezzo + 24h stats
  - bybit.balance(account_type?) — wallet balance (UNIFIED default)
  - bybit.positions(category?, symbol?) — posizioni aperte
  - bybit.klines(symbol, interval, limit?) — candele
  - bybit.recent_trades(symbol, limit?) — ultimi trade

Chiama direttamente l'API Bybit via HTTPS (signed con HMAC).
Env var richieste: BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_DEMO (true|false).

Stdlib only (urllib + hmac + hashlib).
"""

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
import urllib.parse


PROTO_VERSION = "2024-11-05"
SERVER_NAME = "anja_bybit_lite"
SERVER_VERSION = "0.1.0"

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
IS_DEMO = os.environ.get("BYBIT_DEMO", "false").lower() == "true"

BASE_URL = "https://api-demo.bybit.com" if IS_DEMO else "https://api.bybit.com"
RECV_WINDOW = "30000"  # 30s — tollera clock skew client/server


def _sign(query_string: str, timestamp: str) -> str:
    """HMAC-SHA256 signing per Bybit v5."""
    sig_payload = timestamp + API_KEY + RECV_WINDOW + query_string
    return hmac.new(
        API_SECRET.encode(), sig_payload.encode(), hashlib.sha256
    ).hexdigest()


def _http_post(endpoint: str, body: dict) -> dict:
    """POST signed a Bybit API (per place_order/cancel/set_trading_stop)."""
    if not API_KEY or not API_SECRET:
        return {"error": "BYBIT_API_KEY / BYBIT_API_SECRET non impostati"}
    body_str = json.dumps(body, separators=(',', ':'))
    ts = str(int(time.time() * 1000))
    sig = _sign(body_str, ts)  # per POST, query_string param contiene il body string
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "anja-bybit-lite/0.1",
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sig,
    }
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(url, data=body_str.encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = str(e)
        return {"error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _http_get(endpoint: str, params: dict = None, signed: bool = False) -> dict:
    """GET request a Bybit API."""
    qs = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{endpoint}"
    if qs:
        url += "?" + qs
    headers = {"User-Agent": "anja-bybit-lite/0.1"}
    if signed:
        if not API_KEY or not API_SECRET:
            return {"error": "BYBIT_API_KEY / BYBIT_API_SECRET non impostati"}
        ts = str(int(time.time() * 1000))
        sig = _sign(qs, ts)
        headers.update({
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": sig,
        })
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = str(e)
        return {"error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ============================================================
# Tools
# ============================================================

def tool_ticker(args: dict) -> dict:
    """GET ticker prezzo + 24h stats per un simbolo."""
    symbol = (args.get("symbol") or "").upper().strip()
    if not symbol:
        return {"error": "symbol required (es. BTCUSDT)"}
    category = (args.get("category") or "linear").strip()
    r = _http_get("/v5/market/tickers", {"category": category, "symbol": symbol})
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    lst = r.get("result", {}).get("list", [])
    if not lst:
        return {"error": f"symbol {symbol} not found in category {category}"}
    t = lst[0]
    return {
        "symbol": t.get("symbol"),
        "lastPrice": t.get("lastPrice"),
        "bid1Price": t.get("bid1Price"),
        "ask1Price": t.get("ask1Price"),
        "high24h": t.get("highPrice24h"),
        "low24h": t.get("lowPrice24h"),
        "volume24h": t.get("volume24h"),
        "turnover24h": t.get("turnover24h"),
        "price24hPcnt": t.get("price24hPcnt"),
        "openInterest": t.get("openInterest"),
        "fundingRate": t.get("fundingRate"),
        "category": category,
    }


def tool_balance(args: dict) -> dict:
    """GET wallet balance dell'account."""
    account_type = (args.get("account_type") or "UNIFIED").upper().strip()
    coin = (args.get("coin") or "").upper().strip()
    params = {"accountType": account_type}
    if coin:
        params["coin"] = coin
    r = _http_get("/v5/account/wallet-balance", params, signed=True)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    out = []
    for acc in r.get("result", {}).get("list", []):
        for c in acc.get("coin", []):
            we = c.get("walletBalance", "0")
            try:
                if float(we) == 0 and not coin:
                    continue  # skip zero balances unless filtered
            except Exception:
                pass
            out.append({
                "coin": c.get("coin"),
                "wallet_balance": we,
                "available_balance": c.get("availableToWithdraw") or c.get("free"),
                "usd_value": c.get("usdValue"),
            })
    return {"account_type": account_type, "coins": out, "total_equity_usd": acc.get("totalEquity") if r.get("result", {}).get("list") else None}


def tool_positions(args: dict) -> dict:
    """GET posizioni aperte."""
    category = (args.get("category") or "linear").strip()
    symbol = (args.get("symbol") or "").upper().strip()
    settle_coin = (args.get("settle_coin") or "USDT").upper().strip()
    params = {"category": category, "settleCoin": settle_coin}
    if symbol:
        params["symbol"] = symbol
    r = _http_get("/v5/position/list", params, signed=True)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    out = []
    for p in r.get("result", {}).get("list", []):
        try:
            size = float(p.get("size", "0"))
            if size == 0:
                continue
        except Exception:
            pass
        out.append({
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "size": p.get("size"),
            "entry_price": p.get("avgPrice"),
            "mark_price": p.get("markPrice"),
            "unrealized_pnl": p.get("unrealisedPnl"),
            "leverage": p.get("leverage"),
            "liq_price": p.get("liqPrice"),
        })
    if not out:
        return {
            "positions": [],
            "count": 0,
            "status": "no_open_positions",
            "message": f"Nessuna posizione aperta in category={category}, settleCoin={settle_coin}. Account autenticato OK.",
            "account": "DEMO" if IS_DEMO else "LIVE",
        }
    return {"positions": out, "count": len(out), "status": "ok"}


def tool_klines(args: dict) -> dict:
    """GET candele (klines)."""
    symbol = (args.get("symbol") or "").upper().strip()
    interval = str(args.get("interval") or "60")  # 1,3,5,15,30,60,120,240,360,720,D,W,M
    limit = min(int(args.get("limit", 50)), 200)
    category = (args.get("category") or "linear").strip()
    if not symbol:
        return {"error": "symbol required"}
    r = _http_get("/v5/market/kline", {
        "category": category, "symbol": symbol, "interval": interval, "limit": limit,
    })
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    # Bybit ritorna list di array [timestamp, open, high, low, close, volume, turnover]
    raw = r.get("result", {}).get("list", [])
    candles = [{
        "ts": int(c[0]),
        "open": float(c[1]),
        "high": float(c[2]),
        "low": float(c[3]),
        "close": float(c[4]),
        "volume": float(c[5]),
    } for c in raw]
    return {"symbol": symbol, "interval": interval, "count": len(candles), "candles": candles}


def tool_recent_trades(args: dict) -> dict:
    """GET trade recenti del mercato."""
    symbol = (args.get("symbol") or "").upper().strip()
    limit = min(int(args.get("limit", 20)), 60)
    category = (args.get("category") or "linear").strip()
    if not symbol:
        return {"error": "symbol required"}
    r = _http_get("/v5/market/recent-trade", {
        "category": category, "symbol": symbol, "limit": limit,
    })
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    return {"symbol": symbol, "trades": r.get("result", {}).get("list", [])}


def tool_closed_pnl(args: dict) -> dict:
    """GET P/L su posizioni chiuse (storico realizzato)."""
    category = (args.get("category") or "linear").strip()
    symbol = (args.get("symbol") or "").upper().strip()
    limit = min(int(args.get("limit", 20)), 50)
    params = {"category": category, "limit": limit}
    if symbol:
        params["symbol"] = symbol
    r = _http_get("/v5/position/closed-pnl", params, signed=True)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    raw = r.get("result", {}).get("list", [])
    out = []
    total_pnl = 0.0
    for p in raw:
        try:
            pnl = float(p.get("closedPnl", "0"))
            total_pnl += pnl
        except Exception:
            pnl = 0
        out.append({
            "symbol": p.get("symbol"),
            "side": p.get("side"),
            "size": p.get("qty"),
            "avg_entry": p.get("avgEntryPrice"),
            "avg_exit": p.get("avgExitPrice"),
            "closed_pnl": p.get("closedPnl"),
            "exec_type": p.get("execType"),
            "leverage": p.get("leverage"),
            "ts": int(p.get("updatedTime", 0)),
        })
    if not out:
        return {"trades": [], "count": 0, "status": "no_closed_positions",
                "message": "Nessuna posizione chiusa nel periodo recente",
                "account": "DEMO" if IS_DEMO else "LIVE"}
    return {
        "category": category,
        "symbol_filter": symbol or "all",
        "count": len(out),
        "total_pnl": round(total_pnl, 4),
        "trades": out,
    }


def tool_order_history(args: dict) -> dict:
    """GET storico ordini (filled/cancelled/rejected)."""
    category = (args.get("category") or "linear").strip()
    symbol = (args.get("symbol") or "").upper().strip()
    order_status = (args.get("order_status") or "").strip()  # Filled, Cancelled, ...
    limit = min(int(args.get("limit", 20)), 50)
    params = {"category": category, "limit": limit}
    if symbol:
        params["symbol"] = symbol
    if order_status:
        params["orderStatus"] = order_status
    r = _http_get("/v5/order/history", params, signed=True)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    raw = r.get("result", {}).get("list", [])
    out = []
    for o in raw:
        out.append({
            "order_id": o.get("orderId"),
            "symbol": o.get("symbol"),
            "side": o.get("side"),
            "order_type": o.get("orderType"),
            "qty": o.get("qty"),
            "price": o.get("price"),
            "avg_fill_price": o.get("avgPrice"),
            "status": o.get("orderStatus"),
            "cum_exec_qty": o.get("cumExecQty"),
            "cum_exec_value": o.get("cumExecValue"),
            "reduce_only": o.get("reduceOnly"),
            "ts_created": int(o.get("createdTime", 0)),
            "ts_updated": int(o.get("updatedTime", 0)),
        })
    return {"category": category, "symbol_filter": symbol or "all",
            "status_filter": order_status or "all", "count": len(out), "orders": out}


def tool_funding_history(args: dict) -> dict:
    """GET funding rate storico per un simbolo perp."""
    category = (args.get("category") or "linear").strip()
    symbol = (args.get("symbol") or "").upper().strip()
    limit = min(int(args.get("limit", 20)), 200)
    if not symbol:
        return {"error": "symbol required (es. BTCUSDT)"}
    # /v5/market/funding/history è pubblico — no signed
    r = _http_get("/v5/market/funding/history", {
        "category": category, "symbol": symbol, "limit": limit,
    })
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    raw = r.get("result", {}).get("list", [])
    fundings = [{
        "ts": int(f.get("fundingRateTimestamp", 0)),
        "funding_rate": f.get("fundingRate"),
        "symbol": f.get("symbol"),
    } for f in raw]
    # Stats: avg, max, min
    rates = []
    for f in fundings:
        try:
            rates.append(float(f["funding_rate"]))
        except Exception:
            pass
    stats = {}
    if rates:
        stats = {
            "avg": round(sum(rates)/len(rates), 6),
            "max": round(max(rates), 6),
            "min": round(min(rates), 6),
            "current": rates[0] if rates else None,
        }
    return {"symbol": symbol, "category": category, "count": len(fundings),
            "stats": stats, "fundings": fundings}


def tool_orderbook(args: dict) -> dict:
    """GET orderbook (bid/ask depth) di un simbolo."""
    symbol = (args.get("symbol") or "").upper().strip()
    if not symbol:
        return {"error": "symbol required"}
    category = (args.get("category") or "linear").strip()
    # Limit caps per category: spot 1-200, linear/inverse 1-500
    default_limit = 25
    max_limit = 500 if category in ("linear", "inverse") else 200
    limit = min(int(args.get("limit", default_limit)), max_limit)
    r = _http_get("/v5/market/orderbook", {
        "category": category, "symbol": symbol, "limit": limit,
    })
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    res = r.get("result", {})
    bids = [{"price": float(b[0]), "size": float(b[1])} for b in res.get("b", [])]
    asks = [{"price": float(a[0]), "size": float(a[1])} for a in res.get("a", [])]
    # Best bid/ask + spread
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    spread_pct = (spread / best_bid * 100) if (spread and best_bid) else None
    return {
        "symbol": symbol,
        "category": category,
        "ts": res.get("ts"),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
        "bid_depth": sum(b["size"] for b in bids),
        "ask_depth": sum(a["size"] for a in asks),
        "bids": bids[:10],  # top 10 per non gonfiare context
        "asks": asks[:10],
        "levels_returned": len(bids) + len(asks),
    }


# ============================================================
# Phase C — Write tools (place_order, cancel_order, set_trading_stop)
# Safety: hard whitelist symbols, force require BYBIT_DEMO=true unless ALLOW_LIVE=true.
# ============================================================

ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "DOGEUSDT", "XRPUSDT"}
MAX_ORDER_QTY_USDT = float(os.environ.get("ANJA_BYBIT_MAX_ORDER_USDT", "5000"))  # safety cap
ALLOW_LIVE = os.environ.get("ANJA_BYBIT_ALLOW_LIVE", "false").lower() == "true"


def _safety_precheck() -> dict:
    """Verifica safety condizioni prima di permettere un write call."""
    if not IS_DEMO and not ALLOW_LIVE:
        return {"error": "REFUSED: write tools richiedono BYBIT_DEMO=true OPPURE ANJA_BYBIT_ALLOW_LIVE=true (opt-in esplicito per live trading)"}
    return {}


def tool_place_order(args: dict) -> dict:
    """POST /v5/order/create — apri posizione futures.

    args:
      symbol: BTCUSDT (whitelisted)
      side: Buy|Sell
      qty: stringa numerica (es. "0.012")
      category: linear (default) | spot | inverse
      order_type: Market (default) | Limit
      price: required se Limit
      stop_loss: opzionale (numero)
      take_profit: opzionale
      time_in_force: GTC (default)
      reduce_only: false (default)
    """
    err = _safety_precheck()
    if err:
        return err
    symbol = (args.get("symbol") or "").upper().strip()
    if not symbol:
        return {"error": "symbol required"}
    if symbol not in ALLOWED_SYMBOLS:
        return {"error": f"symbol '{symbol}' not in whitelist {sorted(ALLOWED_SYMBOLS)}"}
    side = (args.get("side") or "").capitalize()
    if side not in ("Buy", "Sell"):
        return {"error": "side must be Buy or Sell"}
    qty = str(args.get("qty") or "").strip()
    if not qty:
        return {"error": "qty required"}
    try:
        if float(qty) <= 0:
            return {"error": "qty must be positive"}
    except Exception:
        return {"error": "qty not numeric"}
    category = (args.get("category") or "linear").lower()
    if category not in ("linear", "spot", "inverse"):
        return {"error": f"unsupported category: {category}"}
    order_type = args.get("order_type") or "Market"
    body = {
        "category": category,
        "symbol": symbol,
        "side": side,
        "orderType": order_type,
        "qty": qty,
        "timeInForce": args.get("time_in_force") or "GTC",
    }
    if order_type == "Limit":
        price = args.get("price")
        if not price:
            return {"error": "price required for Limit order"}
        body["price"] = str(price)
    if args.get("stop_loss"):
        body["stopLoss"] = str(args["stop_loss"])
    if args.get("take_profit"):
        body["takeProfit"] = str(args["take_profit"])
    if args.get("reduce_only"):
        body["reduceOnly"] = True
    r = _http_post("/v5/order/create", body)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    res = r.get("result", {})
    return {
        "ok": True,
        "order_id": res.get("orderId"),
        "order_link_id": res.get("orderLinkId"),
        "symbol": symbol, "side": side, "qty": qty,
        "is_demo": IS_DEMO,
        "raw": res,
    }


def tool_cancel_order(args: dict) -> dict:
    """POST /v5/order/cancel — cancella ordine pending.

    args: symbol, category, order_id (or order_link_id)
    """
    err = _safety_precheck()
    if err:
        return err
    symbol = (args.get("symbol") or "").upper().strip()
    if symbol not in ALLOWED_SYMBOLS:
        return {"error": f"symbol '{symbol}' not in whitelist"}
    category = (args.get("category") or "linear").lower()
    order_id = args.get("order_id")
    order_link_id = args.get("order_link_id")
    if not order_id and not order_link_id:
        return {"error": "order_id or order_link_id required"}
    body = {"category": category, "symbol": symbol}
    if order_id:
        body["orderId"] = order_id
    if order_link_id:
        body["orderLinkId"] = order_link_id
    r = _http_post("/v5/order/cancel", body)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    return {"ok": True, "result": r.get("result", {}), "is_demo": IS_DEMO}


def tool_set_trading_stop(args: dict) -> dict:
    """POST /v5/position/trading-stop — setta/modifica SL/TP/trailing su posizione aperta.

    args: symbol, category, stop_loss?, take_profit?, trailing_stop?, position_idx? (default 0 one-way)
    """
    err = _safety_precheck()
    if err:
        return err
    symbol = (args.get("symbol") or "").upper().strip()
    if symbol not in ALLOWED_SYMBOLS:
        return {"error": f"symbol '{symbol}' not in whitelist"}
    category = (args.get("category") or "linear").lower()
    body = {
        "category": category,
        "symbol": symbol,
        "positionIdx": int(args.get("position_idx", 0)),
    }
    if args.get("stop_loss") is not None:
        body["stopLoss"] = str(args["stop_loss"])
    if args.get("take_profit") is not None:
        body["takeProfit"] = str(args["take_profit"])
    if args.get("trailing_stop") is not None:
        body["trailingStop"] = str(args["trailing_stop"])
    if not any(k in body for k in ("stopLoss", "takeProfit", "trailingStop")):
        return {"error": "at least one of stop_loss / take_profit / trailing_stop required"}
    r = _http_post("/v5/position/trading-stop", body)
    if r.get("retCode") != 0:
        return {"error": r.get("retMsg", "API error"), "raw": r}
    return {"ok": True, "result": r.get("result", {}), "is_demo": IS_DEMO}


# ============================================================
# tool registry
# ============================================================

TOOLS = [
    {
        "name": "bybit.ticker",
        "description": "Prezzo + 24h stats di un simbolo Bybit (es. BTCUSDT, ETHUSDT). Category: linear (perp USDT, default), spot, inverse.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Simbolo (es. BTCUSDT)"},
                "category": {"type": "string", "enum": ["linear", "spot", "inverse"], "description": "linear=perp USDT default"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "bybit.balance",
        "description": "Wallet balance dell'account. Default UNIFIED. Filtra per coin opzionale.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_type": {"type": "string", "enum": ["UNIFIED", "CONTRACT", "SPOT"], "description": "UNIFIED default"},
                "coin": {"type": "string", "description": "Filtra per coin (es. USDT, BTC)"},
            },
        },
    },
    {
        "name": "bybit.positions",
        "description": "Posizioni aperte (size != 0). Default category linear + settleCoin USDT.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["linear", "inverse"], "description": "linear=perp USDT default"},
                "symbol": {"type": "string", "description": "Opzionale filtra per simbolo"},
                "settle_coin": {"type": "string", "description": "Default USDT"},
            },
        },
    },
    {
        "name": "bybit.klines",
        "description": "Candele OHLCV per analisi tecnica. Interval: 1,3,5,15,30,60,120,240,360,720,D,W,M (minuti o lettere).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "interval": {"type": "string", "description": "Es. '60' (1h), '240' (4h), 'D' (giornaliero)"},
                "limit": {"type": "integer", "description": "Max 200, default 50"},
                "category": {"type": "string", "description": "linear default"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "bybit.recent_trades",
        "description": "Ultimi trade pubblici di un simbolo. Per sentiment veloce / orderflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "description": "Max 60, default 20"},
                "category": {"type": "string", "description": "linear default"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "bybit.orderbook",
        "description": "Orderbook (bid/ask depth) di un simbolo. Ritorna best bid/ask + spread + top 10 livelli per ogni lato + depth totale.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "description": "Numero livelli per side, default 25"},
                "category": {"type": "string", "enum": ["linear", "spot", "inverse"]},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "bybit.closed_pnl",
        "description": "Storico P/L realizzato (posizioni chiuse). Ritorna trade chiusi + total_pnl aggregato. Usa per 'quanto ho guadagnato/perso', 'P/L del mese'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Opzionale, filtra per simbolo"},
                "limit": {"type": "integer", "description": "Max 50, default 20"},
                "category": {"type": "string", "enum": ["linear", "inverse"]},
            },
        },
    },
    {
        "name": "bybit.order_history",
        "description": "Storico ordini (filled/cancelled/rejected). Filtra opzionale per symbol o order_status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "order_status": {"type": "string", "description": "Filled, Cancelled, Rejected, ..."},
                "limit": {"type": "integer", "description": "Max 50, default 20"},
                "category": {"type": "string", "enum": ["linear", "spot", "inverse"]},
            },
        },
    },
    {
        "name": "bybit.funding_history",
        "description": "Funding rate storico per un perp. Ritorna stats (avg/max/min/current). Usa per analisi sentiment perp / cost of carry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "limit": {"type": "integer", "description": "Max 200, default 20"},
                "category": {"type": "string", "enum": ["linear", "inverse"]},
            },
            "required": ["symbol"],
        },
    },
    # Phase C — Write tools
    {
        "name": "bybit.place_order",
        "description": ("Apri/piazza un ordine futures (linear). "
                        "USA SOLO se autorizzato esplicitamente (autonomy L3 o pending action approved). "
                        f"Safety: symbols whitelist={sorted(ALLOWED_SYMBOLS)}, BYBIT_DEMO required. "
                        "Necessario stop_loss = best practice."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol":   {"type": "string", "description": "es. BTCUSDT"},
                "side":     {"type": "string", "enum": ["Buy", "Sell"]},
                "qty":      {"type": "string", "description": "quantità (string per precisione, es '0.012')"},
                "category": {"type": "string", "default": "linear"},
                "order_type": {"type": "string", "enum": ["Market", "Limit"], "default": "Market"},
                "price":    {"type": "string", "description": "richiesto se Limit"},
                "stop_loss": {"type": "string", "description": "SL (raccomandato sempre)"},
                "take_profit": {"type": "string"},
                "time_in_force": {"type": "string", "enum": ["GTC","IOC","FOK"], "default": "GTC"},
                "reduce_only": {"type": "boolean", "default": False},
            },
            "required": ["symbol", "side", "qty"],
        },
    },
    {
        "name": "bybit.cancel_order",
        "description": "Cancella ordine pending. Args: symbol, order_id (o order_link_id), category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol":    {"type": "string"},
                "category":  {"type": "string", "default": "linear"},
                "order_id":  {"type": "string"},
                "order_link_id": {"type": "string"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "bybit.set_trading_stop",
        "description": "Modifica SL/TP/trailing su posizione aperta. Whitelisted symbols only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol":      {"type": "string"},
                "category":    {"type": "string", "default": "linear"},
                "stop_loss":   {"type": "string"},
                "take_profit": {"type": "string"},
                "trailing_stop": {"type": "string"},
                "position_idx": {"type": "integer", "default": 0},
            },
            "required": ["symbol"],
        },
    },
]

TOOL_HANDLERS = {
    "bybit.ticker": tool_ticker,
    "bybit.balance": tool_balance,
    "bybit.positions": tool_positions,
    "bybit.klines": tool_klines,
    "bybit.recent_trades": tool_recent_trades,
    "bybit.orderbook": tool_orderbook,
    "bybit.closed_pnl": tool_closed_pnl,
    "bybit.order_history": tool_order_history,
    "bybit.funding_history": tool_funding_history,
    # Phase C — Write
    "bybit.place_order": tool_place_order,
    "bybit.cancel_order": tool_cancel_order,
    "bybit.set_trading_stop": tool_set_trading_stop,
}


# ============================================================
# JSON-RPC dispatcher
# ============================================================

def handle_request(req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    req_id = req.get("id")
    if method == "initialize":
        return _ok(req_id, {
            "protocolVersion": PROTO_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        })
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(req_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            return _err(req_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(args)
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
            return _ok(req_id, {"content": content, "isError": "error" in result})
        except Exception as e:
            return _err(req_id, -32603, f"tool failed: {type(e).__name__}: {e}")
    if method == "ping":
        return _ok(req_id, {})
    return _err(req_id, -32601, f"method not found: {method}")


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def main():
    mode = "DEMO" if IS_DEMO else "LIVE"
    has_keys = "✓" if (API_KEY and API_SECRET) else "✗"
    print(f"[anja_bybit_lite] starting (mode={mode} keys={has_keys})", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = _err(None, -32700, f"parse error: {e}")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = handle_request(req)
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
