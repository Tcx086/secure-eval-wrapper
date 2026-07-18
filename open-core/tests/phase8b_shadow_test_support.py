from __future__ import annotations

from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository, ShadowMemoryStore
from secure_eval_wrapper.live.shadow_runtime import FixtureShadowMarketSource, ShadowAssuranceRuntime


TEST_REPOSITORY_SHA = "a" * 40


def runtime(*, store: ShadowMemoryStore | None = None):
    repository = MemoryShadowRepository(store)
    return ShadowAssuranceRuntime(
        repository=repository,
        market_source=FixtureShadowMarketSource(),
        identity_resolver=lambda: RuntimeRepositoryIdentity(TEST_REPOSITORY_SHA, "git_checkout"),
    )
