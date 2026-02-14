"""Run P0 Hotfix tests"""
import subprocess
import sys
import os

os.chdir(r"C:\Users\ghilp\OneDrive\바탕 화면\배성무일반\0_디플런트 D!FFERENT\Decisionwise\decisionwise_api_platform\dpp")

result = subprocess.run(
    [sys.executable, "-m", "pytest", "apps/api/tests/unit/test_p0_hotfix.py", "-v", "--tb=short"],
    capture_output=True,
    text=True
)

print(result.stdout)
if result.stderr:
    for line in result.stderr.split('\n'):
        if not line.startswith('{"timestamp"'):
            print(line, file=sys.stderr)

sys.exit(result.returncode)
