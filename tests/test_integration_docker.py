"""Интеграционные тесты с настоящим docker. Запуск: uv run pytest -m docker -v"""

import secrets

import pytest

from offpack.compose import Compose, sandbox_assets

pytestmark = pytest.mark.docker


def test_stack_isolation():
    with sandbox_assets() as assets:
        compose = Compose(f"offpack-it-{secrets.token_hex(3)}", assets)
        try:
            compose.up()
            compose.wait_ready(timeout=180)
            via_proxy = compose.exec("sandbox", ["npm", "ping"], timeout=120)
            assert via_proxy.returncode == 0, via_proxy.stderr
            direct = compose.exec(
                "sandbox",
                ["node", "-e", "fetch('https://registry.npmjs.org/').then("
                 "() => process.exit(0), () => process.exit(1))"],
                timeout=60,
            )
            assert direct.returncode != 0, "у песочницы не должно быть прямого выхода в интернет"
        finally:
            compose.down()
