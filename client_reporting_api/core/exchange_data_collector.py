"""Exchange data collector — pulls live data from OKX and Binance via CCXT.

Uses read-only API keys from Secret Manager to fetch:
- Account balances (spot + futures)
- Open positions with unrealized P&L
- Trade/fill history
- Deposit/withdrawal history (for transfer detection)

Shard-level failure isolation: per-client errors are logged, never raised.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import ccxt
from unified_api_contracts.internal import (
    AssetBalanceSummary,
    HourlyBalanceSnapshot,
    SnapshotSource,
    TradeRecord,
    TradeSide,
    TradeType,
    TransferRecord,
)
from unified_api_contracts.internal.domain.client_reporting.balance_snapshot import (
    PositionSnapshot,
)

from .tranche_router import get_client_config, load_registry

logger = logging.getLogger(__name__)


def _create_exchange(
    venue: str,
    api_key: str,
    api_secret: str,
    passphrase: str = "",
) -> ccxt.Exchange:
    """Create a CCXT exchange instance for the given venue."""
    config: dict[str, str | bool] = {
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
    }
    if venue == "okx" and passphrase:
        config["password"] = passphrase

    if venue == "binance":
        config["options"] = {"defaultType": "swap"}
    exchange_classes: dict[str, type[ccxt.Exchange]] = {
        "binance": ccxt.binance,
        "okx": ccxt.okx,
    }
    exchange_cls = exchange_classes.get(venue)
    if exchange_cls is None:
        msg = f"Unsupported venue: {venue}"
        raise ValueError(msg)

    return exchange_cls(config)


class ExchangeDataCollector:
    """Collects account data from exchanges using CCXT.

    Each client has a dedicated exchange instance created from
    their API keys stored in Secret Manager.
    """

    def __init__(self, secrets: dict[str, dict[str, str]]) -> None:
        """Initialize with pre-fetched secrets.

        Args:
            secrets: Pre-fetched credentials keyed by secret name.
        """
        self._secrets = secrets
        self._exchanges: dict[str, ccxt.Exchange] = {}

    def _get_exchange(self, client_id: str) -> ccxt.Exchange | None:
        """Get or create a CCXT exchange for a client."""
        if client_id in self._exchanges:
            return self._exchanges[client_id]

        config = get_client_config(client_id)
        if config is None:
            logger.warning("Client %s not in registry", client_id)
            return None

        secret_name = config.get("secret_name", "")
        if not secret_name:
            logger.info("Client %s has no secret_name (manual tranche)", client_id)
            return None

        creds = self._secrets.get(secret_name)
        if creds is None:
            logger.warning("No credentials found for secret %s", secret_name)
            return None

        venue = config.get("venue", "")
        exchange = _create_exchange(
            venue=venue,
            api_key=creds.get("api_key", ""),
            api_secret=creds.get("api_secret", ""),
            passphrase=creds.get("passphrase", ""),
        )
        self._exchanges[client_id] = exchange
        return exchange

    # Stablecoins: 1:1 with USD, no ticker lookup needed
    _STABLECOINS = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "FDUSD", "USD"}

    @staticmethod
    def _get_usd_price(
        exchange: ccxt.Exchange,
        currency: str,
    ) -> float:
        """Get USD price for a currency via exchange ticker."""
        if currency in ExchangeDataCollector._STABLECOINS:
            return 1.0
        for quote in ["USDT", "USD", "USDC"]:
            symbol = f"{currency}/{quote}"
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = float(ticker.get("last", 0) or ticker.get("close", 0) or 0)
                if price > 0:
                    return price
            except ccxt.BaseError:
                continue
        return 0.0

    @staticmethod
    def _parse_balances(
        balance_raw: dict[str, dict[str, float | None]],
        exchange: ccxt.Exchange | None = None,
    ) -> tuple[Decimal, Decimal, Decimal, list[AssetBalanceSummary]]:
        """Extract asset balances from raw CCXT balance response.

        Converts non-stablecoin assets (BTC, ETH, etc.) to USD using live ticker prices.
        """
        total_info = cast(
            dict[str, float | None],
            balance_raw.get("total", {}),
        )
        free_raw = cast(
            dict[str, float | None],
            balance_raw.get("free", {}),
        )
        used_raw = cast(
            dict[str, float | None],
            balance_raw.get("used", {}),
        )

        skip = {"info", "free", "used", "total", "timestamp", "datetime"}
        total_usd = Decimal("0")
        free_usd = Decimal("0")
        locked_usd = Decimal("0")
        assets: list[AssetBalanceSummary] = []

        for cur, total_val in total_info.items():
            if cur in skip or total_val is None or float(total_val) == 0:
                continue
            fv = float(free_raw.get(cur, 0) or 0)
            uv = float(used_raw.get(cur, 0) or 0)
            tv = float(total_val)

            # Convert to USD: stablecoins 1:1, others via ticker
            if cur in ExchangeDataCollector._STABLECOINS:
                usd_price = 1.0
            elif exchange is not None:
                usd_price = ExchangeDataCollector._get_usd_price(exchange, cur)
            else:
                usd_price = 1.0  # Fallback if no exchange

            usd_val = Decimal(str(tv * usd_price))
            total_usd += usd_val
            free_usd += Decimal(str(fv * usd_price))
            locked_usd += Decimal(str(uv * usd_price))
            assets.append(
                AssetBalanceSummary(
                    currency=cur,
                    free=Decimal(str(fv)),
                    locked=Decimal(str(uv)),
                    total=Decimal(str(tv)),
                    usd_value=usd_val,
                )
            )
        return total_usd, free_usd, locked_usd, assets

    @staticmethod
    def _parse_positions(
        exchange: ccxt.Exchange,
    ) -> tuple[list[PositionSnapshot], Decimal]:
        """Fetch and parse open positions from exchange."""
        positions: list[PositionSnapshot] = []
        unrealized_pnl = Decimal("0")
        try:
            pos_raw: list[dict[str, str | float | None]] = exchange.fetch_positions()
            for pos in pos_raw:
                contracts = float(pos.get("contracts", 0) or 0)
                if contracts == 0:
                    continue
                upnl = float(pos.get("unrealizedPnl", 0) or 0)
                unrealized_pnl += Decimal(str(upnl))
                liq = pos.get("liquidationPrice")
                positions.append(
                    PositionSnapshot(
                        symbol=str(pos.get("symbol", "")),
                        side=str(pos.get("side", "long")),
                        quantity=Decimal(str(contracts)),
                        entry_price=Decimal(str(pos.get("entryPrice", 0) or 0)),
                        mark_price=Decimal(str(pos.get("markPrice", 0) or 0)),
                        unrealized_pnl=Decimal(str(upnl)),
                        leverage=Decimal(str(pos.get("leverage", 1) or 1)),
                        liquidation_price=Decimal(str(liq)) if liq else None,
                        notional_usd=Decimal(str(pos.get("notional", 0) or 0)),
                    )
                )
        except (ccxt.NotSupported, ccxt.BadRequest):
            pass
        return positions, unrealized_pnl

    def get_client_snapshot(
        self,
        client_id: str,
    ) -> HourlyBalanceSnapshot | None:
        """Fetch current balance snapshot for a client."""
        exchange = self._get_exchange(client_id)
        if exchange is None:
            return None

        config = get_client_config(client_id)
        if config is None:
            return None

        venue = config.get("venue", "unknown")

        try:
            balance_raw = exchange.fetch_balance()
            total_usd, free_usd, locked_usd, assets = self._parse_balances(balance_raw, exchange)
            positions, unrealized_pnl = self._parse_positions(exchange)

            return HourlyBalanceSnapshot(
                client_id=client_id,
                timestamp=datetime.now(tz=UTC),
                total_equity_usd=total_usd,
                free_balance_usd=free_usd,
                locked_balance_usd=locked_usd,
                unrealized_pnl_usd=unrealized_pnl,
                asset_balances=assets,
                positions=positions,
                source=SnapshotSource(
                    source_type="API_LIVE",
                    venue=venue,
                    account_type="unified",
                ),
            )
        except ccxt.BaseError as exc:
            logger.warning(
                "Failed to fetch snapshot for %s: %s",
                client_id,
                str(exc),
            )
            return None

    def _get_traded_symbols(
        self,
        exchange: ccxt.Exchange,
    ) -> list[str]:
        """Get symbols to query trades for.

        Both OKX and Binance require a symbol for reliable trade fetching.
        We discover symbols from open positions + core pairs.
        """
        symbols: set[str] = set()
        try:
            positions = exchange.fetch_positions()
            for pos in positions:
                sym = pos.get("symbol")
                if sym and float(pos.get("contracts", 0) or 0) != 0:
                    symbols.add(str(sym))
        except ccxt.BaseError:
            pass

        if exchange.id == "okx":
            # Also do a quick instType=SWAP scan
            try:
                recent = exchange.fetch_my_trades(
                    None,
                    limit=50,
                    params={"instType": "SWAP"},
                )
                for t in recent:
                    sym = t.get("symbol")
                    if sym:
                        symbols.add(str(sym))
            except ccxt.BaseError:
                pass

        # Always include core pairs for trade history
        for pair in ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"):
            symbols.add(pair)
        return list(symbols)

    @staticmethod
    def _parse_trade(
        trade: dict[str, str | float | None],
        client_id: str,
        venue: str,
    ) -> TradeRecord:
        """Parse a single CCXT trade into a TradeRecord."""
        ts_val = trade.get("timestamp")
        ts_ms = int(ts_val) if ts_val is not None else 0
        # CCXT `cost` = amount * contractSize * price for derivatives,
        # which is the correct USD notional. `amount` alone is in contracts.
        cost_val = Decimal(str(trade.get("cost", 0) or 0))
        amt = Decimal(str(trade.get("amount", 0) or 0))
        px = Decimal(str(trade.get("price", 0) or 0))
        notional = cost_val if cost_val else amt * px

        return TradeRecord(
            trade_id=str(trade.get("id", "")),  # noqa: qg-empty-fallback
            client_id=client_id,
            venue=venue,
            symbol=str(trade.get("symbol", "")),  # noqa: qg-empty-fallback
            side=TradeSide.BUY if trade.get("side") == "buy" else TradeSide.SELL,
            quantity=amt,
            price=px,
            fee=Decimal(str((trade.get("fee") or {}).get("cost", 0) or 0)),
            fee_currency=str((trade.get("fee") or {}).get("currency", "")),  # noqa: qg-empty-fallback
            realized_pnl=Decimal("0"),
            timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
            order_id=str(trade.get("order", "")),  # noqa: qg-empty-fallback
            trade_type=TradeType.MARKET,
            notional_usd=notional,
        )

    def get_client_trades(
        self,
        client_id: str,
        since_ms: int | None = None,
        limit: int = 500,
    ) -> list[TradeRecord]:
        """Fetch recent trades/fills for a client."""
        exchange = self._get_exchange(client_id)
        if exchange is None:
            return []

        symbols = self._get_traded_symbols(exchange)
        records: list[TradeRecord] = []
        venue = str(exchange.id)

        for sym in symbols:
            try:
                raw = exchange.fetch_my_trades(
                    symbol=sym if sym else None,
                    since=since_ms,
                    limit=limit,
                )
                for trade in raw:
                    records.append(self._parse_trade(trade, client_id, venue))
            except ccxt.BaseError as exc:
                logger.warning(
                    "Failed to fetch trades for %s/%s: %s",
                    client_id,
                    sym,
                    str(exc),
                )
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records[:limit]

    @staticmethod
    def _parse_transfer(
        raw: dict[str, str | float | None],
        client_id: str,
        venue: str,
        direction: str,
    ) -> TransferRecord:
        """Parse a single CCXT deposit/withdrawal into a TransferRecord."""
        ts_val = raw.get("timestamp")
        ts_ms = int(ts_val) if ts_val is not None else 0
        return TransferRecord(
            transfer_id=str(raw.get("id", "")),  # noqa: qg-empty-fallback
            client_id=client_id,
            venue=venue,
            direction=direction,
            currency=str(raw.get("currency", "")),  # noqa: qg-empty-fallback
            amount=Decimal(str(raw.get("amount", 0) or 0)),
            fee=Decimal(str((raw.get("fee") or {}).get("cost", 0) or 0)),
            status=str(raw.get("status", "ok")),
            timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
            tx_hash=str(raw.get("txid", "") or ""),  # noqa: qg-empty-fallback
            address=str(raw.get("address", "") or ""),  # noqa: qg-empty-fallback
            network=str(raw.get("network", "") or ""),  # noqa: qg-empty-fallback
        )

    def get_client_transfers(
        self,
        client_id: str,
        since_ms: int | None = None,
    ) -> list[TransferRecord]:
        """Fetch deposit/withdrawal history for transfer detection."""
        exchange = self._get_exchange(client_id)
        if exchange is None:
            return []

        records: list[TransferRecord] = []
        venue = str(exchange.id)

        try:
            deposits: list[dict[str, str | float | None]] = exchange.fetch_deposits(
                code=None, since=since_ms
            )
            for dep in deposits:
                records.append(self._parse_transfer(dep, client_id, venue, "deposit"))
        except ccxt.BaseError as exc:
            logger.warning("Failed to fetch deposits for %s: %s", client_id, str(exc))

        try:
            withdrawals: list[dict[str, str | float | None]] = exchange.fetch_withdrawals(
                code=None, since=since_ms
            )
            for wd in withdrawals:
                records.append(self._parse_transfer(wd, client_id, venue, "withdrawal"))
        except ccxt.BaseError as exc:
            logger.warning("Failed to fetch withdrawals for %s: %s", client_id, str(exc))

        return records

    def get_all_active_client_ids(self) -> list[str]:
        """Return all active managed client IDs from the registry."""
        registry = load_registry()
        clients = registry["clients"]
        return [
            cid
            for cid, cfg in clients.items()
            if cfg.get("is_active", False) and cfg.get("tranche") == "managed"
        ]
