import os

# "secure"    -> 모든 접근통제가 올바르게 구현된 버전 (오탐률 측정용)
# "vulnerable"-> 의도적으로 주입된 취약점이 활성화된 버전 (탐지율 측정용)
#
# ground_truth/vulnerabilities.json 에 각 취약점이 어느 모드에서 활성화되는지
# 정의되어 있음. 코드 내 "# [VULN:Vx]" 주석으로 위치를 표시해 둠.
VULN_MODE = os.getenv("VULN_MODE", "secure")

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./testapp.db")

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "Admin1234!"

# 쿠키는 포트가 아닌 호스트 기준으로 공유되므로, 같은 호스트에서 두 인스턴스를
# 동시에 띄우면 쿠키 이름이 같을 때 서로 덮어쓴다. 모드별로 이름을 분리한다.
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", f"session_{VULN_MODE}")
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE", str(14 * 24 * 60 * 60)))  # 초, 기본 14일
