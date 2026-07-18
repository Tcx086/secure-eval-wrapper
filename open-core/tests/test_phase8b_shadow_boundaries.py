from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository
from secure_eval_wrapper.live.shadow_runtime import (
    FixtureShadowMarketSource,
    ShadowAssuranceRuntime,
    ShadowAuthorityError,
)
from phase8b_shadow_test_support import TEST_REPOSITORY_SHA
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity


class _SubmitObject:
    def submit_order(self):
        raise AssertionError


class _CancelObject:
    def cancel_order(self):
        raise AssertionError


class _CallableTransport:
    def __call__(self):
        raise AssertionError


class _ArbitraryEndpointTransport:
    def request_endpoint(self):
        raise AssertionError


class Phase8BShadowBoundaryTests(unittest.TestCase):
    def construct(self, source):
        return ShadowAssuranceRuntime(
            repository=MemoryShadowRepository(),
            market_source=source,
            identity_resolver=lambda: RuntimeRepositoryIdentity(
                TEST_REPOSITORY_SHA, "git_checkout"
            ),
        )

    def test_write_capable_and_arbitrary_dependencies_are_rejected_at_construction(self):
        for dependency in (
            _SubmitObject(),
            _CancelObject(),
            _CallableTransport(),
            _ArbitraryEndpointTransport(),
            object(),
        ):
            with self.subTest(dependency=type(dependency).__name__):
                with self.assertRaises(ShadowAuthorityError):
                    self.construct(dependency)

    def test_production_broker_and_authenticated_adapter_types_are_not_injectable(self):
        from secure_eval_wrapper.live.broker import GuardedLiveBroker
        from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter

        for dependency in (GuardedLiveBroker, OkxProductionSpotAdapter):
            with self.assertRaises(ShadowAuthorityError):
                self.construct(dependency)

    def test_shadow_runtime_import_graph_excludes_write_and_authenticated_modules(self):
        module = Path(inspect.getsourcefile(ShadowAssuranceRuntime))
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        forbidden = (".broker", ".venue", ".credentials", ".readonly_preflight")
        self.assertFalse(any(name.endswith(forbidden) for name in imports))

    def test_fixture_source_has_no_generic_transport_or_write_symbols(self):
        source = FixtureShadowMarketSource()
        for name in (
            "send",
            "submit_order",
            "cancel_order",
            "request_endpoint",
            "withdraw",
            "transfer",
            "borrow",
        ):
            self.assertFalse(hasattr(source, name), name)

    def test_repository_dependency_is_exact_type_only(self):
        class ForgedRepository(MemoryShadowRepository):
            pass

        with self.assertRaises(ShadowAuthorityError):
            ShadowAssuranceRuntime(
                repository=ForgedRepository(),
                market_source=FixtureShadowMarketSource(),
            )


if __name__ == "__main__":
    unittest.main()
