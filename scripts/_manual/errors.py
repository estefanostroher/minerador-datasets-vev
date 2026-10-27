"""Allowlisted diagnostics: never render exception messages, paths or file contents."""

SAFE_HINTS = {
    "checkpoint_discovery_not_found": (
        "Nenhum checkpoint de descoberta válido neste contexto. Execute "
        ".\\scripts\\try_discovery.ps1 ou informe -InputJson e o -OutputDirectory correspondente."
    ),
    "checkpoint_screening_not_found": (
        "Nenhum checkpoint de triagem válido neste contexto. Execute "
        ".\\scripts\\try_screening.ps1 ou informe -InputJson e o -OutputDirectory correspondente."
    ),
    "github_token_missing": (
        "GITHUB_TOKEN ausente para acesso ao GitHub. Configure a variável do processo "
        "ou a chave nos arquivos dotenv autorizados; prompt oculto apenas em terminal interativo."
    ),
    "github_token_invalid": (
        "GITHUB_TOKEN: declaração vazia ou malformada no arquivo dotenv autorizado. "
        "Corrija a declaração ou configure a variável do processo."
    ),
}


class ManualInputError(ValueError):
    code = "invalid_manual_input"


class CheckpointNotFound(ManualInputError):
    def __init__(self, stage):
        if stage not in {"discovery", "screening"}:
            raise ValueError("unsupported prerequisite stage")
        self.code = "checkpoint_" + stage + "_not_found"
        super().__init__("manual prerequisite unavailable")


class MissingGitHubToken(ManualInputError):
    code = "github_token_missing"


class InvalidGitHubToken(ManualInputError):
    code = "github_token_invalid"
