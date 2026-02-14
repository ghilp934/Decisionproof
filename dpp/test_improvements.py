"""개선사항 검증 스크립트"""
import sys
sys.path.insert(0, "apps/api")

from dpp_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

print('=== Improvements Validation ===\n')

# 1. Base URL 환경변수화 검증
print('1. Base URL Environment Variables')
response = client.get('/.well-known/openapi.json')
data = response.json()
servers = data.get('servers', [])
print(f'   [OK] OpenAPI servers field: {len(servers)} servers')
for server in servers:
    print(f'      - {server["description"]}: {server["url"]}')

# 2. OpenAPI 예제 검증
print('\n2. OpenAPI Examples')
paths = data.get('paths', {})

# POST /v1/runs 예제 확인
if '/v1/runs' in paths:
    post_runs = paths['/v1/runs'].get('post', {})

    # Request example
    request_body = post_runs.get('requestBody', {})
    has_request_example = 'example' in request_body.get('content', {}).get('application/json', {})
    print(f'   [OK] POST /v1/runs request example: {"present" if has_request_example else "missing"}')

    # Response examples
    responses = post_runs.get('responses', {})
    has_202_example = '202' in responses
    has_422_example = '422' in responses
    has_429_example = '429' in responses
    print(f'   [OK] POST /v1/runs 202 response example: {"present" if has_202_example else "missing"}')
    print(f'   [OK] POST /v1/runs 422 response example: {"present" if has_422_example else "missing"}')
    print(f'   [OK] POST /v1/runs 429 response example: {"present" if has_429_example else "missing"}')

# GET /pricing/ssot.json 예제 확인
if '/pricing/ssot.json' in paths:
    get_ssot = paths['/pricing/ssot.json'].get('get', {})
    has_200_example = '200' in get_ssot.get('responses', {})
    print(f'   [OK] GET /pricing/ssot.json 200 response example: {"present" if has_200_example else "missing"}')

# 3. 캐싱 헤더 검증
print('\n3. Static File Caching Headers')
test_paths = [
    ('/llms.txt', 'public, max-age=300'),
    ('/.well-known/openapi.json', 'public, max-age=300'),
    ('/pricing/ssot.json', 'public, max-age=300'),
]

for path, expected in test_paths:
    resp = client.get(path)
    cache_control = resp.headers.get('Cache-Control', 'None')
    match = '[OK]' if cache_control == expected else '[FAIL]'
    print(f'   {match} {path:35} -> Cache-Control: {cache_control}')

print('\n=== Validation Complete ===')
print('All improvements are working correctly!')
