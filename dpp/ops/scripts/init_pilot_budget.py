"""Pilot 테넌트 예산 초기화 스크립트 (pod 내부에서 실행)"""
import os
from dpp_api.db.redis_client import RedisClient
from dpp_api.db.engine import build_engine, build_sessionmaker
from dpp_api.budget import BudgetManager

TENANT_ID = "tenant_test_001"
CREDIT_USD_MICROS = 10_000_000  # $10.00

engine = build_engine(os.environ["DATABASE_URL"])
db = build_sessionmaker(engine)()
redis = RedisClient.get_client()
bm = BudgetManager(redis, db)

current = bm.get_balance(TENANT_ID)
print(f"현재 잔액: ${current / 1_000_000:.2f}")

if current == 0:
    bm.set_balance(TENANT_ID, CREDIT_USD_MICROS)
    print(f"[OK] ${CREDIT_USD_MICROS / 1_000_000:.2f} 크레딧 추가 완료")
else:
    print(f"[SKIP] 잔액이 이미 있습니다 — 추가하지 않음")

db.close()
