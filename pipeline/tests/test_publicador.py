"""El publicador distingue «el token venció» de cualquier otro fallo de GitHub.

PASÓ: el token de /preciovivo/github-token venció el 18-sep-2026 y la tubería
falló dos meses en el último paso con un `RuntimeError` genérico, mientras el
dato fresco esperaba en S3. Ver docs/aws.md, «El token de GitHub vence».

Step Functions muestra el NOMBRE de la excepción como `error` de la ejecución,
así que el nombre es parte del contrato y se comprueba tal cual.
"""
from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parents[2] / "infra" / "lambda_publicar" / "publicar.py"


@pytest.fixture
def publicar(monkeypatch):
    monkeypatch.setenv("BUCKET_SNAPSHOT", "bucket-de-prueba")
    monkeypatch.setenv("PARAM_TOKEN_GITHUB", "/preciovivo/github-token")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("publicar_bajo_prueba", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _github_responde(monkeypatch, modulo, codigo: int, cuerpo: str) -> None:
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, codigo, "error", {},
                                     io.BytesIO(cuerpo.encode()))
    monkeypatch.setattr(modulo.urllib.request, "urlopen", urlopen)


def test_un_401_dice_que_hay_que_rotar_el_token(publicar, monkeypatch):
    _github_responde(monkeypatch, publicar, 401, '{"message": "Bad credentials"}')

    with pytest.raises(publicar.TokenGitHubRechazado) as info:
        publicar._api("token-vencido", "GET", "/repos/x/y/git/ref/heads/main")

    assert type(info.value).__name__ == "TokenGitHubRechazado"
    # Sigue siendo un RuntimeError: quien ya lo capturaba como tal no cambia.
    assert isinstance(info.value, RuntimeError)
    mensaje = str(info.value)
    assert "/preciovivo/github-token" in mensaje
    assert "put-parameter" in mensaje
    assert "Bad credentials" in mensaje


def test_otros_errores_de_github_no_se_confunden_con_el_token(publicar, monkeypatch):
    _github_responde(monkeypatch, publicar, 422, '{"message": "Update is not a fast forward"}')

    with pytest.raises(RuntimeError) as info:
        publicar._api("token-valido", "PATCH", "/repos/x/y/git/refs/heads/main")

    assert not isinstance(info.value, publicar.TokenGitHubRechazado)
    assert "422" in str(info.value)
