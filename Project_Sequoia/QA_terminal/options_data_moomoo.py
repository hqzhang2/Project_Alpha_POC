"""
Moomoo data provider — STUB (toggle infra, mapping lands later).

The provider-selection seam (options_data.get_provider / provider_status) is live;
the actual Moomoo mapping is deliberately NOT built until the OpenD gateway is
running and the subscription check passes (openapi.moomoo.com -> OpenD app ->
localhost:11111, then `pip install moomoo` on CLT py3.9).

Until then: IMPLEMENTED = False, so get_provider('moomoo') raises
ProviderUnavailableError and the screener UI shows "Moomoo (unavailable)".
"""
from options_data import OptionDataProvider, ProviderUnavailableError

_REASON = ("Moomoo mapping not built yet - requires OpenD gateway (openapi.moomoo.com, "
           "localhost:11111) + moomoo SDK; build after the live subscription check")


class MoomooProvider(OptionDataProvider):
    name = "moomoo"
    IMPLEMENTED = False
    UNAVAILABLE_REASON = _REASON

    def get_expirations(self, ticker):
        raise ProviderUnavailableError(_REASON)

    def get_chain(self, ticker, expiry=None):
        raise ProviderUnavailableError(_REASON)

    def get_next_earnings(self, ticker):
        raise ProviderUnavailableError(_REASON)

    def get_underlying_oi_history(self, ticker):
        raise ProviderUnavailableError(_REASON)
