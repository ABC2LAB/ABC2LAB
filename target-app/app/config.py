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
