"""llms.txt 링크 무결성 검증 스크립트"""
import sys
sys.path.insert(0, "apps/api")

from dpp_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

print('=== llms.txt 링크 무결성 검증 ===\n')

# llms.txt 파싱
response = client.get('/llms.txt')
content = response.text

# 링크 추출
links = []
for line in content.split('\n'):
    if ': /' in line and not line.strip().startswith('#'):
        parts = line.split(': /', 1)
        if len(parts) == 2:
            # 파이프로 구분된 경우
            if ' | ' in parts[1]:
                paths = parts[1].split(' | ')
                for p in paths:
                    path = p.strip().split()[0]
                    if path.startswith('/'):
                        links.append(path)
                    else:
                        links.append('/' + path)
            else:
                path_part = parts[1].split()[0]
                links.append('/' + path_part)

print(f'발견된 링크: {len(links)}개\n')

# 각 링크 테스트
results = {'success': [], 'failed': []}

for link in links:
    try:
        resp = client.get(link)
        status = resp.status_code

        if status == 200:
            results['success'].append((link, status))
            print(f'[OK] {link:40} -> {status}')
        elif status in [401, 403]:
            results['success'].append((link, status, 'auth'))
            print(f'[AUTH] {link:40} -> {status} (auth - OK)')
        elif status == 404:
            results['failed'].append((link, status, 'not found'))
            print(f'[WARN] {link:40} -> {status}')
        else:
            results['failed'].append((link, status))
            print(f'[FAIL] {link:40} -> {status}')
    except Exception as e:
        results['failed'].append((link, 'exception', str(e)))
        print(f'[ERROR] {link:40} -> Exception: {e}')

print(f'\n=== 결과 요약 ===')
print(f'성공: {len(results["success"])}/{len(links)}')
print(f'실패: {len(results["failed"])}/{len(links)}')

if results['failed']:
    print(f'\n[WARN] 실패한 링크:')
    for item in results['failed']:
        print(f'  - {item}')
