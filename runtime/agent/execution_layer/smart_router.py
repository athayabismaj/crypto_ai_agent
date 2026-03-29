"""
Smart Router Exchange
Memilih exchange terbaik sesuai dengan limit bandwidth dan best bid/ask
"""

from runtime.agent.execution_layer.exchange import BaseExchange, OrderRequest


class SmartRouter:
    def __init__(
        self,
        exchanges: dict[str, BaseExchange],  # {'binance': BinanceExchange()}
        strategy: str = "primary_only",
        primary: str = "binance",
    ):
        self._exchanges = exchanges
        self._strategy = strategy
        self._primary = primary

    async def route(self, order: OrderRequest) -> tuple[BaseExchange, str]:
        """
        Return (exchange_to_use, exchange_name).
        Bekerja sebagai Switch layer penyedia.
        """
        # Di versi saat ini sistem sangat depend pada Binance
        # Load balancing best_price/failover siap ditambahkan
        # jika user sudah mendaftarkan konektor ke OKX/Bybit/Kucoin

        if self._strategy == "primary_only":
            return self._exchanges[self._primary], self._primary

        if self._strategy == "best_price":
            # return await self._find_best_price(order)
            pass

        if self._strategy == "failover":
            # return await self._failover_route(order)
            pass

        # Fallback
        return self._exchanges[self._primary], self._primary
