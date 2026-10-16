"""Exchange routing; contracts/lot sizes are always resolved from Kite."""
MARKETS = {
    "NIFTY": {"spot": "NIFTY 50", "spot_exchange": "NSE", "derivative_exchange": "NFO"},
    "BANKNIFTY": {"spot": "NIFTY BANK", "spot_exchange": "NSE", "derivative_exchange": "NFO"},
    "SENSEX": {"spot": "SENSEX", "spot_exchange": "BSE", "derivative_exchange": "BFO"},
    "FINNIFTY": {"spot": "NIFTY FIN SERVICE", "spot_exchange": "NSE", "derivative_exchange": "NFO"},
    "MIDCPNIFTY": {"spot": "NIFTY MID SELECT", "spot_exchange": "NSE", "derivative_exchange": "NFO"},
}

def market(index):
    try:
        return MARKETS[index]
    except KeyError:
        raise ValueError(f"Unsupported index: {index}") from None
