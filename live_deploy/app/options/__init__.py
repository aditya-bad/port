"""
live_deploy — options resolution utilities.

Everything an options strategy needs to go from "what I mean in plain
English" (THIS_WEEK ATM leg, NEXT_WEEK ATM-10 CE, THIS_WEEK CE closest to
premium 40, ...) to a concrete, tradeable `OptionLeg` (tradingsymbol +
instrument_token + lot_size), using the SAME Kite session the dispatcher's
WebSocket is already authenticated with — no separate login.

This package is resolution-only. It never places a trade itself; a
strategy resolves an `OptionLeg` here and then calls the existing
`DeploymentRunner.buy()/sell()` with its tradingsymbol/instrument_token,
exactly like it would for any other instrument.

    from ..options import OptionsResolver

    resolver = OptionsResolver(runner.dispatcher)
    leg = await resolver.get_atm_leg("NIFTY", "THIS_WEEK", "CE")
    await runner.buy(leg.tradingsymbol, leg.instrument_token, leg.lot_size, price)

See resolver.py's module docstring for the full method list.
"""

from .models import OptionLeg
from .client import NoKiteSession, get_kite_connect
from .resolver import OptionsResolver, options_exchange_for

__all__ = ["OptionLeg", "NoKiteSession", "get_kite_connect", "OptionsResolver", "options_exchange_for"]
