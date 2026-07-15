"""Transport-free provider implementation identity constants."""
from secure_eval_wrapper.data_collection.hashing import sha256_payload


OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH = sha256_payload(
    {
        "adapter": "okx-production-spot",
        "version": 5,
        "writes": "phase8b-unreachable",
        "authenticated_readonly_preflight": "exact-six-gets-unparameterized-positions",
    }
)


__all__ = ["OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH"]
