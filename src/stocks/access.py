from datetime import timedelta, datetime
import csv
import concurrent.futures
from botocore.exceptions import ClientError
import pandas as pd
import yfinance as yf

from util import DATE_FORMAT
from .symbols import US_SYMBOLS
from .models import Ticker, Stock

class StockDataAccess:
    def load_all(self):
        return map(lambda s: Stock(s, None, None, None, None), US_SYMBOLS)

    def load_one(self, symbol):
        return Stock(symbol, None, None, None, None)

class TickerDataAccess:
    DIR = "tickers"

    def __init__(self, storage, logger):
        self.storage = storage
        self.logger = logger
        self.symbols = {}

    def update(self, symbols, type, period):
        requested = len(symbols)
        self.logger.info(f"requested symbols {requested}")

        if type == Ticker.Type.OPTIONS:
            updated_symbols = self.__update_options(symbols)
        else:
            updated_symbols = self.__update_symbols(symbols, type, period)

        updated = len(updated_symbols)
        self.logger.info(f"updated symbols {updated}")
        self.__reduce_updated(updated_symbols, type, requested-updated)

    def __update_options(self, symbols):
        data = yf.Tickers(" ".join(symbols))
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            [executor.submit(self.__update_single_option, symbol, getattr(data, symbol)) for symbol in symbols]
        return symbols

    def __update_single_option(self, symbol, obj):
        # update dividends
        self.storage.put(f"{self.DIR}/{symbol}/div.csv", obj.dividends.to_csv(header=True))
        self.logger.info(f"updated dividends for {symbol}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            [executor.submit(self.__write_single_option_spread, symbol, expiration, obj.option_chain(expiration)) for expiration in obj.options]

    def __write_single_option_spread(self, symbol, exp, data):
        self.storage.put(f"{self.DIR}/{symbol}/opts/calls/{datetime.now().strftime(DATE_FORMAT)}_{exp}.csv", data.calls.to_csv())
        self.storage.put(f"{self.DIR}/{symbol}/opts/puts/{datetime.now().strftime(DATE_FORMAT)}_{exp}.csv", data.puts.to_csv())
        self.logger.info(f"updated options for {symbol}")

    def __update_symbols(self, symbols, type, period):
        df = self.__yfin_download(symbols, type, period)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.__write_single_symbol, symbol, type, period, df[symbol] if len(symbols) > 1 else df): symbol for symbol in symbols}
            for future in concurrent.futures.as_completed(futures):
                symbol = futures[future]
                if not future.result(): symbols.remove(symbol)
        return symbols

    def __write_single_symbol(self, symbol, type, period, data):
        # process 1d data as single files.
        if type == Ticker.Type.ONE_DAY:
            if self.__valid_data(data):
                self.storage.put(self.__ticker_filename(symbol, type), data.to_csv())
                self.logger.info(f"updated {symbol} for {type}")
                return True
            else: return False
        else:
            error_count = 0
            if data.empty: return False # remove non-loaded symbols
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(self.__write_single_date_key, symbol, type, days, data) for days in range(0, period)]
                for future in concurrent.futures.as_completed(futures):
                    if not future.result(): error_count+=1
            # consider 20% missing data valid
            self.logger.info(f"{symbol}: errors {error_count} for period {period}")
            return error_count / period < 0.2

    def __write_single_date_key(self, symbol, type, days, data):
        key = (datetime.now() - timedelta(days=days)).strftime(DATE_FORMAT)
        segment = data.loc[key:key]
        if segment.empty: return True # skip weekends
        if self.__valid_data(segment):
            self.storage.put(self.__ticker_filename(symbol, type, key), segment.to_csv())
            self.logger.info(f"updated {symbol} for {type} and date {key}")
            return True
        else: return False

    def __valid_data(self, df):
        if not len(df): return False
        if (not df.iloc[0].Low) or pd.isna(df.iloc[0].Low): return False
        if (not df.iloc[0].High) or pd.isna(df.iloc[0].High): return False
        if (not df.iloc[0].Open) or pd.isna(df.iloc[0].Open): return False
        if (not df.iloc[0].Close) or pd.isna(df.iloc[0].Close): return False
        if (not df.iloc[0].Volume) or pd.isna(df.iloc[0].Volume): return False
        return True

    def load(self, symbol, type, date=None):
        filename = self.__ticker_filename(self, symbol, type, date)
        reader = csv.reader(self.storage.get(filename), delimiter=',')
        next(reader, None)  # skip the headers
        return [Ticker(type, symbol, datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S'), row[1], row[2], row[3], row[4], row[5]) for row in reader]

    def symbols2update(self, type, limit):
        file = self.__update_filename(type)

        try:
            sdata = self.storage.get(file)
        except ClientError:
            # if file doesn't exist for a given type and day, create it with all stocks.
            sdata = ",".join(US_SYMBOLS)
            self.storage.put(file, sdata)

        self.symbols[type] = sdata.strip().split(",") if sdata else []
        self.logger.info(f"{type} total: {len(self.symbols[type])}")

        return self.symbols[type][:limit]

    def __reduce_updated(self, symbols, type, offset):
        if not type in self.symbols: return
        self.symbols[type] = [s for s in self.symbols[type] if s not in symbols]
        for i in range(0, offset): self.symbols[type].append(self.symbols[type].pop(0))
        sdata = ",".join(self.symbols[type]) if self.symbols[type] else ''
        self.storage.put(self.__update_filename(type), sdata)

    def __update_filename(self, type):
        return f"{self.DIR}/{type}.{datetime.now().strftime(DATE_FORMAT)}"

    def __ticker_filename(self, symbol, type, date=None):
        if type == Ticker.Type.ONE_DAY:
            return f"{self.DIR}/{symbol}/{type}.csv"
        else:
            return f"{self.DIR}/{symbol}/{type}/{date}.csv"

    def __yfin_download(self, symbols, type, period):
        return yf.download(
            # tickers list or string as well
            tickers = " ".join(symbols),

            # use "period" instead of start/end
            # valid periods: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max
            # (optional, default is '1mo')
            period = period if period == "max" else f"{period}d",

            # fetch data by interval (including intraday if period < 60 days)
            # valid intervals: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
            # (optional, default is '1d')
            interval = type,

            # group by ticker (to access via data['SPY'])
            # (optional, default is 'column')
            group_by = 'ticker',

            # adjust all OHLC automatically
            # (optional, default is False)
            auto_adjust = True,

            # download pre/post regular market hours data
            # (optional, default is False)
            # prepost = True,

            # use threads for mass downloading? (True/False/Integer)
            # (optional, default is True)
            treads = True,

            # proxy URL scheme use use when downloading?
            # (optional, default is None)
            proxy = None
        )
