"""semantic_analyzer 테스트 공용 설정. LLM 설정을 셸 환경에 의존하지 않게 고정한다."""
from pathlib import Path

import pytest

from modules.semantic_analyzer.llm.factory import ENV_CONFIG_PATH

PROVIDER_FAKE_CONFIG_PATH = Path(__file__).parent / "fixtures" / "configs" / "provider_fake.toml"


@pytest.fixture(autouse=True)
def isolated_llm_config(monkeypatch: pytest.MonkeyPatch) -> Path:
    """LLM 설정을 fake 픽스처로 고정한다. 파이프라인 준비로 셸에 SEMANTIC_ANALYZER_CONFIG_PATH를
    export한 채 전체 pytest를 돌려도, 테스트가 실제 모델을 부르거나 없는 파일로 깨지지 않게 한다.
    env를 자기 값으로 바꾸는 테스트는 같은 monkeypatch로 이 값을 덮어쓴다(끝나면 함께 복구)."""
    monkeypatch.setenv(ENV_CONFIG_PATH, str(PROVIDER_FAKE_CONFIG_PATH))
    return PROVIDER_FAKE_CONFIG_PATH
