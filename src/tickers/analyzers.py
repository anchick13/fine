import sys
import logging
from .models import TickerAnalysisResult

class TickerAnalyzer:
    AVAILABLE_FUNCTIONS=["high", "low"]
    AVAILABLE_PERIODS= list(range(3, 29)) + list(range(30, 89, 5)) + list(range(90, 360, 30)) + list(range(365, 365*5, 365))

    def __init__(self, tickers):
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.tickers = tickers

    def high(self, tickers, start, period):
        max_high = 0
        end = start + period + 1
        if end >= len(tickers): return False
        ticker = tickers[start]

        for i in range(start + 1, end):
            if max_high < tickers[i].high:
                max_high = tickers[i].high

        self.logger.debug("== result max_high: {mh}, ticker: {t} ==".format(mh=max_high, t=ticker))
        return ticker.open < max_high and ticker.high > max_high

    def low(self, tickers, start, period):
        min_low = sys.maxsize
        end = start + period + 1
        if end >= len(tickers): return False
        ticker = tickers[start]

        for i in range(start + 1, end):
            if min_low > tickers[i].low:
                min_low = tickers[i].low

        self.logger.debug("== result min_low: {ml}, ticker: {t} ==".format(ml=min_low, t=ticker))
        return ticker.open > min_low and ticker.low < min_low

    def analyze(self, period, function):
        results = []
        periods = self.AVAILABLE_PERIODS if period is None else [period]
        functions = self.AVAILABLE_FUNCTIONS if function is None else [function]
        for stock in set(map(lambda t: t.stock, self.tickers)):
            tickers = sorted(filter(lambda t: t.stock == stock, self.tickers), key=lambda t: t.date, reverse=True)
            rtickers = list(reversed(tickers))
            results += [self.__analyze(tickers, rtickers, stock, p, f) for f in functions for p in periods]
        return results

    def __analyze(self, tickers, reverse, stock, period, function):
        result = TickerAnalysisResult(stock, reverse, period, function)

        self.logger.debug("== analyze input: tickers={t}, period={p}, function={f}  ==".format(t=len(tickers), p=period, f=function))
        for idx, ticker in enumerate(tickers):
            extreme = getattr(self, function)(tickers, idx, int(period))
            if not extreme and idx == 0:
                self.logger.debug("== {s} {p} {f} - no hit on {d} ==".format(s=stock, p=period, f=function, d=ticker.date))
                break
            if extreme:
                self.logger.debug("== added ticker with index={i} ==".format(i=idx))
                result.add_ticker(idx)

        self.logger.debug("== result count={c} ==".format(c=result.count))
        if not result.empty(): self.logger.info(result)
        return result
